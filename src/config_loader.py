"""Workspace config loader — stub; full YAML implementation provided by config-loader agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def load_workspace_config(workspace: Path) -> dict[str, Any]:  # noqa: ARG001
    """Return configuration from sandboxshift.yaml in *workspace*.

    This stub returns an empty dict. The companion agent replaces this with
    a full YAML-parsing implementation. Tests that exercise YAML-based
    defaults mock this function directly via monkeypatch.
    """
    return {}
