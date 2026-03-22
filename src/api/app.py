"""FastAPI application factory for SandboxShift."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ..observability.audit import AuditLogger
from ..sandbox.burst.engine import BurstEngine
from ..sandbox.detection.sensitivity import SensitivityScanner
from ..sandbox.manager import SandboxManager
from ..sandbox.runtime.fargate import FargateRuntime
from ..sandbox.runtime.podman import PodmanRuntime
from .routes import router

# Required Fargate env vars (Decision #64: task_def_arn replaced by
# execution_role_arn + task_role_arn; ECR image passed separately).
# FARGATE_ECR_IMAGE is optional — absent = Docker Hub default.
_FARGATE_ENV_VARS = [
    "FARGATE_CLUSTER_ARN",
    "FARGATE_EXECUTION_ROLE_ARN",
    "FARGATE_TASK_ROLE_ARN",
    "FARGATE_SUBNET_IDS",
    "FARGATE_SECURITY_GROUP_IDS",   # comma-separated list
    "FARGATE_LOG_GROUP",
    "FARGATE_REGION",
    "FARGATE_WORKSPACE_BUCKET",
]


def _build_fargate_runtime(audit_logger: AuditLogger) -> FargateRuntime | None:
    """Return a FargateRuntime if all required env vars are set, else None.

    Optional env vars:
      FARGATE_TASK_FAMILY — prefix for dynamic task def family names
        (default: 'sandboxshift-sandbox').
      FARGATE_ECR_IMAGE — full container image URI; if absent or empty,
        defaults to 'sandboxshift/runtime-multi:latest' (Docker Hub).
    """
    values = {k: os.environ.get(k, "").strip() for k in _FARGATE_ENV_VARS}
    if any(v == "" for v in values.values()):
        return None
    task_family = os.environ.get("FARGATE_TASK_FAMILY", "sandboxshift-sandbox").strip()
    ecr_image = os.environ.get("FARGATE_ECR_IMAGE", "").strip()
    return FargateRuntime(
        cluster_arn=values["FARGATE_CLUSTER_ARN"],
        execution_role_arn=values["FARGATE_EXECUTION_ROLE_ARN"],
        task_role_arn=values["FARGATE_TASK_ROLE_ARN"],
        subnet_ids=values["FARGATE_SUBNET_IDS"].split(","),
        security_group_ids=values["FARGATE_SECURITY_GROUP_IDS"].split(","),
        region=values["FARGATE_REGION"],
        log_group=values["FARGATE_LOG_GROUP"],
        workspace_bucket=values["FARGATE_WORKSPACE_BUCKET"],
        task_family=task_family,
        ecr_image=ecr_image,
        audit_logger=audit_logger,
    )


def create_app() -> FastAPI:
    """Create and return a configured FastAPI application instance.

    Always call this factory — never use a module-level app instance.
    This enables clean dependency injection in tests.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]
        # --- Audit logger ---
        audit_log_env = os.environ.get("SANDBOXSHIFT_AUDIT_LOG", "")
        audit_log_path = (
            Path(audit_log_env).expanduser()
            if audit_log_env
            else Path.home() / ".sandboxshift" / "audit.log"
        )
        audit_logger = AuditLogger(log_path=audit_log_path)

        # --- BurstEngine ---
        try:
            ram_threshold_gb = float(os.environ.get("SANDBOXSHIFT_RAM_THRESHOLD_GB", "4.0"))
        except ValueError:
            ram_threshold_gb = 4.0
        burst_engine = BurstEngine(ram_threshold_gb=ram_threshold_gb)

        # --- Runtimes ---
        local_runtime = PodmanRuntime(audit_logger=audit_logger)
        cloud_runtime = _build_fargate_runtime(audit_logger)

        # --- Manager ---
        scanner = SensitivityScanner()
        manager = SandboxManager(
            local_runtime=local_runtime,
            cloud_runtime=cloud_runtime,
            burst_engine=burst_engine,
            scanner=scanner,
            audit_logger=audit_logger,
        )

        # --- Wire into app.state ---
        app.state.manager = manager
        app.state.audit_log_path = audit_log_path
        app.state.cloud_runtime_available = cloud_runtime is not None

        yield
        # No teardown needed in V1

    app = FastAPI(title="SandboxShift", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app
