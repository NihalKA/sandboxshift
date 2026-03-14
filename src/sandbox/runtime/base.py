"""Abstract Runtime interface and TaskResult for SandboxShift.

All runtime adaptors (PodmanRuntime, FargateRuntime) implement Runtime.
TaskResult is the common return type for execute().
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import SandboxConfig


@dataclass
class TaskResult:
    """Result of executing a task inside a sandbox.

    Attributes:
        exit_code:        Container exit code (0 = success).
        stdout:           Full captured standard output.
        stderr:           Full captured standard error.
        duration_seconds: Wall-clock seconds for the execute() call.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class Runtime(abc.ABC):
    """Abstract base class for all SandboxShift runtime adaptors.

    Implementations: PodmanRuntime (local), FargateRuntime (cloud).
    """

    @abc.abstractmethod
    async def provision(self, workspace: Path, config: "SandboxConfig") -> str:
        """Provision a sandbox. Returns an opaque instance ID."""

    @abc.abstractmethod
    async def execute(
        self, instance_id: str, task: str, config: "SandboxConfig"
    ) -> TaskResult:
        """Execute a shell task in the sandbox. Returns TaskResult."""

    @abc.abstractmethod
    async def destroy(self, instance_id: str) -> None:
        """Destroy the sandbox. Must be called even if execute() raised."""
