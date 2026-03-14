"""Shared sandbox configuration dataclass for SandboxShift runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SandboxConfig:
    """Configuration for a sandbox run.

    All fields have defaults so callers can construct a minimal config with
    SandboxConfig(). Fields are mutable — callers may adjust before passing to
    a runtime.

    Attributes:
        cpu_limit:          Number of CPUs allocated to the container.
        memory_limit_mb:    RAM limit in megabytes.
        network_allow:      FQDNs the container may reach outbound. Empty = no network.
        timeout_seconds:    Maximum task wall-clock time before kill.
        workspace_readonly: If True, workspace is mounted read-only inside the container.
    """

    cpu_limit: float = 2.0
    memory_limit_mb: int = 4096
    network_allow: list[str] = field(default_factory=list)
    timeout_seconds: int = 1800
    workspace_readonly: bool = False
