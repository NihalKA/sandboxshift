"""FargateRuntime — V1 cloud sandbox runtime for SandboxShift.

Runs agent tasks in the user's own AWS Fargate account. Never touches
any shared SandboxShift infrastructure.

Lifecycle (batch mode — no ports configured):
  provision()  →  upload workspace to the persistent S3 bucket under a
                  unique prefix (workspace/{instance_id}/), register a
                  fresh ECS task definition for this run, store state
  execute()    →  launch ECS Fargate task; the injected command first downloads
                  the workspace from S3 into /workspace, installs dependencies
                  (pip/npm if manifest present), then runs the user task;
                  poll until STOPPED; fetch CloudWatch logs; return TaskResult
  destroy()    →  stop task, deregister the task definition, delete the workspace
                  prefix from S3, clear state

Lifecycle (server mode — ports: configured in sandboxshift.yaml):
  provision()  →  same as batch
  execute()    →  launch ECS task with server SG appended; poll until RUNNING;
                  resolve ENI public IP via EC2 API; print URL to terminal;
                  tail CloudWatch logs live (blocks until Ctrl+C — server stays
                  running); save instance info to ~/.sandboxshift/servers.json;
                  return TaskResult immediately — task keeps running in Fargate
  destroy()    →  deregister the task definition, delete S3 prefix (workspace
                  already downloaded), clear state;
                  does NOT stop the ECS task (user calls `sandboxshift stop <id>`)

Dynamic task definition (Decision #64):
  FargateRuntime registers a new ECS task definition in provision() using
  ecs:RegisterTaskDefinition, with the exact CPU/memory requested by the user.
  The registered ARN is stored in state and used by execute(). destroy() always
  calls ecs:DeregisterTaskDefinition to clean up. This means any valid Fargate
  CPU/memory combination works without any Terraform changes.

User env vars (config.env_vars) are appended to containerOverrides environment
in _run_ecs_task (Decision #65). Values are never written to the audit log —
only keys are recorded.

AWS credentials are read exclusively from the environment (IAM role, AWS_PROFILE,
or AWS_ACCESS_KEY_ID env vars). Credentials are NEVER accepted as constructor
arguments. (Decision #22)

boto3.Session() is called once in __init__ and stored as self._session.
All API calls use self._session.client(service) — this makes tests able to
patch boto3.Session at the module level and intercept all client creation.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import boto3

from ...config import SandboxConfig
from ...observability.audit import AuditLogger
from .base import Runtime, TaskResult


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_SENSITIVE_SKIP_PATTERNS: tuple[str, ...] = (".env", ".pem", ".key")

# Dependency directories that are NEVER uploaded to S3. (Decision #58)
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".pytest_cache",
    ".tox",
    ".eggs",
    "dist",
    "build",
    ".next",
    ".nuxt",
})

_MAX_WORKSPACE_BYTES: int = 500 * 1024 * 1024   # 500 MB
_POLL_INTERVAL_SECONDS: float = 5.0
_S3_DELETE_BATCH_SIZE: int = 1000
_DEFAULT_IMAGE = "sandboxshift/runtime-python:3.11"   # audit-only in V1
_SERVER_START_TIMEOUT_SECONDS: int = 300             # 5 min to reach RUNNING
_LOG_STREAM_WAIT_ATTEMPTS: int = 7                   # 7 x 5s = 35s max wait
_LOG_TAIL_POLL_SECONDS: float = 2.0                  # CloudWatch poll interval

_MARKER_IMAGES: dict[str, str] = {
    "requirements.txt": "sandboxshift/runtime-python:3.11",
    "package.json":     "sandboxshift/runtime-node:20",
    "go.mod":           "sandboxshift/runtime-go:1.22",
}

# ANSI colours
_C_BLUE   = "\033[0;34m"
_C_GREEN  = "\033[0;32m"
_C_YELLOW = "\033[1;33m"
_C_BOLD   = "\033[1m"
_C_RESET  = "\033[0m"

_S3_DOWNLOAD_BOOTSTRAP = (
    "python3 -c \""
    "import boto3, os, pathlib; "
    "s3=boto3.client('s3',region_name=os.environ['SS_REGION']); "
    "bucket=os.environ['SS_BUCKET']; "
    "prefix=os.environ['SS_PREFIX']; "
    "[("
    "  pathlib.Path('/workspace'/pathlib.Path(o['Key'][len(prefix):]).parent).mkdir(parents=True,exist_ok=True),"
    "  s3.download_file(bucket,o['Key'],'/workspace/'+o['Key'][len(prefix):])"
    ") for page in boto3.client('s3',region_name=os.environ['SS_REGION'])"
    ".get_paginator('list_objects_v2').paginate(Bucket=bucket,Prefix=prefix)"
    " for o in page.get('Contents',[])"
    "]\""
)

_S3_DEPS_BOOTSTRAP = (
    "cd /workspace"
    " && ([ -f requirements.txt ] && pip install --quiet -r requirements.txt 2>&1 || true)"
    " && ([ -f package.json ] && npm install 2>&1 || true)"
)

_SERVERS_FILE: Path = Path.home() / ".sandboxshift" / "servers.json"


def _step(msg: str) -> None:
    print(f"{_C_BLUE}[sandboxshift]{_C_RESET} {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"{_C_GREEN}[sandboxshift]{_C_RESET} \u2713 {msg}", flush=True)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _detect_image(workspace: Path) -> str:
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
    return any(name.endswith(pat) for pat in _SENSITIVE_SKIP_PATTERNS)


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _FargateInstanceState:
    bucket_name: str
    s3_prefix: str
    region: str
    cluster_arn: str
    registered_task_def_arn: str
    log_group: str
    config: SandboxConfig
    ecs_task_arn: str | None = None
    is_server: bool = False


# ---------------------------------------------------------------------------
# FargateRuntime
# ---------------------------------------------------------------------------


class FargateRuntime(Runtime):
    """V1 cloud sandbox runtime using AWS Fargate."""

    def __init__(
        self,
        cluster_arn: str,
        execution_role_arn: str,
        task_role_arn: str,
        subnet_ids: list[str],
        security_group_ids: list[str],
        region: str,
        log_group: str,
        workspace_bucket: str,
        task_family: str = "sandboxshift-sandbox",
        ecr_image: str = "",
        server_security_group_id: str | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        for param_name, value in [
            ("cluster_arn", cluster_arn),
            ("execution_role_arn", execution_role_arn),
            ("task_role_arn", task_role_arn),
            ("region", region),
            ("log_group", log_group),
            ("workspace_bucket", workspace_bucket),
        ]:
            if not value:
                raise ValueError(f"{param_name!r} must not be empty")
        for param_name, value in [
            ("subnet_ids", subnet_ids),
            ("security_group_ids", security_group_ids),
        ]:
            if not value:
                raise ValueError(f"{param_name!r} must not be empty")

        self._cluster_arn = cluster_arn
        self._execution_role_arn = execution_role_arn
        self._task_role_arn = task_role_arn
        self._subnet_ids = subnet_ids
        self._security_group_ids = security_group_ids
        self._region = region
        self._log_group = log_group
        self._workspace_bucket = workspace_bucket
        self._task_family = task_family or "sandboxshift-sandbox"
        self._ecr_image = ecr_image.strip() if ecr_image else "sandboxshift/runtime-multi:latest"
        self._server_security_group_id = server_security_group_id
        self._audit = audit_logger if audit_logger is not None else AuditLogger()
        self._instances: dict[str, _FargateInstanceState] = {}
        self._session = boto3.Session()

    # -----------------------------------------------------------------------
    # Public async interface (Runtime ABC)
    # -----------------------------------------------------------------------

    async def provision(self, workspace: Path, config: SandboxConfig) -> str:
        if not workspace.exists():
            raise FileNotFoundError(f"workspace does not exist: {workspace}")

        instance_id = f"ss-{uuid.uuid4().hex[:12]}"
        s3_prefix = f"workspace/{instance_id}/"

        files = [
            f
            for f in workspace.rglob("*")
            if f.is_file()
            and (f.name in config.upload_allow_files or not _sensitive_filename(f.name))
            and ".git" not in f.relative_to(workspace).parts
            and not any(
                part in _SKIP_DIRS
                for part in f.relative_to(workspace).parts
            )
        ]

        upload_allowed_sensitive = [
            f.name for f in files
            if f.name in config.upload_allow_files and _sensitive_filename(f.name)
        ]

        total_bytes = sum(f.stat().st_size for f in files)
        if total_bytes > _MAX_WORKSPACE_BYTES:
            raise ValueError(f"workspace exceeds 500 MB limit: {total_bytes} bytes")

        total = len(files)
        _step(
            f"Uploading {total} file(s) to S3 "
            f"(node_modules / dep dirs skipped) ..."
        )
        for i, f in enumerate(files, 1):
            try:
                await asyncio.to_thread(
                    self._upload_file, self._workspace_bucket, s3_prefix, workspace, f
                )
            except Exception as e:
                print()
                raise RuntimeError(f"S3 upload failed for {f}: {e}") from e
            if i % 5 == 0 or i == total:
                print(
                    f"\r{_C_BLUE}[sandboxshift]{_C_RESET}"
                    f"  [{i}/{total}] uploading ...",
                    end="",
                    flush=True,
                )
        if total > 0:
            print()
        _ok("Workspace uploaded")

        _step("Registering ECS task definition ...")
        try:
            registered_task_def_arn = await asyncio.to_thread(
                self._register_task_definition, instance_id, config
            )
        except Exception as e:
            raise RuntimeError(f"Failed to register task definition: {e}") from e
        _ok(f"Task definition registered: {registered_task_def_arn.split('/')[-1]}")

        image = _detect_image(workspace)
        is_server = bool(config.ports)

        self._instances[instance_id] = _FargateInstanceState(
            bucket_name=self._workspace_bucket,
            s3_prefix=s3_prefix,
            region=self._region,
            cluster_arn=self._cluster_arn,
            registered_task_def_arn=registered_task_def_arn,
            log_group=self._log_group,
            config=config,
            is_server=is_server,
        )

        self._audit.record({
            "event": "provision",
            "instance_id": instance_id,
            "bucket": self._workspace_bucket,
            "s3_prefix": s3_prefix,
            "image_detected": image,
            "task_def_arn": registered_task_def_arn,
            "workspace": str(workspace),
            "network_allow": config.network_allow,
            "is_server": is_server,
            "files_uploaded": total,
            "bytes_uploaded": total_bytes,
            "upload_allowed_sensitive": upload_allowed_sensitive,
            "env_var_keys": list(config.env_vars.keys()),
        })

        return instance_id

    async def execute(
        self,
        instance_id: str,
        task: str,
        config: SandboxConfig,  # noqa: ARG002
    ) -> TaskResult:
        state = self._instances.get(instance_id)
        if state is None:
            raise RuntimeError(f"unknown instance_id: {instance_id}")

        if state.is_server:
            return await self._execute_server(instance_id, task, state)
        else:
            return await self._execute_batch(instance_id, task, state)

    async def destroy(self, instance_id: str) -> None:
        _step("Cleaning up S3 workspace ...")
        try:
            state = self._instances.get(instance_id)
            if state:
                if state.ecs_task_arn and not state.is_server:
                    await asyncio.to_thread(self._stop_ecs_task, state)
                if state.registered_task_def_arn:
                    await asyncio.to_thread(
                        self._deregister_task_definition,
                        state.registered_task_def_arn,
                    )
                await asyncio.to_thread(
                    self._delete_s3_prefix, state.bucket_name, state.s3_prefix
                )
            self._instances.pop(instance_id, None)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._audit.record({"event": "destroy", "instance_id": instance_id})
        _ok("S3 workspace cleaned up")

    # -----------------------------------------------------------------------
    # Execute paths
    # -----------------------------------------------------------------------

    async def _execute_batch(
        self, instance_id: str, task: str, state: _FargateInstanceState
    ) -> TaskResult:
        start_time = time.monotonic()

        _step("Submitting ECS Fargate task ...")
        try:
            ecs_task_arn = await asyncio.to_thread(
                self._run_ecs_task, instance_id, task, state
            )
        except Exception as e:
            raise RuntimeError(f"ECS run_task failed: {e}") from e

        task_short_id = ecs_task_arn.split("/")[-1]
        _ok(f"Task submitted: {task_short_id}")
        state.ecs_task_arn = ecs_task_arn

        await self._poll_until_stopped(instance_id, ecs_task_arn, state)

        _step("Fetching CloudWatch logs ...")
        exit_code = await asyncio.to_thread(self._get_exit_code, ecs_task_arn, state)
        stdout, stderr = await asyncio.to_thread(self._get_logs, instance_id, state)
        _ok("Logs retrieved")

        duration = time.monotonic() - start_time

        self._audit.record({
            "event": "execute",
            "instance_id": instance_id,
            "task": task,
            "exit_code": exit_code,
            "duration_seconds": round(duration, 3),
            "env_var_keys": list(state.config.env_vars.keys()),
        })

        return TaskResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
        )

    async def _execute_server(
        self, instance_id: str, task: str, state: _FargateInstanceState
    ) -> TaskResult:
        start_time = time.monotonic()

        _step("Submitting ECS Fargate server task ...")
        try:
            ecs_task_arn = await asyncio.to_thread(
                self._run_ecs_task, instance_id, task, state
            )
        except Exception as e:
            raise RuntimeError(f"ECS run_task failed: {e}") from e

        task_short_id = ecs_task_arn.split("/")[-1]
        _ok(f"Task submitted: {task_short_id}")
        state.ecs_task_arn = ecs_task_arn

        await self._poll_until_running(ecs_task_arn, state)

        _step("Resolving public IP ...")
        public_ip = await asyncio.to_thread(
            self._get_task_public_ip, ecs_task_arn, state
        )

        duration = time.monotonic() - start_time

        if public_ip:
            urls = [f"http://{public_ip}:{c}" for _, c in state.config.ports]
            url_str = "\n".join(urls)
            stdout_result = url_str
        else:
            urls = []
            url_str = f"Server running (ECS task: {task_short_id})"
            stdout_result = url_str

        print(flush=True)
        print(
            f"{_C_GREEN}[sandboxshift]{_C_RESET} {_C_BOLD}Server is RUNNING{_C_RESET}",
            flush=True,
        )
        if urls:
            for url in urls:
                print(f"  {_C_BOLD}{_C_GREEN}{url}{_C_RESET}", flush=True)
        else:
            print(f"  ECS task: {task_short_id}", flush=True)
            _step("Public IP not yet available — check ECS console")
        print(flush=True)
        print(f"  To stop:  sandboxshift stop {instance_id}", flush=True)
        print(flush=True)

        self._save_server_info(instance_id, ecs_task_arn, state, public_ip)

        self._audit.record({
            "event": "server_running",
            "instance_id": instance_id,
            "task": task,
            "public_ip": public_ip,
            "ports": [[h, c] for h, c in state.config.ports],
            "ecs_task_arn": ecs_task_arn,
            "duration_seconds": round(duration, 3),
            "env_var_keys": list(state.config.env_vars.keys()),
        })

        await self._tail_logs(ecs_task_arn, state)

        return TaskResult(
            exit_code=0,
            stdout=stdout_result,
            stderr="",
            duration_seconds=duration,
        )

    # -----------------------------------------------------------------------
    # Sync helpers (called via asyncio.to_thread)
    # -----------------------------------------------------------------------

    def _upload_file(
        self, bucket_name: str, s3_prefix: str, workspace: Path, file_path: Path
    ) -> None:
        s3 = self._session.client("s3", region_name=self._region)
        key = s3_prefix + str(file_path.relative_to(workspace))
        body = file_path.read_bytes()
        s3.put_object(Bucket=bucket_name, Key=key, Body=body)

    def _register_task_definition(
        self, instance_id: str, config: SandboxConfig
    ) -> str:
        """Register a fresh ECS task definition for this run. Returns the ARN."""
        cpu_units = str(int(config.cpu_limit * 1024))
        memory_mib = str(config.memory_limit_mb)
        family = f"{self._task_family}-{instance_id}"

        ecs = self._session.client("ecs", region_name=self._region)
        response = ecs.register_task_definition(
            family=family,
            requiresCompatibilities=["FARGATE"],
            networkMode="awsvpc",
            cpu=cpu_units,
            memory=memory_mib,
            executionRoleArn=self._execution_role_arn,
            taskRoleArn=self._task_role_arn,
            containerDefinitions=[{
                "name": "sandbox",
                "image": self._ecr_image,
                "essential": True,
                "entryPoint": ["/bin/sh"],
                "command": ["-c", "echo sandboxshift ready"],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": self._log_group,
                        "awslogs-region": self._region,
                        "awslogs-stream-prefix": "sandboxshift",
                    }
                },
                "environment": [],
            }]
        )
        return str(response["taskDefinition"]["taskDefinitionArn"])

    def _deregister_task_definition(self, task_def_arn: str) -> None:
        try:
            ecs = self._session.client("ecs", region_name=self._region)
            ecs.deregister_task_definition(taskDefinition=task_def_arn)
        except Exception:  # noqa: BLE001
            pass

    def _run_ecs_task(
        self, instance_id: str, task: str, state: _FargateInstanceState
    ) -> str:
        """Launch an ECS Fargate task and return its task ARN."""
        user_task = (
            f"{state.config.setup_command} && {task}"
            if state.config.setup_command
            else task
        )
        _deps = f" && {_S3_DEPS_BOOTSTRAP}" if not state.config.setup_command else ""
        full_command = f"{_S3_DOWNLOAD_BOOTSTRAP}{_deps} && {user_task}"

        security_groups = list(self._security_group_ids)
        if state.is_server and self._server_security_group_id:
            security_groups.append(self._server_security_group_id)

        # Build container environment — always include sandbox bootstrap vars.
        env_overrides = [
            {"name": "SS_BUCKET",  "value": state.bucket_name},
            {"name": "SS_PREFIX",  "value": state.s3_prefix},
            {"name": "SS_REGION",  "value": state.region},
            {"name": "SS_TASK_ID", "value": instance_id},
        ]
        if state.config.ports:
            env_overrides.append(
                {"name": "PORT", "value": str(state.config.ports[0][1])}
            )
        # Append user-defined env vars (Decision #65).
        # Values are never written to the audit log — only keys are recorded.
        for k, v in state.config.env_vars.items():
            env_overrides.append({"name": k, "value": v})

        ecs = self._session.client("ecs", region_name=self._region)
        response = ecs.run_task(
            cluster=state.cluster_arn,
            taskDefinition=state.registered_task_def_arn,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": self._subnet_ids,
                    "securityGroups": security_groups,
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": "sandbox",
                        "command": ["-c", full_command],
                        "environment": env_overrides,
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
        deadline = time.monotonic() + state.config.timeout_seconds
        ecs = self._session.client("ecs", region_name=self._region)
        start = time.monotonic()
        last_status = ""

        while True:
            if time.monotonic() >= deadline:
                print()
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

            if status != last_status:
                if last_status:
                    print()
                print(
                    f"{_C_BLUE}[sandboxshift]{_C_RESET}"
                    f" {_C_YELLOW}{status}{_C_RESET} ...",
                    flush=True,
                )
                last_status = status
            else:
                print(
                    f"\r{_C_BLUE}[sandboxshift]{_C_RESET}"
                    f" {_C_YELLOW}{status}{_C_RESET} ({elapsed}s)...",
                    end="",
                    flush=True,
                )

            if status == "STOPPED":
                print()
                return

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _poll_until_running(
        self,
        task_arn: str,
        state: _FargateInstanceState,
    ) -> None:
        deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
        ecs = self._session.client("ecs", region_name=self._region)
        start = time.monotonic()
        last_status = ""

        while True:
            if time.monotonic() >= deadline:
                print()
                raise TimeoutError(
                    f"Fargate server task did not reach RUNNING within "
                    f"{_SERVER_START_TIMEOUT_SECONDS}s: {task_arn}"
                )

            response = await asyncio.to_thread(
                ecs.describe_tasks, cluster=state.cluster_arn, tasks=[task_arn]
            )
            task_desc = response["tasks"][0]
            status = task_desc["lastStatus"]
            elapsed = int(time.monotonic() - start)

            if status != last_status:
                if last_status:
                    print()
                print(
                    f"{_C_BLUE}[sandboxshift]{_C_RESET}"
                    f" {_C_YELLOW}{status}{_C_RESET} ...",
                    flush=True,
                )
                last_status = status
            else:
                print(
                    f"\r{_C_BLUE}[sandboxshift]{_C_RESET}"
                    f" {_C_YELLOW}{status}{_C_RESET} ({elapsed}s)...",
                    end="",
                    flush=True,
                )

            if status == "RUNNING":
                print()
                return

            if status == "STOPPED":
                print()
                raise RuntimeError(
                    f"Fargate server task STOPPED before reaching RUNNING: {task_arn}"
                )

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _tail_logs(
        self,
        ecs_task_arn: str,
        state: _FargateInstanceState,
    ) -> None:
        task_short_id = ecs_task_arn.split("/")[-1]
        stream_name = f"sandboxshift/sandbox/{task_short_id}"
        logs_client = self._session.client("logs", region_name=self._region)

        print(
            f"{_C_BLUE}[sandboxshift]{_C_RESET} "
            f"Streaming logs (Ctrl+C to stop tailing \u2014 server stays running):\n",
            flush=True,
        )

        stream_ready = False
        for attempt in range(_LOG_STREAM_WAIT_ATTEMPTS):
            try:
                resp = await asyncio.to_thread(
                    logs_client.describe_log_streams,
                    logGroupName=state.log_group,
                    logStreamNamePrefix=stream_name,
                )
                if resp.get("logStreams"):
                    stream_ready = True
                    break
            except Exception:  # noqa: BLE001
                pass
            if attempt < _LOG_STREAM_WAIT_ATTEMPTS - 1:
                await asyncio.sleep(5)

        if not stream_ready:
            _step(
                "Log stream not yet available \u2014 "
                f"check CloudWatch console: {state.log_group}"
            )
            return

        next_token: str | None = None
        try:
            while True:
                kwargs: dict = {
                    "logGroupName": state.log_group,
                    "logStreamName": stream_name,
                    "startFromHead": True,
                }
                if next_token:
                    kwargs["nextToken"] = next_token

                resp = await asyncio.to_thread(logs_client.get_log_events, **kwargs)
                events = resp.get("events", [])
                for event in events:
                    print(event["message"], flush=True)

                new_token = resp.get("nextForwardToken")
                if new_token != next_token:
                    next_token = new_token

                await asyncio.sleep(_LOG_TAIL_POLL_SECONDS)

        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

        print(flush=True)
        print(
            f"{_C_BLUE}[sandboxshift]{_C_RESET} "
            "Log tail stopped. Server is still running.",
            flush=True,
        )

    def _get_task_public_ip(
        self, task_arn: str, state: _FargateInstanceState
    ) -> str | None:
        try:
            ecs = self._session.client("ecs", region_name=self._region)
            ec2 = self._session.client("ec2", region_name=self._region)

            response = ecs.describe_tasks(
                cluster=state.cluster_arn, tasks=[task_arn]
            )
            task = response["tasks"][0]

            for attachment in task.get("attachments", []):
                if attachment.get("type") != "ElasticNetworkInterface":
                    continue
                for detail in attachment.get("details", []):
                    if detail.get("name") == "networkInterfaceId":
                        eni_id = detail["value"]
                        eni_resp = ec2.describe_network_interfaces(
                            NetworkInterfaceIds=[eni_id]
                        )
                        assoc = eni_resp["NetworkInterfaces"][0].get("Association", {})
                        return assoc.get("PublicIp") or None
        except Exception:  # noqa: BLE001
            pass
        return None

    def _save_server_info(
        self,
        instance_id: str,
        ecs_task_arn: str,
        state: _FargateInstanceState,
        public_ip: str | None,
    ) -> None:
        try:
            servers: dict = {}
            if _SERVERS_FILE.exists():
                try:
                    servers = json.loads(_SERVERS_FILE.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    servers = {}

            servers[instance_id] = {
                "ecs_task_arn": ecs_task_arn,
                "cluster_arn": state.cluster_arn,
                "region": state.region,
                "public_ip": public_ip,
                "ports": [[h, c] for h, c in state.config.ports],
                "s3_bucket": state.bucket_name,
                "s3_prefix": state.s3_prefix,
            }

            _SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _SERVERS_FILE.write_text(
                json.dumps(servers, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    def _get_exit_code(self, task_arn: str, state: _FargateInstanceState) -> int:
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
        try:
            logs = self._session.client("logs", region_name=self._region)
            task_short_id = (state.ecs_task_arn or "").split("/")[-1]
            stream_name = f"sandboxshift/sandbox/{task_short_id}"
            response = logs.get_log_events(
                logGroupName=state.log_group,
                logStreamName=stream_name,
                startFromHead=True,
            )
            lines = [e["message"] for e in response.get("events", [])]
            return "\n".join(lines), ""
        except Exception:  # noqa: BLE001
            return "", ""

    def _stop_ecs_task(self, state: _FargateInstanceState) -> None:
        if not state.ecs_task_arn:
            return
        ecs = self._session.client("ecs", region_name=self._region)
        try:
            ecs.stop_task(
                cluster=state.cluster_arn,
                task=state.ecs_task_arn,
                reason="SandboxShift destroy()",
            )
        except Exception:  # noqa: BLE001
            pass

    def _delete_s3_prefix(self, bucket_name: str, s3_prefix: str) -> None:
        s3 = self._session.client("s3", region_name=self._region)
        paginator = s3.get_paginator("list_objects_v2")
        objects_to_delete: list[dict] = []

        for page in paginator.paginate(Bucket=bucket_name, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                objects_to_delete.append({"Key": obj["Key"]})
                if len(objects_to_delete) >= _S3_DELETE_BATCH_SIZE:
                    s3.delete_objects(
                        Bucket=bucket_name,
                        Delete={"Objects": objects_to_delete, "Quiet": True},
                    )
                    objects_to_delete = []

        if objects_to_delete:
            s3.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": objects_to_delete, "Quiet": True},
            )
