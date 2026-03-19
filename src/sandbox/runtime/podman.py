"""PodmanRuntime — V1 local sandbox runtime for SandboxShift.

Runs agent tasks inside rootless Podman containers with:
- Auto-detected Chainguard base images (zero CVEs, Decision #2)
- Enforced CPU, RAM, and network limits (Security Layer 5)
- Non-root user (UID 65532 — Chainguard nonroot, Security Layer 2)
- --security-opt=no-new-privileges (prevents setuid escalation)
- Workspace-only volume mount (no host path leakage)
- Full audit trail via AuditLogger (Security Layer 7)
- Port exposure bound exclusively to 127.0.0.1 (Decision #50)

All Podman CLI calls go through asyncio.to_thread(subprocess.run, ...)
so the event loop is never blocked. subprocess.run is the sole integration
point for non-port tasks — tests mock it exclusively.  When ports are
configured, subprocess.Popen is used for real-time stdout streaming
(Decision #51).  No real containers are created in tests.

--privileged is NEVER passed. This is enforced by construction — the flag
does not appear anywhere in this module.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ...config import SandboxConfig
from ...observability.audit import AuditLogger
from .base import Runtime, TaskResult


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_IMAGE = "cgr.dev/chainguard/python:latest"
_NONROOT_USER = "65532:65532"  # Chainguard nonroot UID:GID — never root

_MARKER_IMAGES: dict[str, str] = {
    "requirements.txt": "cgr.dev/chainguard/python:latest",
    "package.json": "cgr.dev/chainguard/node:latest",
    "go.mod": "cgr.dev/chainguard/go:latest",
}

# Pip package cache — persisted on the host across container runs.
# Container path matches Chainguard nonroot user (UID 65532) home in /home/nonroot.
_PIP_CACHE_CONTAINER_PATH: str = "/home/nonroot/.cache/pip"


def _pip_cache_host_path() -> Path:
    """Return the host-side pip cache directory path.

    Implemented as a function (not a module-level constant) so tests can
    monkeypatch it cleanly without patching Path.home() globally.

    Returns:
        Path: ``~/.sandboxshift/cache/pip``
    """
    return Path.home() / ".sandboxshift" / "cache" / "pip"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


@dataclass
class _InstanceState:
    """Internal state stored between provision() and execute()."""

    image: str
    workspace: Path
    config: SandboxConfig
    resolved_hosts: dict[str, str] = field(default_factory=dict)  # domain → IPv4


def _detect_image(workspace: Path) -> str:
    """Return the Chainguard image tag appropriate for this workspace.

    Checks for marker files (requirements.txt, package.json, go.mod).
    Multiple markers → multi-runtime image. None → default Python image.
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


def _resolve_host(domain: str) -> str | None:
    """Resolve a domain name to an IPv4 string for --add-host injection.

    Returns None on any resolution failure — callers must handle the None case.
    Never raises.
    """
    try:
        results = socket.getaddrinfo(domain, None, socket.AF_INET)
        return str(results[0][4][0])
    except socket.gaierror:
        return None


