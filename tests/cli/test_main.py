from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# importlib.import_module goes through sys.modules and returns the actual
# submodule even when sandboxshift/cli/__init__.py shadows the name with
# `from .main import main` (which sets sandboxshift.cli.main = <function>).
_cli_main_module = importlib.import_module("sandboxshift.cli.main")

from sandboxshift.cli.main import (
    _build_parser,
    _validate_workspace,
    main,
)
from sandboxshift.sandbox.manager import RunResult
from sandboxshift.sandbox.runtime.base import TaskResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task_result(exit_code: int = 0, stdout: str = "ok\n", stderr: str = "") -> TaskResult:
    return TaskResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=1.0)


def _make_run_result(
    exit_code: int = 0,
    stdout: str = "ok\n",
    runtime_mode: str = "local",
    sensitivity_reasons: list[str] | None = None,
    duration_seconds: float = 2.5,
) -> RunResult:
    return RunResult(
        task_result=_make_task_result(exit_code=exit_code, stdout=stdout),
        runtime_mode=runtime_mode,
        sensitivity_reasons=sensitivity_reasons or [],
        burst_confidence="preferred",
        duration_seconds=duration_seconds,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def mock_manager(workspace):
    """Patch SandboxManager so run() returns a default successful run result.

    Also patches load_workspace_config to return an empty dict so tests that
    exercise _run_async() don't need a real sandboxshift.yaml.
    """
    with patch("sandboxshift.cli.main.SandboxManager") as mock_cls:
        with patch("sandboxshift.cli.main.PodmanRuntime"):
            with patch("sandboxshift.cli.main.SensitivityScanner"):
                with patch(
                    "sandboxshift.cli.main.load_workspace_config",
                    return_value={},
                ):
                    instance = mock_cls.return_value
                    instance.run = AsyncMock(return_value=_make_run_result())
                    yield mock_cls, instance


# ---------------------------------------------------------------------------
# Group 1 — run happy path
# ---------------------------------------------------------------------------

def test_run_prints_runtime_mode(workspace, mock_manager, capsys):
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest"]):
        with pytest.raises(SystemExit):
            main()
    assert "Runtime: local" in capsys.readouterr().out


def test_run_prints_exit_code(workspace, mock_manager, capsys):
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest"]):
        with pytest.raises(SystemExit):
            main()
    assert "Exit code: 0" in capsys.readouterr().out


def test_run_prints_duration(workspace, mock_manager, capsys):
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest"]):
        with pytest.raises(SystemExit):
            main()
    out = capsys.readouterr().out
    assert "Duration:" in out
    assert "2.50s" in out


def test_run_prints_stdout_when_nonempty(workspace, mock_manager, capsys):
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest"]):
        with pytest.raises(SystemExit):
            main()
    assert "ok" in capsys.readouterr().out


def test_run_prints_sensitivity_reasons_when_flagged(workspace, capsys):
    flagged = _make_run_result(sensitivity_reasons=["found .env file", "found private key"])
    with patch("sandboxshift.cli.main.SandboxManager") as mock_cls, \
         patch("sandboxshift.cli.main.PodmanRuntime"), \
         patch("sandboxshift.cli.main.SensitivityScanner"), \
         patch("sandboxshift.cli.main.load_workspace_config", return_value={}):
        mock_cls.return_value.run = AsyncMock(return_value=flagged)
        with patch("sys.argv", ["sandboxshift", "run", str(workspace), "ls"]):
            with pytest.raises(SystemExit):
                main()
    out = capsys.readouterr().out
    assert "[sensitive] found .env file" in out
    assert "[sensitive] found private key" in out


def test_run_exits_with_task_exit_code(workspace, mock_manager):
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 0


def test_run_nonzero_exit_code_exits_nonzero(workspace):
    failing = _make_run_result(exit_code=2, stdout="")
    with patch("sandboxshift.cli.main.SandboxManager") as mock_cls, \
         patch("sandboxshift.cli.main.PodmanRuntime"), \
         patch("sandboxshift.cli.main.SensitivityScanner"), \
         patch("sandboxshift.cli.main.load_workspace_config", return_value={}):
        mock_cls.return_value.run = AsyncMock(return_value=failing)
        with patch("sys.argv", ["sandboxshift", "run", str(workspace), "false"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
    assert exc_info.value.code == 2


def test_run_default_mode_is_auto(workspace):
    parser = _build_parser()
    args = parser.parse_args(["run", str(workspace), "pytest"])
    assert args.mode == "auto"


# ---------------------------------------------------------------------------
# Group 2 — Validation errors
# ---------------------------------------------------------------------------

def test_run_nonexistent_workspace_exits_1(tmp_path, capsys):
    nonexistent = str(tmp_path / "does_not_exist")
    with patch("sys.argv", ["sandboxshift", "run", nonexistent, "pytest"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_run_sensitive_workspace_exits_1(tmp_path, monkeypatch, capsys):
    """Workspace inside a sensitive root must exit 1 with a 'protected' message.

    Calls _validate_workspace() directly rather than main() to avoid any
    asyncio.run() / event-loop interaction with pytest-asyncio's auto mode.
    The logic being tested belongs entirely to _validate_workspace.

    Uses importlib.import_module to get the actual submodule object rather than
    the 'main' function that __init__.py shadows sandboxshift.cli.main with.
    """
    sensitive_dir = tmp_path / "fake_aws"
    sensitive_dir.mkdir()
    # Patch the module-level tuple so only our temp dir is treated as sensitive.
    monkeypatch.setattr(_cli_main_module, "_SENSITIVE_ROOTS", (sensitive_dir,))
    with pytest.raises(SystemExit) as exc_info:
        _validate_workspace(str(sensitive_dir))
    assert exc_info.value.code == 1
    assert "protected" in capsys.readouterr().err


def test_allow_bare_ip_rejected_exits_1(workspace, capsys):
    """--allow with a bare IPv4 address must be rejected (Layer 4 SSRF guard)."""
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest",
                             "--allow", "10.0.0.1"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1
    assert "IP" in capsys.readouterr().err


def test_allow_imds_ip_rejected_exits_1(workspace, capsys):
    """--allow with IMDS address 169.254.169.254 must be rejected."""
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest",
                             "--allow", "169.254.169.254"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1
    assert "IP" in capsys.readouterr().err


def test_memory_mb_over_limit_exits_1(workspace, capsys):
    """--memory-mb exceeding 65536 must be rejected to prevent host OOM."""
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest",
                             "--memory-mb", "999999"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "memory-mb" in err


def test_cpu_over_limit_exits_1(workspace, capsys):
    """--cpu exceeding 64.0 must be rejected to prevent host resource exhaustion."""
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest",
                             "--cpu", "128.0"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "cpu" in err


# ---------------------------------------------------------------------------
# Group 3 — Config args passed correctly
# ---------------------------------------------------------------------------

def test_run_timeout_arg_passed_to_config(workspace, mock_manager):
    with patch("sandboxshift.cli.main.SandboxConfig") as mock_cfg:
        mock_cfg.return_value = MagicMock()
        with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest", "--timeout", "600"]):
            with pytest.raises(SystemExit):
                main()
    _, kwargs = mock_cfg.call_args
    assert kwargs["timeout_seconds"] == 600


def test_run_memory_mb_arg_passed_to_config(workspace, mock_manager):
    with patch("sandboxshift.cli.main.SandboxConfig") as mock_cfg:
        mock_cfg.return_value = MagicMock()
        with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest", "--memory-mb", "8192"]):
            with pytest.raises(SystemExit):
                main()
    _, kwargs = mock_cfg.call_args
    assert kwargs["memory_limit_mb"] == 8192


def test_run_cpu_arg_passed_to_config(workspace, mock_manager):
    with patch("sandboxshift.cli.main.SandboxConfig") as mock_cfg:
        mock_cfg.return_value = MagicMock()
        with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest", "--cpu", "4.0"]):
            with pytest.raises(SystemExit):
                main()
    _, kwargs = mock_cfg.call_args
    assert kwargs["cpu_limit"] == 4.0


def test_run_allow_arg_passed_to_config(workspace, mock_manager):
    with patch("sandboxshift.cli.main.SandboxConfig") as mock_cfg:
        mock_cfg.return_value = MagicMock()
        with patch("sys.argv", [
            "sandboxshift", "run", str(workspace), "pytest",
            "--allow", "pypi.org", "api.github.com",
        ]):
            with pytest.raises(SystemExit):
                main()
    _, kwargs = mock_cfg.call_args
    assert kwargs["network_allow"] == ["pypi.org", "api.github.com"]


# ---------------------------------------------------------------------------
# Group 4 — audit tail
# ---------------------------------------------------------------------------

def test_audit_tail_file_missing_prints_message(tmp_path, capsys):
    missing = str(tmp_path / "nonexistent.log")
    with patch("sys.argv", ["sandboxshift", "audit", "tail", "--log", missing]):
        main()  # must NOT raise SystemExit
    assert "No audit log found" in capsys.readouterr().out


def test_audit_tail_prints_last_n_entries(tmp_path, capsys):
    log = tmp_path / "audit.log"
    entries = [{"event": "run", "n": i} for i in range(5)]
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    with patch("sys.argv", ["sandboxshift", "audit", "tail", "--log", str(log), "--n", "2"]):
        main()
    out = capsys.readouterr().out
    assert '"n": 3' in out
    assert '"n": 4' in out
    assert '"n": 0' not in out


def test_audit_tail_default_n_is_100():
    parser = _build_parser()
    args = parser.parse_args(["audit", "tail"])
    assert args.n == 100


def test_audit_tail_invalid_json_skipped(tmp_path, capsys):
    log = tmp_path / "audit.log"
    log.write_text(
        '{"event": "run_start"}\n'
        'THIS IS NOT JSON\n'
        '{"event": "run_complete"}\n'
    )
    with patch("sys.argv", ["sandboxshift", "audit", "tail", "--log", str(log)]):
        main()
    out = capsys.readouterr().out
    assert "run_start" in out
    assert "run_complete" in out
    assert "THIS IS NOT JSON" not in out


# ---------------------------------------------------------------------------
# Group 5 — Fargate wiring
# ---------------------------------------------------------------------------

def test_fargate_skipped_when_env_vars_missing(workspace, monkeypatch, mock_manager):
    for var in [
        "FARGATE_CLUSTER_ARN", "FARGATE_TASK_DEFINITION_ARN",
        "FARGATE_SUBNET_IDS", "FARGATE_SECURITY_GROUP_IDS",
        "FARGATE_LOG_GROUP", "FARGATE_REGION",
    ]:
        monkeypatch.delenv(var, raising=False)
    mock_cls, _ = mock_manager
    with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest"]):
        with pytest.raises(SystemExit):
            main()
    _, kwargs = mock_cls.call_args
    assert kwargs["cloud_runtime"] is None


def test_fargate_wired_when_all_env_vars_present(workspace, monkeypatch, mock_manager):
    monkeypatch.setenv("FARGATE_CLUSTER_ARN", "arn:aws:ecs:us-east-1:123:cluster/c")
    monkeypatch.setenv("FARGATE_TASK_DEFINITION_ARN", "arn:aws:ecs:us-east-1:123:task-def/t:1")
    monkeypatch.setenv("FARGATE_SUBNET_IDS", "subnet-aaa,subnet-bbb")
    monkeypatch.setenv("FARGATE_SECURITY_GROUP_IDS", "sg-ccc")
    monkeypatch.setenv("FARGATE_LOG_GROUP", "/sandboxshift/logs")
    monkeypatch.setenv("FARGATE_REGION", "us-east-1")
    mock_cls, _ = mock_manager
    with patch("sandboxshift.cli.main.FargateRuntime") as mock_fargate:
        mock_fargate.return_value = MagicMock()
        with patch("sys.argv", ["sandboxshift", "run", str(workspace), "pytest"]):
            with pytest.raises(SystemExit):
                main()
    mock_fargate.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["cloud_runtime"] is mock_fargate.return_value


# ---------------------------------------------------------------------------
# Group 6 — --setup flag
# ---------------------------------------------------------------------------

def test_setup_flag_default_is_none(tmp_path: Path) -> None:
    """--setup defaults to None when not provided."""
    parser = _build_parser()
    args = parser.parse_args(["run", str(tmp_path), "echo hi"])
    assert args.setup is None


def test_setup_flag_is_parsed(tmp_path: Path) -> None:
    """--setup value is parsed correctly."""
    parser = _build_parser()
    args = parser.parse_args([
        "run", str(tmp_path), "echo hi",
        "--setup", "pip install -r requirements.txt",
    ])
    assert args.setup == "pip install -r requirements.txt"


def test_run_setup_flag_passed_to_sandbox_config(
    tmp_path: Path,
    mock_manager: tuple[type, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--setup value is wired to SandboxConfig.setup_command."""
    _, instance = mock_manager
    monkeypatch.setattr(sys, "argv", [
        "sandboxshift", "run", str(tmp_path), "pytest",
        "--setup", "uv sync",
    ])
    with pytest.raises(SystemExit):
        main()
    call_kwargs = instance.run.call_args.kwargs
    assert call_kwargs["config"].setup_command == "uv sync"


def test_run_no_setup_flag_config_setup_command_is_none(
    tmp_path: Path,
    mock_manager: tuple[type, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No --setup flag → SandboxConfig.setup_command is None."""
    _, instance = mock_manager
    monkeypatch.setattr(sys, "argv", [
        "sandboxshift", "run", str(tmp_path), "pytest",
    ])
    with pytest.raises(SystemExit):
        main()
    call_kwargs = instance.run.call_args.kwargs
    assert call_kwargs["config"].setup_command is None


# ---------------------------------------------------------------------------
# Group 7 — Port flags
# ---------------------------------------------------------------------------

def test_port_flag_adds_port_to_config(
    tmp_path: Path,
    mock_manager: tuple[type, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--port 8000:8000 wires ports=[(8000, 8000)] into SandboxConfig."""
    _, instance = mock_manager
    monkeypatch.setattr(sys, "argv", [
        "sandboxshift", "run", str(tmp_path), "python server.py",
        "--port", "8000:8000",
    ])
    with pytest.raises(SystemExit):
        main()
    call_kwargs = instance.run.call_args.kwargs
    assert call_kwargs["config"].ports == [(8000, 8000)]


def test_port_flag_repeatable(
    tmp_path: Path,
    mock_manager: tuple[type, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--port repeated twice yields both tuples in order."""
    _, instance = mock_manager
    monkeypatch.setattr(sys, "argv", [
        "sandboxshift", "run", str(tmp_path), "python server.py",
        "--port", "8000:8000",
        "--port", "3000:3000",
    ])
    with pytest.raises(SystemExit):
        main()
    call_kwargs = instance.run.call_args.kwargs
    assert call_kwargs["config"].ports == [(8000, 8000), (3000, 3000)]


def test_port_flag_invalid_format_exits_1(
    tmp_path: Path,
    mock_manager: tuple[type, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--port notaport → SystemExit(1) with error message on stderr."""
    monkeypatch.setattr(sys, "argv", [
        "sandboxshift", "run", str(tmp_path), "pytest",
        "--port", "notaport",
    ])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    assert "Error:" in capsys.readouterr().err


def test_port_flag_out_of_range_exits_1(
    tmp_path: Path,
    mock_manager: tuple[type, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--port 99999:8000 → SystemExit(1) because 99999 > 65535."""
    monkeypatch.setattr(sys, "argv", [
        "sandboxshift", "run", str(tmp_path), "pytest",
        "--port", "99999:8000",
    ])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    assert "Error:" in capsys.readouterr().err


def test_yaml_ports_loaded_when_no_cli_port(
    tmp_path: Path,
    mock_manager: tuple[type, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When load_workspace_config returns ports, they appear in SandboxConfig even with no --port flag."""
    _, instance = mock_manager
    # Use the module object (not the string path) — "sandboxshift.cli.main" resolves
    # to the main *function* due to __init__.py shadowing. Same pattern as _SENSITIVE_ROOTS.
    monkeypatch.setattr(
        _cli_main_module,
        "load_workspace_config",
        lambda _: {"ports": [(8000, 8000)]},
    )
    monkeypatch.setattr(sys, "argv", [
        "sandboxshift", "run", str(tmp_path), "pytest",
    ])
    with pytest.raises(SystemExit):
        main()
    call_kwargs = instance.run.call_args.kwargs
    assert call_kwargs["config"].ports == [(8000, 8000)]
