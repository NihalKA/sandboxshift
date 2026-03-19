"""Tests for PodmanRuntime, _detect_image, _resolve_host, and _check_port_available.

All subprocess.run calls are mocked — no real containers are created.
asyncio_mode = "auto" is set in pyproject.toml; no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.config import SandboxConfig
from src.observability.audit import AuditLogger
from src.sandbox.runtime.podman import (
    PodmanRuntime,
    _InstanceState,
    _check_port_available,
    _detect_image,
    _resolve_host,
)
from src.sandbox.runtime.base import TaskResult


# ---------------------------------------------------------------------------
# Constants & fixtures
# ---------------------------------------------------------------------------

SUBPROCESS_PATCH = "src.sandbox.runtime.podman.subprocess.run"
TO_THREAD_PATCH = "src.sandbox.runtime.podman.asyncio.to_thread"


def _ok_result(**kwargs) -> CompletedProcess:  # type: ignore[type-arg]
    """Return a successful CompletedProcess with sensible defaults."""
    defaults = {"args": [], "returncode": 0, "stdout": "hello\n", "stderr": ""}
    defaults.update(kwargs)
    return CompletedProcess(**defaults)


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    """An existing temporary directory used as the workspace in tests."""
    return tmp_path


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Alias for tmp_path — used in Group 8 tests."""
    return tmp_path


@pytest.fixture()
def default_config() -> SandboxConfig:
    """SandboxConfig with all defaults (empty network_allow, empty ports)."""
    return SandboxConfig()


@pytest.fixture()
def runtime() -> PodmanRuntime:
    """Fresh PodmanRuntime with the default AuditLogger stub."""
    return PodmanRuntime()


@pytest.fixture()
def mock_audit() -> MagicMock:
    """MagicMock with AuditLogger spec for asserting on audit calls."""
    return MagicMock(spec=AuditLogger)


