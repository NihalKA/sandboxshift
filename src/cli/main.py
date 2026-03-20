"""SandboxShift CLI — sandboxshift run / sandboxshift audit tail."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import re
import sys
from pathlib import Path

from ..config import SandboxConfig
from ..config_loader import load_workspace_config
from ..observability.audit import AuditLogger
from ..sandbox.burst.engine import BurstEngine
from ..sandbox.detection.sensitivity import SensitivityScanner
from ..sandbox.manager import RunResult, SandboxManager, SensitivityBlockedError
from ..sandbox.runtime.fargate import FargateRuntime
from ..sandbox.runtime.podman import PodmanRuntime

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_AUDIT_LOG: Path = Path.home() / ".sandboxshift" / "audit.log"

_SENSITIVE_ROOTS: tuple[Path, ...] = (
    Path.home() / ".aws",
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/root"),
)

_FARGATE_ENV_VARS: list[str] = [
    "FARGATE_CLUSTER_ARN",
    "FARGATE_TASK_DEFINITION_ARN",
    "FARGATE_SUBNET_IDS",
    "FARGATE_SECURITY_GROUP_IDS",
    "FARGATE_LOG_GROUP",
    "FARGATE_REGION",
]

# FQDN regex: requires at least two labels separated by dots.
_FQDN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+$",
    re.IGNORECASE,
)

_MEMORY_MB_MIN = 128
_MEMORY_MB_MAX = 65536
_CPU_MIN = 0.25
_CPU_MAX = 64.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_workspace(workspace_str: str) -> Path:
    """Validate workspace path: must exist and must not be a sensitive root."""
    p = Path(workspace_str).expanduser()
    if not p.exists():
        print(f"Error: workspace does not exist: {workspace_str!r}", file=sys.stderr)
        sys.exit(1)
    resolved = p.resolve()
    for sensitive in _SENSITIVE_ROOTS:
        try:
            sensitive_resolved = sensitive.resolve()
            if resolved == sensitive_resolved or sensitive_resolved in resolved.parents:
                print(
                    f"Error: workspace {resolved!r} is inside a protected directory"
                    f" ({sensitive_resolved}). Refusing to run.",
                    file=sys.stderr,
                )
                sys.exit(1)
        except Exception:  # noqa: BLE001
            pass
    return resolved


def _validate_allow_hosts(hosts: list[str]) -> None:
    """Reject bare IP addresses and non-FQDN values in --allow.

    Mirrors the validation in src/api/models.py to protect CLI users who
    bypass the API layer. Prevents SSRF against AWS IMDS (169.254.169.254)
    and internal services reachable from the sandbox network.
    """
    for host in hosts:
        # Reject bare IP addresses outright (IPv4 and IPv6).
        try:
            ipaddress.ip_address(host)
            print(
                f"Error: --allow does not accept IP addresses: {host!r}. Use FQDNs only.",
                file=sys.stderr,
            )
            sys.exit(1)
        except ValueError:
            pass
        # Require a valid FQDN (at least two dot-separated labels).
        if not _FQDN_RE.match(host):
            print(
                f"Error: --allow requires a valid fully-qualified domain name: {host!r}",
                file=sys.stderr,
            )
            sys.exit(1)


def _parse_port(port_str: str) -> tuple[int, int]:
    """Parse 'HOST:CONTAINER' string. Raises ValueError on invalid input."""
    parts = port_str.split(":")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid port format '{port_str}': expected HOST:CONTAINER (e.g. 8000:8000)"
        )
    try:
        host, container = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Port numbers in '{port_str}' must be integers")
    if not (1 <= host <= 65535):
        raise ValueError(f"Host port {host} out of valid range (1-65535)")
    if not (1 <= container <= 65535):
        raise ValueError(f"Container port {container} out of valid range (1-65535)")
    return (host, container)


def _build_fargate_runtime(audit_logger: AuditLogger) -> FargateRuntime | None:
    """Build FargateRuntime from environment variables, or return None if any are missing."""
    values = {k: os.environ.get(k, "").strip() for k in _FARGATE_ENV_VARS}
    if any(v == "" for v in values.values()):
        return None
    return FargateRuntime(
        cluster_arn=values["FARGATE_CLUSTER_ARN"],
        task_def_arn=values["FARGATE_TASK_DEFINITION_ARN"],
        subnet_ids=values["FARGATE_SUBNET_IDS"].split(","),
        security_group_ids=values["FARGATE_SECURITY_GROUP_IDS"].split(","),
        region=values["FARGATE_REGION"],
        log_group=values["FARGATE_LOG_GROUP"],
        audit_logger=audit_logger,
    )


def _resolve_audit_log(args: argparse.Namespace) -> Path:
    """Resolve audit log path: --audit-log arg → env var → default."""
    if args.audit_log:
        return Path(args.audit_log).expanduser()
    env_val = os.environ.get("SANDBOXSHIFT_AUDIT_LOG", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    return _DEFAULT_AUDIT_LOG


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def _run_async(args: argparse.Namespace, workspace: Path) -> RunResult:
    # Load YAML config from workspace (returns {} if sandboxshift.yaml absent).
    yaml_cfg = load_workspace_config(Path(args.workspace))

    # Parse CLI --port flags; exit(1) on invalid input.
    parsed_ports: list[tuple[int, int]] = []
    for port_str in (args.ports or []):
        try:
            parsed_ports.append(_parse_port(port_str))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Combine YAML ports with CLI ports (YAML first, then CLI), deduped.
    yaml_ports = yaml_cfg.get("ports", [])
    all_ports: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for p in [*yaml_ports, *parsed_ports]:
        pt = (int(p[0]), int(p[1]))
        if pt not in seen:
            seen.add(pt)
            all_ports.append(pt)

    # setup_command: CLI --setup wins over YAML.
    effective_setup = args.setup if args.setup is not None else yaml_cfg.get("setup_command")
    # network_allow: CLI --allow wins over YAML.
    effective_allow = list(args.allow) if args.allow else yaml_cfg.get("network_allow", [])
    # skip_sensitivity_check: CLI --skip-sensitivity-check wins over YAML.
    skip_scan = args.skip_sensitivity_check or yaml_cfg.get("skip_sensitivity_check", False)

    config = SandboxConfig(
        cpu_limit=args.cpu,
        memory_limit_mb=args.memory_mb,
        network_allow=effective_allow,
        timeout_seconds=args.timeout,
        setup_command=effective_setup,
        ports=all_ports,
        skip_sensitivity_check=skip_scan,
    )
    audit_log_path = _resolve_audit_log(args)
    audit_logger = AuditLogger(log_path=audit_log_path)
    burst_engine = BurstEngine(ram_threshold_gb=args.ram_threshold)
    local_runtime = PodmanRuntime(audit_logger=audit_logger)
    cloud_runtime = _build_fargate_runtime(audit_logger)
    scanner = SensitivityScanner()
    manager = SandboxManager(
        local_runtime=local_runtime,
        cloud_runtime=cloud_runtime,
        burst_engine=burst_engine,
        scanner=scanner,
        audit_logger=audit_logger,
    )
    return await manager.run(workspace=workspace, task=args.task, config=config)


def _cmd_run(args: argparse.Namespace) -> None:
    # Validate workspace path.
    workspace = _validate_workspace(args.workspace)

    # Validate --memory-mb bounds.
    if not (_MEMORY_MB_MIN <= args.memory_mb <= _MEMORY_MB_MAX):
        print(
            f"Error: --memory-mb must be between {_MEMORY_MB_MIN} and {_MEMORY_MB_MAX}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate --cpu bounds.
    if not (_CPU_MIN <= args.cpu <= _CPU_MAX):
        print(
            f"Error: --cpu must be between {_CPU_MIN} and {_CPU_MAX}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate --allow FQDN-only (rejects bare IPs including IMDS 169.254.169.254).
    if args.allow:
        _validate_allow_hosts(args.allow)

    try:
        result = asyncio.run(_run_async(args, workspace))
    except SensitivityBlockedError as exc:
        for reason in exc.findings:
            print(f"[sensitive] {reason}", file=sys.stderr)
        print(
            "\nBlocked: workspace contains sensitive data.",
            file=sys.stderr,
        )
        print(
            "To run anyway: sandboxshift run ... --skip-sensitivity-check",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Runtime: {result.runtime_mode}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    print(f"Exit code: {result.task_result.exit_code}")
    if result.task_result.stdout.strip():
        print()
        print(result.task_result.stdout, end="")
    if result.task_result.stderr.strip():
        print(result.task_result.stderr, end="", file=sys.stderr)
    for reason in result.sensitivity_reasons:
        print(f"[sensitive] {reason}")
    sys.exit(result.task_result.exit_code)


def _cmd_audit_tail(args: argparse.Namespace) -> None:
    # Resolve log path: --log arg → SANDBOXSHIFT_AUDIT_LOG env var → default
    if args.log:
        log_path = Path(args.log).expanduser()
    else:
        env_val = os.environ.get("SANDBOXSHIFT_AUDIT_LOG", "").strip()
        log_path = Path(env_val).expanduser() if env_val else _DEFAULT_AUDIT_LOG
    if not log_path.exists():
        print(f"No audit log found at {log_path}")
        return
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return
    lines = [line for line in text.splitlines() if line.strip()]
    lines = lines[-args.n :]
    for line in lines:
        try:
            entry = json.loads(line)
            print(json.dumps(entry, indent=2))
        except json.JSONDecodeError:
            pass


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sandboxshift",
        description="Self-hosted AI agent sandbox with automatic local/cloud bursting.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # --- run ---
    run_p = subparsers.add_parser("run", help="Run a task inside a sandbox.")
    run_p.add_argument("workspace", help="Path to workspace directory.")
    run_p.add_argument("task", help="Shell command to run inside the sandbox.")
    run_p.add_argument("--mode", choices=["local", "cloud", "auto"], default="auto")
    run_p.add_argument("--timeout", type=int, default=1800, metavar="SECONDS")
    run_p.add_argument("--memory-mb", type=int, default=4096, dest="memory_mb")
    run_p.add_argument("--cpu", type=float, default=2.0)
    run_p.add_argument("--allow", nargs="*", metavar="FQDN", default=None)
    run_p.add_argument("--audit-log", default=None, dest="audit_log")
    run_p.add_argument("--ram-threshold", type=float, default=4.0, dest="ram_threshold")
    run_p.add_argument(
        "--setup",
        default=None,
        metavar="CMD",
        dest="setup",
        help="Shell command to run before the main task (e.g. 'pip install -r requirements.txt').",
    )
    run_p.add_argument(
        "--port",
        metavar="HOST:CONTAINER",
        action="append",
        dest="ports",
        default=[],
        help=(
            "Expose container port to host as HOST:CONTAINER (e.g. --port 8000:8000)."
            " Repeatable."
        ),
    )
    run_p.add_argument(
        "--skip-sensitivity-check",
        action="store_true",
        default=False,
        dest="skip_sensitivity_check",
        help=(
            "Skip the sensitive-data scan. Use only for workspaces you own and trust. "
            "WARNING: disables Security Layer 6."
        ),
    )

    # --- audit ---
    audit_p = subparsers.add_parser("audit", help="Work with audit logs.")
    audit_sub = audit_p.add_subparsers(dest="audit_command", metavar="SUBCOMMAND")
    tail_p = audit_sub.add_parser("tail", help="Show the last N audit log entries.")
    tail_p.add_argument("--n", type=int, default=100, metavar="N")
    tail_p.add_argument("--log", default=None, metavar="PATH")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "run":
        _cmd_run(args)
    elif args.command == "audit":
        if getattr(args, "audit_command", None) == "tail":
            _cmd_audit_tail(args)
        else:
            parser.parse_args(["audit", "--help"])
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
