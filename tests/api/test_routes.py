"""Tests for SandboxShift FastAPI routes — 26 tests across 6 groups.

Conventions:
  - asyncio_mode = "auto" is set in pyproject.toml; @pytest.mark.asyncio is NEVER used.
  - All async fixtures use @pytest_asyncio.fixture.
  - PodmanRuntime and SensitivityScanner are patched to avoid real subprocess calls.
  - After lifespan fires, app.state.manager is replaced with a MagicMock.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from sandboxshift.api.app import create_app
from sandboxshift.sandbox.manager import RunResult
from sandboxshift.sandbox.runtime.base import TaskResult


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_run_result(
    exit_code: int = 0,
    stdout: str = "hello",
    stderr: str = "",
    task_duration: float = 0.5,
    runtime_mode: str = "local",
    sensitivity_reasons: list[str] | None = None,
    burst_confidence: str = "preferred",
    duration_seconds: float = 1.23,
) -> RunResult:
    return RunResult(
        task_result=TaskResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=task_duration,
        ),
        runtime_mode=runtime_mode,
        sensitivity_reasons=sensitivity_reasons if sensitivity_reasons is not None else [],
        burst_confidence=burst_confidence,
        duration_seconds=duration_seconds,
    )


# ---------------------------------------------------------------------------
# Primary fixture — yields (AsyncClient, mock_manager, app)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path):
    """Fixture that returns (AsyncClient, mock_manager, app) with a mocked SandboxManager."""
    app = create_app()
    with (
        patch("sandboxshift.api.app.PodmanRuntime", MagicMock()),
        patch("sandboxshift.api.app.SensitivityScanner", MagicMock()),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            mock_manager = MagicMock()
            mock_manager.run = AsyncMock(return_value=_make_run_result())
            app.state.manager = mock_manager
            app.state.audit_log_path = tmp_path / "audit.log"
            yield ac, mock_manager, app


# ---------------------------------------------------------------------------
# Group 1: POST /run happy path
# ---------------------------------------------------------------------------


async def test_run_local_mode_returns_200(client, tmp_path):
    ac, _, _ = client
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo hello"})
    assert response.status_code == 200
    assert response.json()["runtime_mode"] == "local"


async def test_run_cloud_mode_returns_200(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(return_value=_make_run_result(runtime_mode="cloud"))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo hello"})
    assert response.status_code == 200
    assert response.json()["runtime_mode"] == "cloud"


async def test_run_all_response_fields_present(client, tmp_path):
    ac, _, _ = client
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo hello"})
    assert response.status_code == 200
    data = response.json()
    expected_keys = {
        "exit_code",
        "stdout",
        "stderr",
        "runtime_mode",
        "sensitivity_reasons",
        "burst_confidence",
        "duration_seconds",
    }
    assert expected_keys <= set(data.keys())


async def test_run_response_exit_code_mapped(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(return_value=_make_run_result(exit_code=42))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "exit 42"})
    assert response.status_code == 200
    assert response.json()["exit_code"] == 42


async def test_run_response_stdout_mapped(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(return_value=_make_run_result(stdout="test output"))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo test output"})
    assert response.status_code == 200
    assert response.json()["stdout"] == "test output"


async def test_run_response_sensitivity_reasons_mapped(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(
        return_value=_make_run_result(sensitivity_reasons=["[file_pattern] .env"])
    )
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo hello"})
    assert response.status_code == 200
    assert response.json()["sensitivity_reasons"] == ["[file_pattern] .env"]


async def test_run_response_burst_confidence_mapped(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(return_value=_make_run_result(burst_confidence="forced"))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo hello"})
    assert response.status_code == 200
    assert response.json()["burst_confidence"] == "forced"


async def test_run_response_duration_seconds_mapped(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(return_value=_make_run_result(duration_seconds=5.5))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "sleep 5"})
    assert response.status_code == 200
    assert response.json()["duration_seconds"] == pytest.approx(5.5)


async def test_run_nonzero_exit_code_still_200(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(return_value=_make_run_result(exit_code=1))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "exit 1"})
    assert response.status_code == 200
    assert response.json()["exit_code"] == 1


async def test_run_config_overrides_sent_to_manager(client, tmp_path):
    ac, mock_manager, _ = client
    payload = {
        "workspace": str(tmp_path),
        "task": "echo hello",
        "timeout": 60,
        "memory_mb": 2048,
        "cpu": 1.0,
        "allowed_hosts": ["pypi.org"],
    }
    response = await ac.post("/run", json=payload)
    assert response.status_code == 200
    call_kwargs = mock_manager.run.call_args.kwargs
    config = call_kwargs["config"]
    assert config.timeout_seconds == 60
    assert config.memory_limit_mb == 2048
    assert config.cpu_limit == pytest.approx(1.0)
    assert config.network_allow == ["pypi.org"]


# ---------------------------------------------------------------------------
# Group 2: POST /run validation errors
# ---------------------------------------------------------------------------


async def test_run_missing_task_returns_422(client, tmp_path):
    ac, _, _ = client
    response = await ac.post("/run", json={"workspace": str(tmp_path)})
    assert response.status_code == 422


async def test_run_missing_workspace_returns_422(client):
    ac, _, _ = client
    response = await ac.post("/run", json={"task": "echo x"})
    assert response.status_code == 422


async def test_run_nonexistent_workspace_returns_422(client):
    ac, _, _ = client
    response = await ac.post(
        "/run",
        json={"workspace": "/this/path/does/not/exist/at/all/ever", "task": "echo x"},
    )
    assert response.status_code == 422


async def test_run_invalid_mode_returns_422(client, tmp_path):
    ac, _, _ = client
    response = await ac.post(
        "/run",
        json={"workspace": str(tmp_path), "task": "echo x", "mode": "kubernetes"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Group 3: POST /run exception → 500
# ---------------------------------------------------------------------------


async def test_run_manager_raises_runtime_error_returns_500(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(side_effect=RuntimeError("podman failed"))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo hello"})
    assert response.status_code == 500


async def test_run_manager_raises_value_error_returns_500(client, tmp_path):
    ac, mock_manager, _ = client
    mock_manager.run = AsyncMock(side_effect=ValueError("config invalid"))
    response = await ac.post("/run", json={"workspace": str(tmp_path), "task": "echo hello"})
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# Group 4: GET /health
# ---------------------------------------------------------------------------


async def test_health_always_200(client):
    ac, _, _ = client
    response = await ac.get("/health")
    assert response.status_code == 200


async def test_health_returns_ok_status(client):
    ac, _, _ = client
    response = await ac.get("/health")
    assert response.json()["status"] == "ok"


async def test_health_returns_version_string(client):
    ac, _, _ = client
    response = await ac.get("/health")
    data = response.json()
    assert "version" in data
    assert isinstance(data["version"], str)
    assert len(data["version"]) > 0


# ---------------------------------------------------------------------------
# Group 5: GET /audit
# ---------------------------------------------------------------------------


async def test_audit_file_missing_returns_empty_list(client):
    ac, _, app = client
    # Point audit_log_path to a file that does not exist
    app.state.audit_log_path = Path("/tmp/sandboxshift_test_nonexistent_audit_xyz.log")
    response = await ac.get("/audit")
    assert response.status_code == 200
    assert response.json() == []


async def test_audit_returns_last_n_entries(client, tmp_path):
    ac, _, app = client
    audit_path = tmp_path / "audit.log"
    app.state.audit_log_path = audit_path
    lines = [
        {"event": f"ev_{i}", "ts": f"2026-03-14T00:00:0{i}Z", "session": "abc"}
        for i in range(5)
    ]
    audit_path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    response = await ac.get("/audit?n=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["event"] == "ev_2"
    assert data[1]["event"] == "ev_3"
    assert data[2]["event"] == "ev_4"


async def test_audit_default_n_is_100(client, tmp_path):
    ac, _, app = client
    audit_path = tmp_path / "audit.log"
    app.state.audit_log_path = audit_path
    lines = [
        {"event": f"ev_{i}", "ts": "2026-03-14T00:00:00Z", "session": "abc"}
        for i in range(150)
    ]
    audit_path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    response = await ac.get("/audit")
    assert response.status_code == 200
    assert len(response.json()) == 100


async def test_audit_n_larger_than_file_returns_all(client, tmp_path):
    ac, _, app = client
    audit_path = tmp_path / "audit.log"
    app.state.audit_log_path = audit_path
    lines = [
        {"event": f"ev_{i}", "ts": "2026-03-14T00:00:00Z", "session": "abc"}
        for i in range(5)
    ]
    audit_path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    response = await ac.get("/audit?n=100")
    assert response.status_code == 200
    assert len(response.json()) == 5


async def test_audit_invalid_json_lines_skipped(client, tmp_path):
    ac, _, app = client
    audit_path = tmp_path / "audit.log"
    app.state.audit_log_path = audit_path
    valid_line = json.dumps({"event": "ev_ok", "ts": "2026-03-14T00:00:00Z", "session": "abc"})
    invalid_line = "this is not json {{{"
    # 3 valid + 1 invalid + 1 valid = 5 lines; only 4 are parseable
    content = "\n".join(
        [
            valid_line,
            valid_line,
            valid_line,
            invalid_line,
            valid_line,
        ]
    ) + "\n"
    audit_path.write_text(content, encoding="utf-8")
    response = await ac.get("/audit?n=100")
    assert response.status_code == 200
    assert len(response.json()) == 4


# ---------------------------------------------------------------------------
# Group 6: Startup wiring
#
# ASGITransport does NOT send ASGI lifespan events, so app.state is never
# populated by the lifespan context manager. These tests call
# _build_fargate_runtime() directly — the correct unit test boundary for
# the env-var → FargateRuntime wiring logic.
# ---------------------------------------------------------------------------


def test_fargate_skipped_when_env_vars_missing(monkeypatch):
    """When any FARGATE_* env var is blank/missing, _build_fargate_runtime returns None."""
    from sandboxshift.api.app import _build_fargate_runtime

    for var in [
        "FARGATE_CLUSTER_ARN",
        "FARGATE_TASK_DEFINITION_ARN",
        "FARGATE_SUBNET_IDS",
        "FARGATE_SECURITY_GROUP_IDS",
        "FARGATE_LOG_GROUP",
        "FARGATE_REGION",
    ]:
        monkeypatch.setenv(var, "")
    result = _build_fargate_runtime(MagicMock())
    assert result is None


def test_fargate_wired_when_all_env_vars_present(monkeypatch):
    """When all FARGATE_* env vars are set, _build_fargate_runtime returns a FargateRuntime."""
    from sandboxshift.api.app import _build_fargate_runtime

    monkeypatch.setenv("FARGATE_CLUSTER_ARN", "arn:aws:ecs:us-east-1:123456789012:cluster/test")
    monkeypatch.setenv(
        "FARGATE_TASK_DEFINITION_ARN",
        "arn:aws:ecs:us-east-1:123456789012:task-definition/test:1",
    )
    monkeypatch.setenv("FARGATE_SUBNET_IDS", "subnet-12345")
    monkeypatch.setenv("FARGATE_SECURITY_GROUP_IDS", "sg-12345")
    monkeypatch.setenv("FARGATE_LOG_GROUP", "/sandboxshift/test")
    monkeypatch.setenv("FARGATE_REGION", "us-east-1")
    with patch("sandboxshift.api.app.FargateRuntime") as mock_fargate:
        mock_fargate.return_value = MagicMock()
        result = _build_fargate_runtime(MagicMock())
    assert result is not None
    mock_fargate.assert_called_once()
