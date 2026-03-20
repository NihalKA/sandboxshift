"""Load sandboxshift.yaml from a workspace directory."""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

try:
    import yaml  # pyyaml

    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False


def load_workspace_config(workspace_path: Path) -> dict[str, Any]:
    """Load sandboxshift.yaml from *workspace_path*.

    Returns a normalised dict suitable for merging into CLI argument defaults.
    Returns ``{}`` if the file does not exist, pyyaml is unavailable, or the
    file contains invalid YAML.  Never raises.

    Supported YAML structure::

        sandbox:
          timeout: 1800             # seconds
          setup: "uv sync"          # shell command
          skip_sensitivity_check: true  # skip scan for trusted workspaces

        workspace:
          readonly: true            # mount workspace read-only inside container

        network:
          allow:
            - pypi.org

        resources:
          cpu: 2
          memory: 4096              # MB, or "4GB" / "4096MB"

        ports:
          - 8000:8000
          - 3000:3000

    sensitivity:
      level: auto                   # reserved for V2; ignored in V1
    """
    config_file = workspace_path / "sandboxshift.yaml"
    if not config_file.exists():
        return {}
    if not _YAML_AVAILABLE:  # pragma: no cover
        warnings.warn(
            "pyyaml is not installed — sandboxshift.yaml ignored. "
            "Install it with: pip install pyyaml",
            stacklevel=2,
        )
        return {}
    try:
        with open(config_file) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        warnings.warn(
            f"sandboxshift.yaml in {workspace_path} is invalid — ignoring",
            stacklevel=2,
        )
        return {}

    result: dict[str, Any] = {}

    # ── sandbox section ──────────────────────────────────────────────────────
    sandbox = data.get("sandbox") or {}
    if "timeout" in sandbox:
        result["timeout_seconds"] = int(sandbox["timeout"])
    if "setup" in sandbox:
        result["setup_command"] = str(sandbox["setup"])
    if sandbox.get("skip_sensitivity_check"):
        result["skip_sensitivity_check"] = True

    # ── workspace section ────────────────────────────────────────────────────
    workspace = data.get("workspace") or {}
    if "readonly" in workspace:
        result["workspace_readonly"] = bool(workspace["readonly"])

    # ── network section ──────────────────────────────────────────────────────
    network = data.get("network") or {}
    if "allow" in network:
        result["network_allow"] = list(network["allow"])

    # ── resources section ────────────────────────────────────────────────────
    resources = data.get("resources") or {}
    if "cpu" in resources:
        result["cpu_limit"] = float(resources["cpu"])
    if "memory" in resources:
        mem = resources["memory"]
        if isinstance(mem, str) and mem.upper().endswith("GB"):
            result["memory_limit_mb"] = int(float(mem[:-2]) * 1024)
        elif isinstance(mem, str) and mem.upper().endswith("MB"):
            result["memory_limit_mb"] = int(float(mem[:-2]))
        else:
            result["memory_limit_mb"] = int(mem)

    # ── ports section ────────────────────────────────────────────────────────
    raw_ports = data.get("ports") or []
    parsed: list[tuple[int, int]] = []
    for p in raw_ports:
        parts = str(p).split(":")
        if len(parts) == 2:
            try:
                parsed.append((int(parts[0]), int(parts[1])))
            except ValueError:
                pass  # skip malformed port entries silently
    if parsed:
        result["ports"] = parsed

    return result
