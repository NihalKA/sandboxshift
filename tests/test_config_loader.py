"""Tests for config_loader.load_workspace_config.

asynio_mode = "auto" is set in pyproject.toml; no @pytest.mark.asyncio needed.
All tests are synchronous — load_workspace_config is a pure sync function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config_loader import load_workspace_config


# ---------------------------------------------------------------------------
# Group 1 — basic loading
# ---------------------------------------------------------------------------


def test_load_empty_when_no_yaml(tmp_path: Path) -> None:
    """Workspace with no sandboxshift.yaml returns empty dict without raising."""
    result = load_workspace_config(tmp_path)
    assert result == {}


def test_load_ports_parsed_correctly(tmp_path: Path) -> None:
    """YAML port strings are converted to (host, container) tuples."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "ports:\n  - 8000:8000\n  - 3000:3000\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("ports") == [(8000, 8000), (3000, 3000)]


def test_load_invalid_yaml_returns_empty(tmp_path: Path) -> None:
    """Malformed YAML file returns empty dict — no exception raised."""
    # This content is invalid YAML (nested colons without quotes).
    (tmp_path / "sandboxshift.yaml").write_text(
        "key: {this: is: broken\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result == {}


def test_load_network_allow(tmp_path: Path) -> None:
    """network.allow list is returned as network_allow."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "network:\n  allow:\n    - pypi.org\n    - api.github.com\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("network_allow") == ["pypi.org", "api.github.com"]


def test_load_resources(tmp_path: Path) -> None:
    """resources.cpu and resources.memory are normalised correctly."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "resources:\n  cpu: 4\n  memory: 8192\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("cpu_limit") == pytest.approx(4.0)
    assert result.get("memory_limit_mb") == 8192


def test_load_sandbox_timeout_and_setup(tmp_path: Path) -> None:
    """sandbox.timeout → timeout_seconds; sandbox.setup → setup_command."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "sandbox:\n  timeout: 600\n  setup: uv sync\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("timeout_seconds") == 600
    assert result.get("setup_command") == "uv sync"


def test_load_invalid_port_string_skipped(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Invalid port strings are skipped with a warning; valid ports still returned."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "ports:\n  - notaport\n  - 8000:8000\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("ports") == [(8000, 8000)]
    out = capsys.readouterr().out
    assert "Warning" in out
