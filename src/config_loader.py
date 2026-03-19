"""YAML workspace config loader for SandboxShift CLI.

Loads ``sandboxshift.yaml`` from the workspace root and returns a normalised
dict consumed by the CLI before arg merging.  The API layer does NOT use this
loader — API consumers construct ``SandboxConfig`` directly (Decision #49).

If the file is absent or malformed this module prints a warning and returns
an empty dict.  It never raises.
"""

from __future__ import annotations

from pathlib import Path

try:
    import yaml as _yaml  # type: ignore[import]
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]


def load_workspace_config(workspace_path: Path) -> dict:  # type: ignore[type-arg]
    """Load ``sandboxshift.yaml`` from *workspace_path*. Returns ``{}`` if not found.

    Parsed YAML → normalised result dict keys:
    - ``timeout_seconds``  (int)
    - ``setup_command``    (str | None)
    - ``network_allow``    (list[str])
    - ``cpu_limit``        (float)
    - ``memory_limit_mb``  (int)
    - ``ports``            (list[tuple[int, int]])

    Port strings like ``"8000:8000"`` are converted to ``(8000, 8000)`` tuples.
    Invalid port strings are skipped with a warning.

    Args:
        workspace_path: Directory that may contain ``sandboxshift.yaml``.

    Returns:
        Normalised config dict.  Only keys that were present in the YAML are
        included; absent keys are simply not in the returned dict.
    """
    config_file = workspace_path / "sandboxshift.yaml"
    if not config_file.exists():
        return {}

    if _yaml is None:  # pragma: no cover
        print(
            "Warning: pyyaml is not installed; sandboxshift.yaml will be ignored.",
            flush=True,
        )
        return {}

    try:
        raw = _yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not parse sandboxshift.yaml: {exc}", flush=True)
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict = {}  # type: ignore[type-arg]

    # --- sandbox section ---
    sandbox = raw.get("sandbox") or {}
    if isinstance(sandbox, dict):
        if "timeout" in sandbox:
            try:
                result["timeout_seconds"] = int(sandbox["timeout"])
            except (TypeError, ValueError):
                pass
        setup_val = sandbox.get("setup")
        if isinstance(setup_val, str) and setup_val:
            result["setup_command"] = setup_val

    # --- network section ---
    network = raw.get("network") or {}
    if isinstance(network, dict):
        raw_allow = network.get("allow")
        if isinstance(raw_allow, list):
            result["network_allow"] = [str(h) for h in raw_allow if h]

    # --- resources section ---
    resources = raw.get("resources") or {}
    if isinstance(resources, dict):
        if "cpu" in resources:
            try:
                result["cpu_limit"] = float(resources["cpu"])
            except (TypeError, ValueError):
                pass
        if "memory" in resources:
            try:
                result["memory_limit_mb"] = int(resources["memory"])
            except (TypeError, ValueError):
                pass

    # --- ports section ---
    raw_ports = raw.get("ports") or []
    if isinstance(raw_ports, list):
        parsed: list[tuple[int, int]] = []
        for p in raw_ports:
            p_str = str(p)
            parts = p_str.split(":")
            if len(parts) != 2:
                print(
                    f"Warning: invalid port in sandboxshift.yaml: {p_str!r}"
                    " (expected HOST:CONTAINER)",
                    flush=True,
                )
                continue
            try:
                host_port = int(parts[0])
                container_port = int(parts[1])
            except ValueError:
                print(
                    f"Warning: non-integer port in sandboxshift.yaml: {p_str!r}",
                    flush=True,
                )
                continue
            if not (1 <= host_port <= 65535) or not (1 <= container_port <= 65535):
                print(
                    f"Warning: port out of range in sandboxshift.yaml: {p_str!r}",
                    flush=True,
                )
                continue
            parsed.append((host_port, container_port))
        if parsed:
            result["ports"] = parsed

    return result
