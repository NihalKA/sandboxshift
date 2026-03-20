"""FargateRuntime — V1 cloud sandbox runtime for SandboxShift.

Runs agent tasks in the user's own AWS Fargate account. Never touches
any shared SandboxShift infrastructure.

Lifecycle:
  provision()  →  create ephemeral S3 bucket, upload workspace, store state
  execute()    →  launch ECS Fargate task, poll until STOPPED, fetch CloudWatch logs
  destroy()    →  stop task, delete S3 bucket + objects, clear state

AWS credentials are read exclusively from the environment (IAM role, AWS_PROFILE,
or AWS_ACCESS_KEY_ID env vars). Credentials are NEVER accepted as constructor
arguments. (Decision #22)

boto3.Session() is called once in __init__ and stored as self._session.
All API calls use self._session.client(service) — this makes tests able to
patch boto3.Session at the module level and intercept all client creation.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import boto3

from ...config import SandboxConfig
from ...observability.audit import AuditLogger
from .base import Runtime, TaskResult


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_SENSITIVE_SKIP_PATTERNS: tuple[str, ...] = (".env", ".pem", ".key")
_MAX_WORKSPACE_BYTES: int = 500 * 1024 * 1024   # 500 MB
_POLL_INTERVAL_SECONDS: float = 5.0
_S3_DELETE_BATCH_SIZE: int = 1000
_DEFAULT_IMAGE = "sandboxshift/runtime-python:3.11"   # audit-only in V1

_MARKER_IMAGES: dict[str, str] = {
    "requirements.txt": "sandboxshift/runtime-python:3.11",
    "package.json":     "sandboxshift/runtime-node:20",
    "go.mod":           "sandboxshift/runtime-go:1.22",
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _detect_image(workspace: Path) -> str:
    """Return the runtime image tag appropriate for this workspace.

    Checks for marker files (requirements.txt, package.json, go.mod).
    Multiple markers -> multi-runtime image. None -> default Python image.
    Used for audit record ONLY in V1 — does not affect which ECS image runs.
    """
    found: list[str] = [
        image
        for marker, image in _MARKER_IMAGES.items()
        if (workspace / marker).exists()
    ]
    if len(found) > 1:
        return "sandboxshift/runtime-multi"
    if len(found) == 1:
        return found[0]
    return _DEFAULT_IMAGE


def _sensitive_filename(name: str) -> bool:
    """Return True if the filename matches a sensitive pattern to skip."""
    return any(name.endswith(pat) for pat in _SENSITIVE_SKIP_PATTERNS)


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _FargateInstanceState:
    """Internal state stored between provision() and execute()/destroy()."""

    bucket_name: str
    region: str
    cluster_arn: str
    task_def_arn: str
    log_group: str
    config: SandboxConfig
    ecs_task_arn: str | None = None   # written by execute() after task starts


# ---------------------------------------------------------------------------
# FargateRuntime
# ---------------------------------------------------------------------------


class FargateRuntime(Runtime):
    """V1 cloud sandbox runtime using AWS Fargate.

    Runs agent tasks inside the caller's own AWS ECS Fargate cluster.
    Workspace files are staged to an ephemeral S3 bucket, the ECS task is
    launched with command overrides, CloudWatch logs are fetched after
    completion, and all AWS resources are cleaned up in destroy().

    Args:
        cluster_arn:         ARN of the ECS cluster to run tasks in.
        task_def_arn:        ARN of the ECS task definition (family:revision).
        subnet_ids:          VPC subnet IDs for the Fargate task network interface.
        security_group_ids:  Security group IDs for the Fargate task.
        region:              AWS region (e.g. 'us-east-1').
        log_group:           CloudWatch Logs log group name.
        audit_logger:        Optional AuditLogger. Defaults to the V1 stub.
    """

    def __init__(
        self,
        cluster_arn: str,
        task_def_arn: str,
        subnet_ids: list[str],
        security_group_ids: list[str],
        region: str,
        log_group: str,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        # Validate required string params
        for param_name, value in [
            ("cluster_arn", cluster_arn),
            ("task_def_arn", task_def_arn),
            ("region", region),
            ("log_group", log_group),
        ]:
            if not value:
                raise ValueError(f"{param_name!r} must not be empty")

        # Validate required list params
        for param_name, value in [
            ("subnet_ids", subnet_ids),
            ("security_group_ids", security_group_ids),
        ]:
            if not value:
                raise ValueError(f"{param_name!r} must not be empty")

        self._cluster_arn = cluster_arn
        self._task_def_arn = task_def_arn
        self._subnet_ids = subnet_ids
        self._security_group_ids = security_group_ids
        self._region = region
        self._log_group = log_group
        self._audit = audit_logger if audit_logger is not None else AuditLogger()
        self._instances: dict[str, _FargateInstanceState] = {}
        # CRITICAL: called here so tests can patch boto3.Session before constructing.
        self._session = boto3.Session()

    # -----------------------------------------------------------------------
    # Public async interface (Runtime ABC)
    # -----------------------------------------------------------------------

    async def provision(self, workspace: Path, config: SandboxConfig) -> str:
        """Provision a cloud sandbox.

        Creates an ephemeral S3 bucket, uploads workspace files (skipping
        sensitive filenames), and stores instance state.

        Args:
            workspace: Local directory to stage to S3. Must exist.
            config:    Sandbox configuration.

        Returns:
            Opaque instance_id string (format: "ss-{12 hex chars}").

        Raises:
            FileNotFoundError: If workspace does not exist.
            ValueError:        If workspace exceeds 500 MB.
            RuntimeError:      If S3 bucket creation or upload fails.
        """
        if not workspace.exists():
            raise FileNotFoundError(f"workspace does not exist: {workspace}")

        total = sum(f.stat().st_size for f in workspace.rglob("*") if f.is_file())
        if total > _MAX_WORKSPACE_BYTES:
            raise ValueError(f"workspace exceeds 500 MB limit: {total} bytes")

        instance_id = f"ss-{uuid.uuid4().hex[:12]}"
        bucket_name = f"sandboxshift-{instance_id}"

        try:
            await asyncio.to_thread(self._create_bucket, bucket_name)
        except Exception as e:
            raise RuntimeError(f"S3 bucket creation failed: {e}") from e

        files = [
            f
            for f in workspace.rglob("*")
            if f.is_file() and not _sensitive_filename(f.name)
        ]
        for f in files:
            try:
                await asyncio.to_thread(self._upload_file, bucket_name, workspace, f)
            except Exception as e:
                raise RuntimeError(f"S3 upload failed for {f}: {e}") from e

        image = _detect_image(workspace)  # audit-only

        self._instances[instance_id] = _FargateInstanceState(
            bucket_name=bucket_name,
            region=self._region,
            cluster_arn=self._cluster_arn,
            task_def_arn=self._task_def_arn,
            log_group=self._log_group,
            config=config,
        )

        self._audit.record({
            "event": "provision",
            "instance_id": instance_id,
            "bucket": bucket_name,
            "image_detected": image,
            "workspace": str(workspace),
            "network_allow": config.network_allow,
        })

        return instance_id

    async def execute(
        self,
        instance_id: str,
        task: str,
        config: SandboxConfig,  # noqa: ARG002
    ) -> TaskResult:
        """Launch an ECS Fargate task and wait for it to complete.

        Args:
            instance_id: Returned by provision().
            task:        Shell command string, run via /bin/sh -c.
            config:      Accepted for ABC compatibility — unused in V1.

        Returns:
            TaskResult with exit_code, stdout, stderr, and duration_seconds.

        Raises:
            RuntimeError:  If instance_id is unknown or ECS run_task fails.
            TimeoutError:  If the task exceeds config.timeout_seconds.
        """
        state = self._instances.get(instance_id)
        if state is None:
            raise RuntimeError(f"unknown instance_id: {instance_id}")

        start_time = time.monotonic()

        try:
            ecs_task_arn = await asyncio.to_thread(
                self._run_ecs_task, instance_id, task, state
            )
        except Exception as e:
            raise RuntimeError(f"ECS run_task failed: {e}") from e

        state.ecs_task_arn = ecs_task_arn

        await self._poll_until_stopped(instance_id, ecs_task_arn, state)

        exit_code = await asyncio.to_thread(self._get_exit_code, ecs_task_arn, state)
        stdout, stderr = await asyncio.to_thread(self._get_logs, instance_id, state)

        duration = time.monotonic() - start_time

        self._audit.record({
            "event": "execute",
            "instance_id": instance_id,
            "task": task,
            "exit_code": exit_code,
            "duration_seconds": round(duration, 3),
        })

        return TaskResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
        )

    async def destroy(self, instance_id: str) -> None:
        """Destroy the sandbox. Idempotent — never raises.

        Stops the ECS task (if running), deletes all S3 objects, deletes the
        S3 bucket, and removes the instance from internal state.

        The audit record is written in the finally block to guarantee it is
        always emitted regardless of cleanup errors (Security Layer 7).

        Args:
            instance_id: The ID returned by provision(). Unknown IDs are a no-op.
        """
        try:
            state = self._instances.get(instance_id)
            if state and state.ecs_task_arn:
                await asyncio.to_thread(self._stop_ecs_task, state)
            if state:
                await asyncio.to_thread(self._delete_bucket, state.bucket_name)
            self._instances.pop(instance_id, None)
        except Exception:  # noqa: BLE001 — destroy must never raise
            pass
        finally:
            # Security Layer 7: audit event must always fire, even if cleanup fails.
            self._audit.record({"event": "destroy", "instance_id": instance_id})

    # -----------------------------------------------------------------------
    # Sync helpers (called via asyncio.to_thread)
    # -----------------------------------------------------------------------

    def _create_bucket(self, bucket_name: str) -> None:
        """Create an S3 bucket with encryption and public access blocked."""
        s3 = self._session.client("s3", region_name=self._region)
        if self._region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": self._region},
            )
        s3.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
        )

    def _upload_file(
        self, bucket_name: str, workspace: Path, file_path: Path
    ) -> None:
        """Upload a single workspace file to S3 under the workspace/ prefix."""
        s3 = self._session.client("s3", region_name=self._region)
        key = "workspace/" + str(file_path.relative_to(workspace))
        body = file_path.read_bytes()
        s3.put_object(Bucket=bucket_name, Key=key, Body=body)

    def _run_ecs_task(
        self, instance_id: str, task: str, state: _FargateInstanceState
    ) -> str:
        """Launch an ECS Fargate task and return its task ARN."""
        ecs = self._session.client("ecs", region_name=self._region)
        response = ecs.run_task(
            cluster=state.cluster_arn,
            taskDefinition=state.task_def_arn,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": self._subnet_ids,
                    "securityGroups": self._security_group_ids,
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": "sandbox",
                        "command": ["/bin/sh", "-c", task],
                        "environment": [
                            {"name": "SS_BUCKET",  "value": state.bucket_name},
                            {"name": "SS_PREFIX",  "value": "workspace/"},
                            {"name": "SS_TASK_ID", "value": instance_id},
                        ],
                    }
                ]
            },
        )
        tasks = response.get("tasks", [])
        if not tasks:
            failures = response.get("failures", [])
            raise RuntimeError(f"ECS run_task returned no tasks: {failures}")
        return str(tasks[0]["taskArn"])

    async def _poll_until_stopped(
        self,
        instance_id: str,  # noqa: ARG002
        task_arn: str,
        state: _FargateInstanceState,
    ) -> None:
        """Poll ECS until the task reaches STOPPED status or timeout."""
        deadline = time.monotonic() + state.config.timeout_seconds
        ecs = self._session.client("ecs", region_name=self._region)
        start = time.monotonic()
        print("[sandboxshift] Fargate task started — waiting for completion...", flush=True)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Fargate task timed out after {state.config.timeout_seconds}s:"
                    f" {task_arn}"
                )
            response = await asyncio.to_thread(
                ecs.describe_tasks, cluster=state.cluster_arn, tasks=[task_arn]
            )
            task_desc = response["tasks"][0]
            status = task_desc["lastStatus"]
            elapsed = int(time.monotonic() - start)
            print(f"\r[sandboxshift] {status} ({elapsed}s)...", end="", flush=True)
            if status == "STOPPED":
                print()  # newline after the final \r line
                return
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def _get_exit_code(self, task_arn: str, state: _FargateInstanceState) -> int:
        """Retrieve the container exit code from ECS. Returns -1 if unavailable."""
        ecs = self._session.client("ecs", region_name=self._region)
        response = ecs.describe_tasks(cluster=state.cluster_arn, tasks=[task_arn])
        containers = response["tasks"][0].get("containers", [])
        if not containers:
            self._audit.record({"event": "exit_code_missing", "task_arn": task_arn})
            return -1
        exit_code = containers[0].get("exitCode")
        if exit_code is None:
            self._audit.record({"event": "exit_code_missing", "task_arn": task_arn})
            return -1
        return int(exit_code)

    def _get_logs(
        self, instance_id: str, state: _FargateInstanceState
    ) -> tuple[str, str]:
        """Fetch CloudWatch logs for the task. Never raises. Returns (stdout, stderr).

        ECS CloudWatch stream name format:
          {awslogs-stream-prefix}/{container_name}/{task_short_id}
        where task_short_id is the last '/'-separated segment of the task ARN.

        stderr is always empty string in V1.
        """
        try:
            logs = self._session.client("logs", region_name=self._region)
            # Derive the stream name from the ECS task ARN.
            # ECS creates streams as: sandboxshift/sandbox/{task_short_id}
            # task ARN format: arn:aws:ecs:region:account:task/cluster/TASK_SHORT_ID
            task_short_id = (state.ecs_task_arn or "").split("/")[-1]
            stream_name = f"sandboxshift/sandbox/{task_short_id}"
            response = logs.get_log_events(
                logGroupName=state.log_group,
                logStreamName=stream_name,
                startFromHead=True,
            )
            events = response.get("events", [])
            combined = "\n".join(e["message"] for e in events)
            return combined, ""
        except Exception as exc:  # noqa: BLE001
            self._audit.record({
                "event": "cloudwatch_logs_unavailable",
                "instance_id": instance_id,
                "error": str(exc),
            })
            return "", ""

    def _stop_ecs_task(self, state: _FargateInstanceState) -> None:
        """Stop the ECS task. Ignores errors (task may already be stopped)."""
        ecs = self._session.client("ecs", region_name=self._region)
        try:
            ecs.stop_task(cluster=state.cluster_arn, task=state.ecs_task_arn)
        except Exception:  # noqa: BLE001
            pass  # task may already be stopped

    def _delete_bucket(self, bucket_name: str) -> None:
        """Delete all objects in the bucket then delete the bucket itself."""
        s3 = self._session.client("s3", region_name=self._region)
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            objects = page.get("Contents", [])
            if objects:
                s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
                )
        s3.delete_bucket(Bucket=bucket_name)
