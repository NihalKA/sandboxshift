"""Pydantic request/response models for the SandboxShift API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RunRequest(BaseModel):
    workspace: str = Field(..., description="Absolute path to the workspace directory.")
    task: str = Field(..., min_length=1, description="Shell command to run inside the sandbox.")
    mode: str | None = Field(default=None, pattern=r"^(local|cloud|auto)$")
    timeout: int | None = Field(default=None, gt=0, le=86400)
    memory_mb: int | None = Field(default=None, gt=0)
    cpu: float | None = Field(default=None, gt=0.0)
    allowed_hosts: list[str] | None = Field(default=None)

    @field_validator("workspace")
    @classmethod
    def workspace_must_exist(cls, v: str) -> str:
        if not Path(v).exists():
            raise ValueError(f"workspace does not exist on disk: {v!r}")
        return v


class RunResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    runtime_mode: str
    sensitivity_reasons: list[str]
    burst_confidence: str
    duration_seconds: float
    # NO session_id — RunResult has no session_id


class HealthResponse(BaseModel):
    status: str
    version: str


class AuditEntry(BaseModel):
    model_config = {"extra": "allow"}

    ts: str | None = Field(default=None)
    session: str | None = Field(default=None)
    event: str | None = Field(default=None)
