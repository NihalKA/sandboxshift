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
# They are large, platform-specific, and are reinstalled fresh inside the ECS
# task by _S3_DEPS_BOOTSTRAP (npm install / pip install). Skipping them makes
# uploads orders of magnitude faster for typical Node/Python workspaces.
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules",   # npm packages — npm install runs in container
    "__pycache__",    # Python bytecode — not portable across platforms
    ".venv",          # Python virtualenv — pip install runs in container
    "venv",           # alternate virtualenv name
    "env",            # another common virtualenv name
    ".pytest_cache",  # pytest artefacts
    ".tox",           # tox environments
    ".eggs",          # Python egg-info
    "dist",           # build output — may be large and stale
    "build",          # build output
    ".next",          # Next.js build cache
    ".nuxt",          # Nuxt.js build cache
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

# ANSI colours — used for verbose step output
_C_BLUE   = "\033[0;34m"
_C_GREEN  = "\033[0;32m"
_C_YELLOW = "\033[1;33m"
_C_BOLD   = "\033[1m"
_C_RESET  = "\033[0m"

# Python one-liner injected at the start of every ECS task command.
# Downloads the workspace from S3 into /workspace before the user task runs.
# Uses boto3 (installed in all SandboxShift runtime images) so no aws CLI needed.
# SS_BUCKET, SS_PREFIX, SS_REGION are injected via containerOverrides environment.
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

# Shell fragment injected after workspace download — installs Python and Node
# dependencies if manifest files are present. cd /workspace first so relative
# imports work. Both install commands redirect stderr to stdout so all output
# is visible in CloudWatch Logs. Failures are non-fatal (|| true) so a missing
# npm/pip doesn't abort a task that doesn't need it.
_S3_DEPS_BOOTSTRAP = (
    "cd /workspace"
    " && ([ -f requirements.txt ] && pip install --quiet -r requirements.txt 2>&1 || true)"
    " && ([ -f package.json ] && npm install 2>&1 || true)"
)

_SERVERS_FILE: Path = Path.home() / ".sandboxshift" / "servers.json"


def _step(msg: str) -> None:
    """Print a verbose progress line with a blue [sandboxshift] prefix."""
    print(f"{_C_BLUE}[sandboxshift]{_C_RESET} {msg}", flush=True)


def _ok(msg: str) -> None:
    """Print a green completion line."""
    print(f"{_C_GREEN}[sandboxshift]{_C_RESET} \u2713 {msg}", flush=True)


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
    s3_prefix: str      # e.g. "workspace/ss-abc123def456/"
    region: str
    cluster_arn: str
    registered_task_def_arn: str   # registered dynamically in provision() (Decision #64)
    log_group: str
    config: SandboxConfig
    ecs_task_arn: str | None = None   # written by execute() after task starts
    is_server: bool = False           # True when config.ports is non-empty


# ---------------------------------------------------------------------------
# FargateRuntime
# ---------------------------------------------------------------------------


