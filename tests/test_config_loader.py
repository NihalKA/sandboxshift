"""Tests for config_loader.load_workspace_config.

asyncio_mode = "auto" is set in pyproject.toml; no @pytest.mark.asyncio needed.
All tests are synchronous — load_workspace_config is a pure sync function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config_loader import load_workspace_config


# ---------------------------------------------------------------------------
# Group 1 — basic loading
# ---------------------------------------------------------------------------


def test_returns_empty_when_no_yaml(tmp_path: Path) -> None:
    """Workspace with no sandboxshift.yaml returns empty dict without raising."""
    result = load_workspace_config(tmp_path)
    assert result == {}


def test_ports_parsed_to_tuples(tmp_path: Path) -> None:
    """YAML port strings are converted to (host, container) tuples."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "ports:\n  - 8000:8000\n  - 3000:3000\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("ports") == [(8000, 8000), (3000, 3000)]


def test_invalid_yaml_returns_empty(tmp_path: Path) -> None:
    """Malformed YAML file returns empty dict — no exception raised."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "key: {this: is: broken\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result == {}


def test_network_allow_loaded(tmp_path: Path) -> None:
    """network.allow list is returned under network_allow key."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "network:\n  allow:\n    - pypi.org\n    - api.github.com\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("network_allow") == ["pypi.org", "api.github.com"]


def test_resources_cpu_and_memory_mb(tmp_path: Path) -> None:
    """resources.cpu and resources.memory integers are normalised correctly."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "resources:\n  cpu: 4\n  memory: 8192\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("cpu_limit") == pytest.approx(4.0)
    assert result.get("memory_limit_mb") == 8192


def test_resources_memory_gb_string(tmp_path: Path) -> None:
    """resources.memory '4GB' string is converted to 4096 MB."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "resources:\n  memory: 4GB\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("memory_limit_mb") == 4096


def test_resources_memory_mb_string(tmp_path: Path) -> None:
    """resources.memory '2048MB' string is parsed correctly."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "resources:\n  memory: 2048MB\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("memory_limit_mb") == 2048


def test_sandbox_timeout_and_setup(tmp_path: Path) -> None:
    """sandbox.timeout → timeout_seconds; sandbox.setup → setup_command."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "sandbox:\n  timeout: 600\n  setup: uv sync\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    assert result.get("timeout_seconds") == 600
    assert result.get("setup_command") == "uv sync"


def test_invalid_port_string_skipped_silently(tmp_path: Path) -> None:
    """Invalid port strings are skipped silently; valid ports are still returned."""
    (tmp_path / "sandboxshift.yaml").write_text(
        "ports:\n  - notaport\n  - 8000:8000\n",
        encoding="utf-8",
    )
    result = load_workspace_config(tmp_path)
    # "notaport" has no colon so it's skipped; "8000:8000" is valid
    assert result.get("ports") == [(8000, 8000)]


def test_empty_yaml_returns_empty_dict(tmp_path: Path) -> None:
    """Empty sandboxshift.yaml (or YAML null document) returns empty dict."""
    (tmp_path / "sandboxshift.yaml").write_text("", encoding="utf-8")
    result = load_workspace_config(tmp_path)
    assert result == {}
