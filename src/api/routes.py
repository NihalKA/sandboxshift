"""FastAPI route handlers for SandboxShift."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..config import SandboxConfig
from .models import AuditEntry, HealthResponse, RunRequest, RunResponse

_VERSION = "0.1.0"

router = APIRouter()


def _get_manager(request: Request) -> Any:
    return request.app.state.manager


ManagerDep = Annotated[Any, Depends(_get_manager)]


@router.post("/run", response_model=RunResponse)
async def run_sandbox(body: RunRequest, manager: ManagerDep) -> RunResponse:
    config = SandboxConfig(
        timeout_seconds=body.timeout if body.timeout is not None else SandboxConfig().timeout_seconds,
        memory_limit_mb=body.memory_mb if body.memory_mb is not None else SandboxConfig().memory_limit_mb,
        cpu_limit=body.cpu if body.cpu is not None else SandboxConfig().cpu_limit,
        network_allow=body.allowed_hosts if body.allowed_hosts is not None else [],
    )
    try:
        result = await manager.run(workspace=Path(body.workspace), task=body.task, config=config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return RunResponse(
        exit_code=result.task_result.exit_code,
        stdout=result.task_result.stdout,
        stderr=result.task_result.stderr,
        runtime_mode=result.runtime_mode,
        sensitivity_reasons=result.sensitivity_reasons,
        burst_confidence=result.burst_confidence,
        duration_seconds=result.duration_seconds,
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=_VERSION)


@router.get("/audit", response_model=list[AuditEntry])
async def get_audit(
    request: Request,
    n: Annotated[int, Query(gt=0, le=10_000)] = 100,
) -> list[AuditEntry]:
    audit_log_path: Path = request.app.state.audit_log_path
    if not audit_log_path.exists():
        return []
    entries: list[AuditEntry] = []
    try:
        text = audit_log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    # Take last n lines
    lines = lines[-n:]
    for line in lines:
        try:
            data = json.loads(line)
            entries.append(AuditEntry.model_validate(data))
        except Exception:  # noqa: BLE001
            pass
    return entries
