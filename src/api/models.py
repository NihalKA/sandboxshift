"""Pydantic request/response models for the SandboxShift API."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Constants for security validation
# ---------------------------------------------------------------------------

# Sensitive system paths — workspace must not overlap any of these.
_SENSITIVE_ROOTS: tuple[Path, ...] = (
    Path.home() / ".aws",
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/root"),
)

# FQDN pattern: one or more labels (letters/digits/hyphens) separated by dots,
# ending with a TLD of at least 2 characters. No bare IPs.
_FQDN_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)

# Private / link-local IPv4 ranges to block outright.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("169.254.0.0/16"),   # link-local / IMDS
    ipaddress.IPv4Network("127.0.0.0/8"),       # loopback
)


def _is_blocked_ip(value: str) -> bool:
    """Return True if *value* is any IP address (bare IP = always blocked)."""
    try:
        ipaddress.ip_address(value)
        return True  # Any bare IP is rejected — FQDNs only.
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    workspace: str = Field(
        ...,
        description="Absolute path to the workspace directory on the host machine.",
    )
    task: str = Field(
        ...,
        min_length=1,
        description="Shell command to execute inside the sandbox.",
    )
    mode: str | None = Field(
        default=None,
        pattern=r"^(local|cloud|auto)$",
        description="Execution mode: 'local', 'cloud', or 'auto'. None → 'auto'.",
    )
    timeout: int | None = Field(
        default=None,
        gt=0,
        le=86400,
        description="Timeout override in seconds (1–86400).",
    )
    memory_mb: int | None = Field(
        default=None,
        gt=0,
        le=65536,   # 64 GB ceiling — prevent crash-the-host via unbounded cgroup
        description="Memory limit override in megabytes (1–65536).",
    )
    cpu: float | None = Field(
        default=None,
        gt=0.0,
        le=64.0,
        description="CPU limit override in fractional cores (0–64).",
    )
    allowed_hosts: list[str] | None = Field(
        default=None,
        description=(
            "FQDNs the sandbox may reach outbound. Bare IP addresses are rejected. "
            "Private/link-local ranges are also blocked."
        ),
    )
    setup_command: str | None = Field(
        default=None,
        description=(
            "Optional shell command to run before the main task inside the container. "
            "If set, executed as `setup_command && task` in a single /bin/sh invocation."
        ),
    )
    ports: list[str] | None = Field(
        default=None,
        description="Ports to expose as HOST:CONTAINER strings (e.g. ['8000:8000'])",
    )

    @field_validator("workspace")
    @classmethod
    def workspace_must_exist_and_be_safe(cls, v: str) -> str:
        """Validate workspace exists and does not overlap a sensitive system path.

        Resolves symlinks before checking to prevent traversal attacks.
        Sensitive roots: ~/.aws, ~/.ssh, ~/.gnupg, /etc, /proc, /sys, /root.

        Raises:
            ValueError: If the path does not exist or overlaps a protected root.
        """
        p = Path(v)
        if not p.exists():
            raise ValueError(f"workspace does not exist on disk: {v!r}")
        resolved = p.resolve()
        for sensitive in _SENSITIVE_ROOTS:
            try:
                sensitive_resolved = sensitive.resolve()
            except Exception:  # noqa: BLE001
                continue
            if resolved == sensitive_resolved or sensitive_resolved in resolved.parents:
                raise ValueError(
                    f"workspace overlaps a protected system path: {v!r} "
                    f"(contains {sensitive})"
                )
        return v

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def validate_allowed_hosts(cls, v: object) -> object:
        """Reject bare IP addresses and private/link-local ranges.

        Only fully-qualified domain names (FQDNs) are permitted.
        This prevents SSRF attacks against IMDS (169.254.169.254) and internal
        services visible from the sandbox network.

        Raises:
            ValueError: If any entry is a bare IP, an invalid FQDN, or a
                        private/link-local address range.
        """
        if v is None:
            return v
        entries: list[str] = list(v)  # type: ignore[arg-type]
        for entry in entries:
            entry = entry.strip()
            if _is_blocked_ip(entry):
                raise ValueError(
                    f"allowed_hosts must contain FQDNs only — bare IP addresses "
                    f"are not permitted: {entry!r}"
                )
            if not _FQDN_RE.match(entry):
                raise ValueError(
                    f"allowed_hosts entry is not a valid FQDN: {entry!r}"
                )
        return entries

    @field_validator("ports", mode="before")
    @classmethod
    def _validate_ports(cls, v: list[str] | None) -> list[str] | None:
        """Validate each port string is HOST:CONTAINER with integers in 1–65535.

        Raises:
            ValueError: If any entry is malformed or out of range.
        """
        if v is None:
            return v
        validated = []
        for p in v:
            parts = str(p).split(":")
            if len(parts) != 2:
                raise ValueError(f"Invalid port '{p}': expected HOST:CONTAINER")
            try:
                h, c = int(parts[0]), int(parts[1])
            except ValueError:
                raise ValueError(f"Port numbers in '{p}' must be integers")
            if not (1 <= h <= 65535) or not (1 <= c <= 65535):
                raise ValueError(f"Port numbers in '{p}' must be 1-65535")
            validated.append(p)
        return validated


class RunResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    runtime_mode: str
    sensitivity_reasons: list[str]
    burst_confidence: str
    duration_seconds: float
    # NO session_id — RunResult has no session_id field


class HealthResponse(BaseModel):
    status: str
    version: str


class AuditEntry(BaseModel):
    model_config = {"extra": "allow"}

    ts: str | None = Field(default=None)
    session: str | None = Field(default=None)
    event: str | None = Field(default=None)
