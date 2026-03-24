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
        cpu_limit:                Number of CPUs allocated to the container.
        memory_limit_mb:          RAM limit in megabytes.
        network_allow:            FQDNs the container may reach outbound. Empty = no network.
        timeout_seconds:          Maximum task wall-clock time before kill.
        workspace_readonly:       If True, workspace is mounted read-only inside the container.
        setup_command:            Optional shell command run before the main task.
                                  If set, PodmanRuntime runs it as ``setup_command && task``
                                  in a single /bin/sh invocation. None = no setup step.
        ports:                    List of (host_port, container_port) tuples to expose.
                                  Ports are bound to 127.0.0.1 only (never 0.0.0.0).
                                  Empty list = no port exposure (default).
        skip_sensitivity_check:   When True, skip the sensitive-data scan entirely.
                                  Use only for workspaces you own and trust.
                                  WARNING: disabling this weakens Security Layer 6.
        min_cpu_required:         Minimum number of logical CPUs the task needs.
                                  If local machine has fewer CPUs, BurstEngine will
                                  force cloud execution. 0.0 = no requirement (default).
        min_memory_mb_required:   Minimum available RAM in MB the task needs.
                                  If local available RAM is less, BurstEngine will
                                  force cloud execution. 0 = no requirement (default).
        upload_allow_files:       List of filenames explicitly allowed to upload to S3
                                  even if they match sensitive patterns (e.g. ['.env',
                                  '.env.dev']). Exact filename match only — not a glob.
                                  Each overridden file is recorded in the audit log.
        env_vars:                 Extra environment variables injected into the container.
                                  Keys and values are passed as-is. Values are NEVER
                                  written to the audit log — only keys are recorded.
                                  Both Podman (--env KEY=VAL) and Fargate
                                  (containerOverrides environment) honour this field.
    """

    cpu_limit: float = 2.0
    memory_limit_mb: int = 4096
    network_allow: list[str] = field(default_factory=list)
    timeout_seconds: int = 1800
    workspace_readonly: bool = False
    setup_command: str | None = None
    ports: list[tuple[int, int]] = field(default_factory=list)
    skip_sensitivity_check: bool = False
    min_cpu_required: float = 0.0
    min_memory_mb_required: int = 0
    upload_allow_files: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