def _check_port_available(host_port: int) -> None:
    """Raise OSError if *host_port* is already in use on 127.0.0.1.

    Uses a temporary socket bind as a pre-flight check.  Fails fast before
    the container starts so the user gets a clear error message rather than
    a silent Podman bind failure (Decision #52).

    Args:
        host_port: TCP port number to test on the loopback interface.

    Raises:
        OSError: If the port is already bound by another process.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", host_port))
        except OSError:
            raise OSError(
                f"Port {host_port} is already in use on 127.0.0.1. "
                "Free the port and retry."
            )


# ---------------------------------------------------------------------------
# PodmanRuntime
# ---------------------------------------------------------------------------


class PodmanRuntime(Runtime):
    """V1 local sandbox runtime using rootless Podman.

    All Podman CLI calls are made via asyncio.to_thread(subprocess.run, ...)
    so the async event loop is never blocked.

    When ``config.ports`` is non-empty, ``execute()`` switches to
    ``subprocess.Popen`` with real-time stdout streaming so server output
    is visible immediately in the terminal (Decision #51).

    Args:
        audit_logger: Optional AuditLogger instance. Defaults to the V1 stub.
    """

    def __init__(self, audit_logger: AuditLogger | None = None) -> None:
        self._audit = audit_logger if audit_logger is not None else AuditLogger()
        self._instances: dict[str, _InstanceState] = {}

    async def provision(self, workspace: Path, config: SandboxConfig) -> str:
        """Provision a sandbox for the given workspace.

        Steps:
          1. Validate workspace exists.
          2. Auto-detect Chainguard image from workspace markers.
          3. Generate a unique instance_id.
          4. Resolve DNS for each domain in config.network_allow.
          5. Pre-check each host port in config.ports for availability.
          6. Store _InstanceState keyed by instance_id.
          7. Record audit event.
          8. Return instance_id.

        Args:
            workspace: Directory to mount into the container. Must exist.
            config:    Sandbox configuration (CPU, RAM, network policy, timeout,
                       ports).

        Returns:
            Opaque instance_id string (format: "ss-{12 hex chars}").

        Raises:
            FileNotFoundError: If workspace does not exist.
            OSError:           If any requested host port is already in use
                               (Decision #52 — fail fast before container starts).
        """
        if not workspace.exists():
            raise FileNotFoundError(f"workspace does not exist: {workspace}")

        image = _detect_image(workspace)
        instance_id = f"ss-{uuid.uuid4().hex[:12]}"

        # Resolve DNS for allowed domains ahead of container start.
        # Fail-safe: unresolvable domains are skipped with an audit warning.
        resolved_hosts: dict[str, str] = {}
        for domain in config.network_allow:
            ip = await asyncio.to_thread(_resolve_host, domain)
            if ip is not None:
                resolved_hosts[domain] = ip
            else:
                self._audit.record(
                    {
                        "event": "dns_resolution_failed",
                        "instance_id": instance_id,
                        "domain": domain,
                    }
                )

        # Pre-check port availability (Decision #52).
        # Fail fast with a clear error before starting the container.
        # _check_port_available is a fast socket bind — called synchronously.
        for host_port, _container_port in config.ports:
            _check_port_available(host_port)

        self._instances[instance_id] = _InstanceState(
            image=image,
            workspace=workspace,
            config=config,
            resolved_hosts=resolved_hosts,
        )

        self._audit.record(
            {
                "event": "provision",
                "instance_id": instance_id,
                "image": image,
                "workspace": str(workspace),
                "ports": [[h, c] for h, c in config.ports],
            }
        )

        return instance_id

    async def execute(
        self,
        instance_id: str,
        task: str,
        config: SandboxConfig,  # noqa: ARG002 — reserved for V2; use stored state
    ) -> TaskResult:
        """Execute a shell task in the provisioned sandbox.

        If ``state.config.setup_command`` is set, it is prepended to the task
        as ``setup_command && task`` inside a single ``/bin/sh -c`` invocation.

        If ``state.config.ports`` is non-empty, ``subprocess.Popen`` is used
        with real-time stdout streaming so server logs appear immediately in the
        terminal.  The returned ``TaskResult.stdout`` is
        ``"<streamed to terminal>"`` in that case (Decision #51).

        Args:
            instance_id: Returned by provision().
            task:        Shell command string, wrapped in /bin/sh -c.
            config:      Accepted for ABC compatibility — unused in V1.

        Returns:
            TaskResult with exit_code, stdout, stderr, and duration_seconds.

        Raises:
            RuntimeError:             If instance_id was not returned by a prior provision().
            subprocess.TimeoutExpired: If the task exceeds config.timeout_seconds
                                       (capture mode only).
        """
        state = self._instances.get(instance_id)
        if state is None:
            raise RuntimeError(f"unknown instance_id: {instance_id}")

        network_flags = self._build_network_flags(state)

        ro_suffix = ":ro" if state.config.workspace_readonly else ""
        volume_flag = f"{state.workspace}:/workspace{ro_suffix}"

        pip_cache_host = _pip_cache_host_path()
        try:
            pip_cache_host.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        pip_cache_volume_flag = f"{pip_cache_host}:{_PIP_CACHE_CONTAINER_PATH}"

        shell_cmd = (
            f"{state.config.setup_command} && {task}"
            if state.config.setup_command
            else task
        )

        # Build port publish flags — always bound to 127.0.0.1 (Decision #50).
        port_flags: list[str] = []
        for h, c in state.config.ports:
            port_flags.extend(["-p", f"127.0.0.1:{h}:{c}"])

        cmd: list[str] = [
            "podman",
            "run",
            "--name",
            instance_id,
            "--rm",
            "--user",
            _NONROOT_USER,
            "--cpus",
            str(state.config.cpu_limit),
            "--memory",
            f"{state.config.memory_limit_mb}m",
            "--volume",
            volume_flag,
            "--volume",
            pip_cache_volume_flag,
            "--workdir",
            "/workspace",
            "--security-opt",
            "no-new-privileges",
            *network_flags,
            *port_flags,
            state.image,
            "/bin/sh",
            "-c",
            shell_cmd,
        ]

        t_start = time.perf_counter()

        if state.config.ports:
            # Streaming mode — long-running servers need real-time output.
            # Uses Popen so stdout lines are written to the terminal as they
            # arrive.  asyncio.to_thread keeps the event loop unblocked.
            def _run_streaming() -> int:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    # Decode and print — print() works in all environments
                    # including pytest capture mode (no .buffer needed).
                    text = raw_line.decode("utf-8", errors="replace")
                    print(text, end="", flush=True)
                proc.wait()
                return proc.returncode

            exit_code = await asyncio.to_thread(_run_streaming)
            duration = time.perf_counter() - t_start

            self._audit.record(
                {
                    "event": "execute",
                    "instance_id": instance_id,
                    "setup_command": state.config.setup_command,
                    "task": task,
                    "exit_code": exit_code,
                    "duration_seconds": round(duration, 3),
                    "ports": [[h, c] for h, c in state.config.ports],
                    "streaming": True,
                }
            )

            return TaskResult(
                exit_code=exit_code,
                stdout="<streamed to terminal>",
                stderr="",
                duration_seconds=duration,
            )

        else:
            # Capture mode — batch tasks; collect stdout/stderr for the caller.
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=state.config.timeout_seconds,
            )
            duration = time.perf_counter() - t_start

            self._audit.record(
                {
                    "event": "execute",
                    "instance_id": instance_id,
                    "setup_command": state.config.setup_command,
                    "task": task,
                    "exit_code": result.returncode,
                    "duration_seconds": round(duration, 3),
                    "ports": [],
                    "streaming": False,
                }
            )

            return TaskResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration,
            )

    async def destroy(self, instance_id: str) -> None:
        """Destroy the sandbox container. Idempotent — never raises.

        Calls `podman rm -f {instance_id}`. Ignores all errors including:
        - Non-zero exit codes (container already removed by --rm flag on podman run)
        - FileNotFoundError / OSError (podman binary not on PATH)

        Args:
            instance_id: The ID returned by provision(). If unknown, this is a no-op.
        """
        cmd = ["podman", "rm", "-f", instance_id]
        try:
            await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        except Exception:  # noqa: BLE001 — intentionally broad; destroy must never raise
            pass

        self._instances.pop(instance_id, None)

        self._audit.record({"event": "destroy", "instance_id": instance_id})

    def _build_network_flags(self, state: _InstanceState) -> list[str]:
        """Return the Podman network flags for the given instance state.

        Ports require the slirp4netns network stack even when network_allow is
        empty, because --network=none disables the loopback interface used by
        port publishing (Decision #53).

        Rules:
        - No network_allow AND no ports → --network=none (full isolation)
        - network_allow OR ports         → slirp4netns + --dns=none
        """
        needs_network = bool(state.config.network_allow) or bool(state.config.ports)
        if not needs_network:
            return ["--network=none"]

        flags: list[str] = ["--network=slirp4netns", "--dns=none"]
        for domain, ip in state.resolved_hosts.items():
            flags.append(f"--add-host={domain}:{ip}")
        return flags