@pytest.fixture()
def mock_subprocess():
    """Patches subprocess.run and asyncio.to_thread.

    asyncio.to_thread is replaced with a synchronous caller so that
    async tests work without spawning real threads.

    Returns the subprocess.run mock so tests can customise return_value.
    """
    run_mock = MagicMock(return_value=_ok_result())

    async def fake_to_thread(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
        return fn(*args, **kwargs)

    with (
        patch(SUBPROCESS_PATCH, run_mock),
        patch(TO_THREAD_PATCH, side_effect=fake_to_thread),
    ):
        yield run_mock


@pytest.fixture()
def mock_port_check(monkeypatch):
    """Silences _check_port_available so provision() never contacts the network."""
    monkeypatch.setattr(
        "src.sandbox.runtime.podman._check_port_available", lambda port: None
    )


# ---------------------------------------------------------------------------
# Group 1 — _detect_image unit tests (sync, no mocking)
# ---------------------------------------------------------------------------


def test_detect_image_python(tmp_workspace: Path) -> None:
    (tmp_workspace / "requirements.txt").touch()
    assert _detect_image(tmp_workspace) == "cgr.dev/chainguard/python:latest"


def test_detect_image_node(tmp_workspace: Path) -> None:
    (tmp_workspace / "package.json").touch()
    assert _detect_image(tmp_workspace) == "cgr.dev/chainguard/node:latest"


def test_detect_image_go(tmp_workspace: Path) -> None:
    (tmp_workspace / "go.mod").touch()
    assert _detect_image(tmp_workspace) == "cgr.dev/chainguard/go:latest"


def test_detect_image_multi(tmp_workspace: Path) -> None:
    (tmp_workspace / "requirements.txt").touch()
    (tmp_workspace / "package.json").touch()
    assert _detect_image(tmp_workspace) == "sandboxshift/runtime-multi"


def test_detect_image_default_on_no_markers(tmp_workspace: Path) -> None:
    assert _detect_image(tmp_workspace) == "cgr.dev/chainguard/python:latest"


# ---------------------------------------------------------------------------
# Group 2 — _resolve_host unit tests (sync, no async)
# ---------------------------------------------------------------------------


def test_resolve_host_success() -> None:
    with patch("src.sandbox.runtime.podman.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(None, None, None, None, ("1.2.3.4", 0))]
        result = _resolve_host("example.com")
    assert result == "1.2.3.4"


def test_resolve_host_returns_none_on_gaierror() -> None:
    with patch("src.sandbox.runtime.podman.socket.getaddrinfo") as mock_gai:
        mock_gai.side_effect = socket.gaierror("mock dns failure")
        result = _resolve_host("nonexistent.invalid")
    assert result is None


# ---------------------------------------------------------------------------
# Group 3 — provision() tests
# ---------------------------------------------------------------------------


async def test_provision_returns_string_instance_id(
    runtime: PodmanRuntime,
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    instance_id = await runtime.provision(tmp_workspace, default_config)
    assert isinstance(instance_id, str)
    assert instance_id.startswith("ss-")


async def test_provision_unique_ids(
    runtime: PodmanRuntime,
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    id1 = await runtime.provision(tmp_workspace, default_config)
    id2 = await runtime.provision(tmp_workspace, default_config)
    assert id1 != id2


async def test_provision_raises_on_missing_workspace(
    runtime: PodmanRuntime,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    missing = Path("/tmp/sandboxshift-test-does-not-exist-abc123")
    with pytest.raises(FileNotFoundError):
        await runtime.provision(missing, default_config)
    mock_subprocess.assert_not_called()


async def test_provision_stores_instance_in_internal_dict(
    runtime: PodmanRuntime,
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    instance_id = await runtime.provision(tmp_workspace, default_config)
    assert instance_id in runtime._instances
    assert isinstance(runtime._instances[instance_id], _InstanceState)


async def test_provision_detects_correct_image(
    runtime: PodmanRuntime,
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    (tmp_workspace / "package.json").touch()
    instance_id = await runtime.provision(tmp_workspace, default_config)
    assert runtime._instances[instance_id].image == "cgr.dev/chainguard/node:latest"


async def test_provision_resolves_dns_for_allowed_domains(
    runtime: PodmanRuntime,
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(network_allow=["example.com"])
    with patch("src.sandbox.runtime.podman._resolve_host", return_value="1.2.3.4"):
        instance_id = await runtime.provision(tmp_workspace, config)
    assert runtime._instances[instance_id].resolved_hosts == {"example.com": "1.2.3.4"}


async def test_provision_skips_unresolvable_domain(
    runtime: PodmanRuntime,
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(network_allow=["bad.invalid"])
    with patch("src.sandbox.runtime.podman._resolve_host", return_value=None):
        instance_id = await runtime.provision(tmp_workspace, config)
    assert runtime._instances[instance_id].resolved_hosts == {}


async def test_provision_calls_audit_record(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_audit: MagicMock,
    mock_subprocess,
) -> None:
    rt = PodmanRuntime(audit_logger=mock_audit)
    await rt.provision(tmp_workspace, default_config)
    assert mock_audit.record.call_count >= 1
    events = [c.args[0]["event"] for c in mock_audit.record.call_args_list]
    assert "provision" in events


# ---------------------------------------------------------------------------
# Group 4 — execute() tests
# ---------------------------------------------------------------------------


async def _provisioned_runtime(
    tmp_workspace: Path,
    config: SandboxConfig,
    mock_subprocess,
    audit: AuditLogger | None = None,
) -> tuple[PodmanRuntime, str]:
    """Helper: provision and return (runtime, instance_id)."""
    rt = PodmanRuntime(audit_logger=audit)
    instance_id = await rt.provision(tmp_workspace, config)
    return rt, instance_id


async def test_execute_returns_task_result(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    result = await rt.execute(iid, "echo hi", default_config)
    assert isinstance(result, TaskResult)
    assert result.exit_code == 0


async def test_execute_captures_stdout(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    mock_subprocess.return_value = _ok_result(stdout="expected output")
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    result = await rt.execute(iid, "echo expected output", default_config)
    assert result.stdout == "expected output"


async def test_execute_captures_stderr(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    mock_subprocess.return_value = _ok_result(stderr="some error")
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    result = await rt.execute(iid, "echo err >&2", default_config)
    assert result.stderr == "some error"


async def test_execute_captures_nonzero_exit_code(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    mock_subprocess.return_value = _ok_result(returncode=42)
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    result = await rt.execute(iid, "exit 42", default_config)
    assert result.exit_code == 42


async def test_execute_duration_seconds_non_negative(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    result = await rt.execute(iid, "echo hi", default_config)
    assert result.duration_seconds >= 0.0


async def test_execute_unknown_instance_id_raises(
    runtime: PodmanRuntime,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    with pytest.raises(RuntimeError, match="unknown instance_id"):
        await runtime.execute("ss-doesnotexist", "echo hi", default_config)


async def test_execute_command_never_contains_privileged(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.execute(iid, "echo hi", default_config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--privileged" not in cmd


async def test_execute_command_contains_user_flag(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.execute(iid, "echo hi", default_config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--user" in cmd
    user_idx = cmd.index("--user")
    assert cmd[user_idx + 1] == "65532:65532"


async def test_execute_command_contains_cpus_flag(
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(cpu_limit=1.5)
    rt, iid = await _provisioned_runtime(tmp_workspace, config, mock_subprocess)
    await rt.execute(iid, "echo hi", config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--cpus" in cmd
    assert "1.5" in cmd


async def test_execute_command_contains_memory_flag(
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(memory_limit_mb=2048)
    rt, iid = await _provisioned_runtime(tmp_workspace, config, mock_subprocess)
    await rt.execute(iid, "echo hi", config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--memory" in cmd
    assert "2048m" in cmd


async def test_execute_command_contains_workspace_volume(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.execute(iid, "echo hi", default_config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--volume" in cmd
    vol_idx = cmd.index("--volume")
    assert str(tmp_workspace) in cmd[vol_idx + 1]


async def test_execute_command_readonly_workspace(
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(workspace_readonly=True)
    rt, iid = await _provisioned_runtime(tmp_workspace, config, mock_subprocess)
    await rt.execute(iid, "echo hi", config)
    cmd = mock_subprocess.call_args[0][0]
    vol_idx = cmd.index("--volume")
    assert cmd[vol_idx + 1].endswith(":ro")


async def test_execute_command_network_none_when_empty_allowlist(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.execute(iid, "echo hi", default_config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--network=none" in cmd


async def test_execute_command_slirp4netns_when_allowlist_set(
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(network_allow=["pypi.org"])
    with patch("src.sandbox.runtime.podman._resolve_host", return_value="1.2.3.4"):
        rt, iid = await _provisioned_runtime(tmp_workspace, config, mock_subprocess)
        await rt.execute(iid, "echo hi", config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--network=slirp4netns" in cmd


async def test_execute_command_dns_none_when_slirp4netns(
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(network_allow=["pypi.org"])
    with patch("src.sandbox.runtime.podman._resolve_host", return_value="1.2.3.4"):
        rt, iid = await _provisioned_runtime(tmp_workspace, config, mock_subprocess)
        await rt.execute(iid, "echo hi", config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--dns=none" in cmd


async def test_execute_command_add_host_for_resolved_domain(
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(network_allow=["pypi.org"])
    with patch("src.sandbox.runtime.podman._resolve_host", return_value="1.2.3.4"):
        rt, iid = await _provisioned_runtime(tmp_workspace, config, mock_subprocess)
        await rt.execute(iid, "echo hi", config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--add-host=pypi.org:1.2.3.4" in cmd


async def test_execute_command_includes_security_opt(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.execute(iid, "echo hi", default_config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--security-opt" in cmd
    sec_idx = cmd.index("--security-opt")
    assert cmd[sec_idx + 1] == "no-new-privileges"


async def test_execute_command_includes_workdir(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.execute(iid, "echo hi", default_config)
    cmd = mock_subprocess.call_args[0][0]
    assert "--workdir" in cmd
    wd_idx = cmd.index("--workdir")
    assert cmd[wd_idx + 1] == "/workspace"


async def test_execute_timeout_passed_to_subprocess(
    tmp_workspace: Path,
    mock_subprocess,
) -> None:
    config = SandboxConfig(timeout_seconds=300)
    rt, iid = await _provisioned_runtime(tmp_workspace, config, mock_subprocess)
    await rt.execute(iid, "echo hi", config)
    assert mock_subprocess.call_args.kwargs.get("timeout") == 300


async def test_execute_calls_audit_record(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    mock_audit = MagicMock(spec=AuditLogger)
    rt = PodmanRuntime(audit_logger=mock_audit)
    iid = await rt.provision(tmp_workspace, default_config)
    await rt.execute(iid, "echo hi", default_config)
    events = [c.args[0]["event"] for c in mock_audit.record.call_args_list]
    assert "execute" in events


# ---------------------------------------------------------------------------
# Group 5 — destroy() tests
# ---------------------------------------------------------------------------


async def test_destroy_calls_podman_rm(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.destroy(iid)
    last_cmd = mock_subprocess.call_args[0][0]
    assert last_cmd[0] == "podman"
    assert last_cmd[1] == "rm"
    assert last_cmd[2] == "-f"
    assert last_cmd[3] == iid


async def test_destroy_removes_from_internal_state(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    assert iid in rt._instances
    await rt.destroy(iid)
    assert iid not in rt._instances


async def test_destroy_idempotent_on_unknown_id(
    runtime: PodmanRuntime,
    mock_subprocess,
) -> None:
    await runtime.destroy("ss-doesnotexist")


async def test_destroy_idempotent_when_rm_fails(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    mock_subprocess.return_value = _ok_result(returncode=125)
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.destroy(iid)


async def test_destroy_calls_audit_record(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    mock_audit = MagicMock(spec=AuditLogger)
    rt = PodmanRuntime(audit_logger=mock_audit)
    iid = await rt.provision(tmp_workspace, default_config)
    mock_audit.reset_mock()
    await rt.destroy(iid)
    events = [c.args[0]["event"] for c in mock_audit.record.call_args_list]
    assert "destroy" in events


# ---------------------------------------------------------------------------
# Group 8 — Port Exposure
# ---------------------------------------------------------------------------


async def test_port_flags_in_execute_cmd(
    tmp_workspace: Path,
    mock_subprocess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When config has ports, the podman cmd includes -p 127.0.0.1:HOST:CONTAINER."""
    monkeypatch.setattr(
        "src.sandbox.runtime.podman._check_port_available", lambda port: None
    )
    # Popen mock for streaming path
    mock_popen = MagicMock()
    mock_popen.stdout = iter([])
    mock_popen.returncode = 0
    mock_popen.wait.return_value = 0
    monkeypatch.setattr(
        "src.sandbox.runtime.podman.subprocess.Popen", lambda *a, **kw: mock_popen
    )
    captured_cmds: list[list[str]] = []

    original_popen = lambda *a, **kw: mock_popen  # noqa: E731

    def capturing_popen(cmd, **kw):  # type: ignore[no-untyped-def]
        captured_cmds.append(list(cmd))
        return mock_popen

    monkeypatch.setattr("src.sandbox.runtime.podman.subprocess.Popen", capturing_popen)

    config = SandboxConfig(ports=[(8000, 8000)])
    rt = PodmanRuntime()
    iid = await rt.provision(tmp_workspace, config)
    await rt.execute(iid, "uvicorn app:app", config)

    assert captured_cmds, "Popen was not called"
    cmd = captured_cmds[0]
    assert "-p" in cmd
    p_idx = cmd.index("-p")
    assert cmd[p_idx + 1] == "127.0.0.1:8000:8000"


async def test_port_forces_slirp4netns(
    tmp_workspace: Path,
    mock_subprocess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ports are set but network_allow is empty, still uses slirp4netns."""
    monkeypatch.setattr(
        "src.sandbox.runtime.podman._check_port_available", lambda port: None
    )
    mock_popen = MagicMock()
    mock_popen.stdout = iter([])
    mock_popen.returncode = 0
    mock_popen.wait.return_value = 0
    captured_cmds: list[list[str]] = []

    def capturing_popen(cmd, **kw):  # type: ignore[no-untyped-def]
        captured_cmds.append(list(cmd))
        return mock_popen

    monkeypatch.setattr("src.sandbox.runtime.podman.subprocess.Popen", capturing_popen)

    config = SandboxConfig(ports=[(8000, 8000)])  # no network_allow
    rt = PodmanRuntime()
    iid = await rt.provision(tmp_workspace, config)
    await rt.execute(iid, "server", config)

    assert captured_cmds
    cmd = captured_cmds[0]
    assert "--network=slirp4netns" in cmd
    assert "--network=none" not in cmd


async def test_port_check_called_on_provision(
    tmp_workspace: Path,
    mock_subprocess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_check_port_available is called once for each requested host port."""
    called_with: list[int] = []

    def mock_check(port: int) -> None:
        called_with.append(port)

    monkeypatch.setattr(
        "src.sandbox.runtime.podman._check_port_available", mock_check
    )

    config = SandboxConfig(ports=[(8000, 8000), (3000, 3000)])
    rt = PodmanRuntime()
    await rt.provision(tmp_workspace, config)

    assert 8000 in called_with
    assert 3000 in called_with


async def test_port_conflict_raises(
    tmp_workspace: Path,
    mock_subprocess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _check_port_available raises OSError, provision() propagates it."""

    def raise_oserror(port: int) -> None:
        raise OSError(f"Port {port} is already in use on 127.0.0.1.")

    monkeypatch.setattr(
        "src.sandbox.runtime.podman._check_port_available", raise_oserror
    )

    config = SandboxConfig(ports=[(8000, 8000)])
    rt = PodmanRuntime()

    with pytest.raises(OSError, match="already in use"):
        await rt.provision(tmp_workspace, config)


async def test_no_port_flags_when_not_configured(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    """When no ports are configured, there is no -p flag in the cmd."""
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    await rt.execute(iid, "echo hi", default_config)
    cmd = mock_subprocess.call_args[0][0]
    assert "-p" not in cmd


async def test_port_bind_is_localhost_only(
    tmp_workspace: Path,
    mock_subprocess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every -p flag value starts with 127.0.0.1: (never 0.0.0.0)."""
    monkeypatch.setattr(
        "src.sandbox.runtime.podman._check_port_available", lambda port: None
    )
    captured_cmds: list[list[str]] = []
    mock_popen = MagicMock()
    mock_popen.stdout = iter([])
    mock_popen.returncode = 0
    mock_popen.wait.return_value = 0

    def capturing_popen(cmd, **kw):  # type: ignore[no-untyped-def]
        captured_cmds.append(list(cmd))
        return mock_popen

    monkeypatch.setattr("src.sandbox.runtime.podman.subprocess.Popen", capturing_popen)

    config = SandboxConfig(ports=[(8000, 8080), (3000, 3000)])
    rt = PodmanRuntime()
    iid = await rt.provision(tmp_workspace, config)
    await rt.execute(iid, "server", config)

    assert captured_cmds
    cmd = captured_cmds[0]
    for i, arg in enumerate(cmd):
        if arg == "-p":
            binding = cmd[i + 1]
            assert binding.startswith("127.0.0.1:"), (
                f"Port binding must use 127.0.0.1, got: {binding!r}"
            )


async def test_streaming_execute_when_ports_set(
    tmp_workspace: Path,
    mock_subprocess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ports, execute() uses Popen (streaming); stdout is '<streamed to terminal>'."""
    monkeypatch.setattr(
        "src.sandbox.runtime.podman._check_port_available", lambda port: None
    )
    mock_popen = MagicMock()
    mock_popen.stdout = iter([b"line1\n", b"line2\n"])
    mock_popen.returncode = 0
    mock_popen.wait.return_value = 0
    monkeypatch.setattr(
        "src.sandbox.runtime.podman.subprocess.Popen", lambda *a, **kw: mock_popen
    )

    config = SandboxConfig(ports=[(8000, 8000)])
    rt = PodmanRuntime()
    iid = await rt.provision(tmp_workspace, config)
    result = await rt.execute(iid, "uvicorn app:app", config)

    assert result.exit_code == 0
    assert result.stdout == "<streamed to terminal>"
    assert result.stderr == ""
    # subprocess.run (mock_subprocess) must NOT have been called for execute
    # (provision with no network_allow also doesn't call it).
    mock_subprocess.assert_not_called()


async def test_capture_execute_when_no_ports(
    tmp_workspace: Path,
    default_config: SandboxConfig,
    mock_subprocess,
) -> None:
    """Without ports, execute() uses subprocess.run (capture mode)."""
    rt, iid = await _provisioned_runtime(tmp_workspace, default_config, mock_subprocess)
    result = await rt.execute(iid, "echo hi", default_config)

    # subprocess.run must have been called (that's how mock_subprocess works)
    assert mock_subprocess.called
    # stdout comes from captured output, not streaming sentinel
    assert result.stdout != "<streamed to terminal>"