class FargateRuntime(Runtime):
    """V1 cloud sandbox runtime using AWS Fargate.

    Runs agent tasks inside the caller's own AWS ECS Fargate cluster.
    Workspace files are staged to a persistent S3 bucket under a unique
    per-run prefix, the ECS task is launched with a bootstrap command that
    downloads the workspace, installs dependencies, then runs the user task.
    CloudWatch logs are fetched after completion (batch mode), and the S3
    prefix is cleaned up in destroy().

    Dynamic task definitions (Decision #64):
    FargateRuntime registers a fresh ECS task definition in provision() with
    the exact CPU/memory/image requested by the user. This allows any valid
    Fargate CPU/memory combination without Terraform changes. The task def
    is deregistered in destroy() — automatically for batch tasks, and when
    `sandboxshift stop <id>` is called for server tasks.

    Server mode (ports: configured):
    When config.ports is non-empty, execute() switches to server mode:
    the task is launched with server_security_group_id appended (ALL TCP
    inbound), execution waits until RUNNING then tails CloudWatch logs live.
    The tail blocks until Ctrl+C — the server keeps running. Use
    `sandboxshift stop <instance_id>` to stop the Fargate task.

    Args:
        cluster_arn:              ARN of the ECS cluster to run tasks in.
        execution_role_arn:       IAM execution role ARN — allows Fargate to pull
                                  images and write CloudWatch logs.
        task_role_arn:            IAM task role ARN — grants the container S3
                                  workspace access.
        subnet_ids:               VPC subnet IDs for the Fargate task network interface.
        security_group_ids:       Security group IDs for the Fargate task (batch).
        region:                   AWS region (e.g. 'us-east-1').
        log_group:                CloudWatch Logs log group name.
        workspace_bucket:         Name of the persistent S3 bucket for workspace staging.
        task_family:              ECS task definition family prefix (e.g. 'sandboxshift-sandbox').
                                  Each run registers family 'task_family-instance_id'.
        ecr_image:                Full image URI to run (e.g. ECR URI or Docker Hub name).
                                  If empty, defaults to 'sandboxshift/runtime-multi:latest'.
        server_security_group_id: Optional SG with ALL TCP inbound — attached only when
                                  ports are configured (server mode). If not set, server
                                  mode tasks run with the standard batch SG only (no
                                  inbound access).
        audit_logger:             Optional AuditLogger. Defaults to the V1 stub.
    """

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
        # Validate required string params
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

        # Validate required list params
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
        # Resolve container image: use ecr_image if provided, else Docker Hub default.
        self._ecr_image = ecr_image.strip() if ecr_image else "sandboxshift/runtime-multi:latest"
        self._server_security_group_id = server_security_group_id
        self._audit = audit_logger if audit_logger is not None else AuditLogger()
        self._instances: dict[str, _FargateInstanceState] = {}
        # CRITICAL: called here so tests can patch boto3.Session before constructing.
        self._session = boto3.Session()

    # -----------------------------------------------------------------------
    # Public async interface (Runtime ABC)
    # -----------------------------------------------------------------------

    async def provision(self, workspace: Path, config: SandboxConfig) -> str:
        """Provision a cloud sandbox.

        Uploads workspace files to the persistent S3 bucket under a unique
        per-run prefix (workspace/{instance_id}/), then registers a fresh
        ECS task definition for this run (Decision #64). Skips:
          - sensitive filenames (.env, .pem, .key)
          - .git directory
          - local-only dependency directories (node_modules, __pycache__, .venv,
            etc. — defined in _SKIP_DIRS; reinstalled fresh in ECS by
            _S3_DEPS_BOOTSTRAP, Decision #58)

        Prints [n/total] inline progress every 5 files so long uploads are
        never silent.

        Args:
            workspace: Local directory to stage to S3. Must exist.
            config:    Sandbox configuration.

        Returns:
            Opaque instance_id string (format: "ss-{12 hex chars}").

        Raises:
            FileNotFoundError: If workspace does not exist.
            ValueError:        If workspace (after filtering) exceeds 500 MB.
            RuntimeError:      If S3 upload or task def registration fails.
        """
        if not workspace.exists():
            raise FileNotFoundError(f"workspace does not exist: {workspace}")

        instance_id = f"ss-{uuid.uuid4().hex[:12]}"
        # S3 prefix for this run — trailing slash is intentional
        s3_prefix = f"workspace/{instance_id}/"

        # Collect files to upload — skip sensitive names, .git, and local-only
        # dependency directories (node_modules etc. are reinstalled inside the
        # ECS task by _S3_DEPS_BOOTSTRAP). (Decision #58)
        files = [
            f
            for f in workspace.rglob("*")
            if f.is_file()
            and not _sensitive_filename(f.name)
            and ".git" not in f.relative_to(workspace).parts
            and not any(
                part in _SKIP_DIRS
                for part in f.relative_to(workspace).parts
            )
        ]

        # 500 MB cap is checked against the filtered set (what we actually upload).
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
                print()  # newline before error so progress line isn't clobbered
                raise RuntimeError(f"S3 upload failed for {f}: {e}") from e
            # Inline progress — overwrite the same line every 5 files and on
            # the last file so long uploads are never silent.
            if i % 5 == 0 or i == total:
                print(
                    f"\r{_C_BLUE}[sandboxshift]{_C_RESET}"
                    f"  [{i}/{total}] uploading ...",
                    end="",
                    flush=True,
                )
        if total > 0:
            print()  # newline after inline progress
        _ok("Workspace uploaded")

        # Register a fresh task definition for this run (Decision #64).
        # CPU/memory/image are baked in at registration time — no overrides needed.
        _step("Registering ECS task definition ...")
        try:
            registered_task_def_arn = await asyncio.to_thread(
                self._register_task_definition, instance_id, config
            )
        except Exception as e:
            raise RuntimeError(f"Failed to register task definition: {e}") from e
        _ok(f"Task definition registered: {registered_task_def_arn.split('/')[-1]}")

        image = _detect_image(workspace)  # audit-only
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
        })

        return instance_id

    async def execute(
        self,
        instance_id: str,
        task: str,
        config: SandboxConfig,  # noqa: ARG002
    ) -> TaskResult:
        """Launch an ECS Fargate task.

        Batch mode (no ports): waits until STOPPED, fetches logs, returns result.
        Server mode (ports configured): waits until RUNNING, resolves public IP,
        prints URL, tails CloudWatch logs live (blocks until Ctrl+C — server
        stays running), then returns.

        Args:
            instance_id: Returned by provision().
            task:        Shell command string, run via /bin/sh -c.
            config:      Accepted for ABC compatibility — unused in V1.

        Returns:
            TaskResult with exit_code, stdout, stderr, and duration_seconds.

        Raises:
            RuntimeError:  If instance_id is unknown or ECS run_task fails.
            TimeoutError:  If the task exceeds config.timeout_seconds (batch) or
                           does not reach RUNNING within 5 minutes (server).
        """
        state = self._instances.get(instance_id)
        if state is None:
            raise RuntimeError(f"unknown instance_id: {instance_id}")

        if state.is_server:
            return await self._execute_server(instance_id, task, state)
        else:
            return await self._execute_batch(instance_id, task, state)

    async def destroy(self, instance_id: str) -> None:
        """Destroy the sandbox. Idempotent — never raises.

        Batch mode: stops the ECS task (if running), deregisters the task
        definition, and deletes the S3 prefix.
        Server mode: deregisters the task definition and deletes the S3 prefix
        only — does NOT stop the ECS task.
        The task keeps running; user calls `sandboxshift stop <instance_id>` to stop it.

        Args:
            instance_id: The ID returned by provision(). Unknown IDs are a no-op.
        """
        _step("Cleaning up S3 workspace ...")
        try:
            state = self._instances.get(instance_id)
            if state:
                # Only stop the ECS task for batch mode. Server tasks keep running.
                if state.ecs_task_arn and not state.is_server:
                    await asyncio.to_thread(self._stop_ecs_task, state)
                # Deregister the task definition for both batch and server mode.
                # For server mode this happens when `sandboxshift stop` calls destroy().
                if state.registered_task_def_arn:
                    await asyncio.to_thread(
                        self._deregister_task_definition,
                        state.registered_task_def_arn,
                    )
                await asyncio.to_thread(
                    self._delete_s3_prefix, state.bucket_name, state.s3_prefix
                )
            self._instances.pop(instance_id, None)
        except Exception:  # noqa: BLE001 — destroy must never raise
            pass
        finally:
            # Security Layer 7: audit event must always fire, even if cleanup fails.
            self._audit.record({"event": "destroy", "instance_id": instance_id})
        _ok("S3 workspace cleaned up")

    # -----------------------------------------------------------------------
    # Execute paths
    # -----------------------------------------------------------------------

    async def _execute_batch(
        self, instance_id: str, task: str, state: _FargateInstanceState
    ) -> TaskResult:
        """Batch execute: launch task, wait for STOPPED, fetch logs, return."""
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
        """Server execute: launch task, wait for RUNNING, tail logs, return.

        Flow:
          1. Launch ECS task (with server SG)
          2. Poll until RUNNING
          3. Resolve ENI public IP
          4. Print URL + stop command
          5. Tail CloudWatch logs live (blocks until Ctrl+C — server stays running)
          6. Return TaskResult

        The ECS task is NOT stopped here — it keeps running in Fargate.
        FargateRuntime.destroy() skips the ECS stop call for is_server=True.
        """
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

        # Poll until RUNNING (not STOPPED)
        await self._poll_until_running(ecs_task_arn, state)

        # Resolve ENI public IP via EC2 API
        _step("Resolving public IP ...")
        public_ip = await asyncio.to_thread(
            self._get_task_public_ip, ecs_task_arn, state
        )

        # Duration = time to reach RUNNING (not including log tail)
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

        # Persist server info so `sandboxshift stop` can find it later
        self._save_server_info(instance_id, ecs_task_arn, state, public_ip)

        self._audit.record({
            "event": "server_running",
            "instance_id": instance_id,
            "task": task,
            "public_ip": public_ip,
            "ports": [[h, c] for h, c in state.config.ports],
            "ecs_task_arn": ecs_task_arn,
            "duration_seconds": round(duration, 3),
        })

        # Tail CloudWatch logs live — blocks until Ctrl+C.
        # The server keeps running after the user stops tailing.
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
        """Upload a single workspace file to S3 under the run's S3 prefix."""
        s3 = self._session.client("s3", region_name=self._region)
        key = s3_prefix + str(file_path.relative_to(workspace))
        body = file_path.read_bytes()
        s3.put_object(Bucket=bucket_name, Key=key, Body=body)

    def _register_task_definition(
        self, instance_id: str, config: SandboxConfig
    ) -> str:
        """Register a fresh ECS task definition for this run. Returns the ARN.

        The task definition family name is '{task_family}-{instance_id}' —
        unique per run, enabling parallel runs and clean deregister on destroy.
        CPU (float vCPUs → ECS CPU units = vCPUs * 1024) and memory (MB)
        are baked in at registration time, so any valid Fargate combination
        works without Terraform changes. (Decision #64)

        The container image is self._ecr_image (resolved in __init__ from
        the ecr_image constructor arg, defaulting to
        'sandboxshift/runtime-multi:latest' if empty).
        """
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
        """Deregister an ECS task definition. Silently swallows all errors.

        Called in destroy() for both batch and server mode. Failure to
        deregister is non-fatal — the task definition will linger in ECS
        but will not affect future runs (each run gets a unique family name).
        """
        try:
            ecs = self._session.client("ecs", region_name=self._region)
            ecs.deregister_task_definition(taskDefinition=task_def_arn)
        except Exception:  # noqa: BLE001
            pass

    def _run_ecs_task(
        self, instance_id: str, task: str, state: _FargateInstanceState
    ) -> str:
        """Launch an ECS Fargate task and return its task ARN.

        The injected command runs up to four stages in sequence:
          1. _S3_DOWNLOAD_BOOTSTRAP — download workspace from S3 into /workspace
          2. _S3_DEPS_BOOTSTRAP     — cd /workspace, pip/npm install if manifests present
          3. config.setup_command   — optional user pre-task command (if set, e.g. "npm ci")
          4. task                   — the user's command

        The task definition was registered in provision() (Decision #64) with
        the correct CPU/memory already baked in. No task-level overrides are
        needed — the containerOverrides block holds only the command and
        environment variables.

        Server mode: appends server_security_group_id to the SG list so the
        task's public IP is reachable on any configured port (ALL TCP inbound).

        PORT env var is injected when ports are configured (Decision #57) so
        apps can read process.env.PORT (Node) or $PORT without hardcoding the
        port number. Uses the first configured container port.
        """
        user_task = (
            f"{state.config.setup_command} && {task}"
            if state.config.setup_command
            else task
        )
        full_command = f"{_S3_DOWNLOAD_BOOTSTRAP} && {_S3_DEPS_BOOTSTRAP} && {user_task}"

        # Server mode: attach the server SG (ALL TCP inbound) alongside the
        # standard batch SG. Batch mode: batch SG only.
        security_groups = list(self._security_group_ids)
        if state.is_server and self._server_security_group_id:
            security_groups.append(self._server_security_group_id)

        # Build container environment — always include sandbox bootstrap vars.
        # Inject PORT when ports are configured so apps read process.env.PORT/$PORT.
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
        """Poll ECS every 5s, printing a clean status line until STOPPED."""
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
        """Poll ECS every 5s until the task reaches RUNNING (server mode).

        Raises TimeoutError if RUNNING is not reached within 5 minutes.
        Raises RuntimeError if the task STOPs before reaching RUNNING.
        """
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
        """Stream CloudWatch Logs for a running server task until Ctrl+C.

        Waits up to 35s for the log stream to be created (Fargate tasks take a
        few seconds to start writing). Polls every 2s for new events and prints
        them directly to stdout. Handles Ctrl+C gracefully — the server task
        keeps running after tailing stops.
        """
        task_short_id = ecs_task_arn.split("/")[-1]
        stream_name = f"sandboxshift/sandbox/{task_short_id}"
        logs_client = self._session.client("logs", region_name=self._region)

        print(
            f"{_C_BLUE}[sandboxshift]{_C_RESET} "
            f"Streaming logs (Ctrl+C to stop tailing — server stays running):\n",
            flush=True,
        )

        # Wait for the log stream to be created (task startup takes a few seconds)
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
                "Log stream not yet available — "
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

                # get_log_events returns the same token when there are no new events;
                # only advance the token when new events were returned.
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
        """Resolve the public IP of a running Fargate task via its ENI.

        Returns None on any error — always safe to call.
        """
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
        """Persist server info to ~/.sandboxshift/servers.json for `sandboxshift stop`."""
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
        except Exception:  # noqa: BLE001 — saving server info must never crash execute
            pass

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
        """Fetch CloudWatch logs for the task. Never raises. Returns (stdout, stderr)."""
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
        """Stop the ECS task. No-op if already stopped."""
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
        """Delete all objects under an S3 prefix in batches of 1000."""
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
