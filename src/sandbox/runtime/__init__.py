"""SandboxShift runtime adaptors."""

from .base import Runtime, TaskResult
from .podman import PodmanRuntime

__all__ = ["PodmanRuntime", "Runtime", "TaskResult"]
