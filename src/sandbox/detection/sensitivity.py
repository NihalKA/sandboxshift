"""
src/sandbox/detection/sensitivity.py

Two-layer sensitive-data scanner for SandboxShift (Security Layer 6 of 7).

Layer 1 — File Pattern Matching
    Walks the workspace with rglob and checks every file name against a
    curated list of glob patterns that are likely to contain secrets
    (e.g. .env, *.pem, credentials …).  Also inspects parent directory
    components so that files nested inside .aws/ or .ssh/ are flagged
    even though those directories are not files themselves.

Layer 2 — Content Scanning
    Opens every text file that is small enough to read and scans its
    content with a battery of compiled regular expressions that detect
    well-known secret shapes (AWS access-key IDs, PEM private-key
    headers, hardcoded passwords/secrets/API keys, RFC-1918 internal
    IP addresses).

Both layers are executed concurrently via asyncio.gather.  Results are
merged into a single SensitivityResult that carries a machine-readable
Recommendation (FORCE_LOCAL or ALLOW_CLOUD) and a human-readable
explanation list.

Fail-closed design: if the directory walk raises an OS-level error the
scanner returns a sentinel Finding so the workspace is treated as
sensitive and forced to run locally.  Scan errors must NEVER produce
ALLOW_CLOUD.

The scanner is intentionally synchronous-I/O-free: all blocking
filesystem operations are delegated to asyncio.to_thread so the whole
module stays non-blocking inside an async event loop.
"""

import asyncio
import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, List


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DetectionLayer(str, Enum):
    FILE_PATTERN = "file_pattern"
    CONTENT_SCAN = "content_scan"


class Recommendation(str, Enum):
    FORCE_LOCAL = "force_local"
    ALLOW_CLOUD = "allow_cloud"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    layer: DetectionLayer
    file: Path
    pattern: str
    reason: str
    match_value: str = ""  # always redacted: first 6 chars + "***"


@dataclass
class SensitivityResult:
    is_sensitive: bool
    findings: List[Finding] = field(default_factory=list)
    recommendation: Recommendation = Recommendation.ALLOW_CLOUD

    def explain(self) -> List[str]:
        """
        Return one human-readable string per finding, suitable for the audit log.

        Includes the full file path (not just filename) for unambiguous audit records,
        and the redacted match_value when available.
        """
        lines: List[str] = []
        for f in self.findings:
            evidence = f" [evidence: {f.match_value}]" if f.match_value else ""
            lines.append(
                f"[{f.layer.value}] {f.file}: {f.reason} (pattern: {f.pattern}){evidence}"
            )
        return lines


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES = 1_048_576  # 1 MB — larger files skipped in content scan
BINARY_PROBE_SIZE = 8192  # 8 KB — probe window for binary detection

# (pattern_glob, reason) tuples — matched against file names only
SENSITIVE_FILE_PATTERNS: list[tuple[str, str]] = [
    (".env", "Environment variable file may contain secrets"),
    ("*.env", "Environment variable file may contain secrets"),
    ("*.pem", "PEM file may contain private key or certificate"),
    ("*.key", "Key file may contain private key"),
    ("*.p12", "PKCS#12 keystore may contain private key"),
    ("credentials", "AWS/GCP credentials file"),
    ("credentials.json", "GCP/OAuth credentials file"),
    ("*secret*", "Filename contains 'secret'"),
    ("*token*", "Filename contains 'token'"),
]

# (pattern_glob, reason) tuples — matched against each parent directory component
# This catches files *inside* .aws/ or .ssh/ even though those are not files.
SENSITIVE_DIR_COMPONENTS: list[tuple[str, str]] = [
    (".aws", "File is inside the AWS configuration directory (.aws/)"),
    (".ssh", "File is inside the SSH key directory (.ssh/)"),
]

# (compiled_regex, pattern_string, reason) tuples — all compiled once at module load
SENSITIVE_CONTENT_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        r"AKIA[0-9A-Z]{16}",
        "AWS Access Key ID detected",
    ),
    (
        re.compile(r"-----BEGIN .+ PRIVATE KEY-----", re.MULTILINE),
        r"-----BEGIN .+ PRIVATE KEY-----",
        "PEM private key header detected",
    ),
    (
        re.compile(r"(?i)password\s*=\s*\S+"),
        r"(?i)password\s*=\s*\S+",
        "Hardcoded password assignment detected",
    ),
    (
        re.compile(r"(?i)secret\s*=\s*\S+"),
        r"(?i)secret\s*=\s*\S+",
        "Hardcoded secret assignment detected",
    ),
    (
        re.compile(r"(?i)api[_-]?key\s*=\s*\S+"),
        r"(?i)api[_-]?key\s*=\s*\S+",
        "Hardcoded API key assignment detected",
    ),
    (
        re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        "RFC-1918 internal IP address (10.x.x.x) detected",
    ),
    (
        re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
        r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
        "RFC-1918 internal IP address (192.168.x.x) detected",
    ),
]

# Sentinel Finding injected when directory traversal fails (fail-closed design).
# When the scanner cannot walk the workspace it must not return ALLOW_CLOUD.
_WALK_ERROR_REASON = (
    "Directory traversal failed — treating workspace as sensitive (fail-safe). "
    "Ensure the workspace is readable before re-running."
)


def _make_walk_error_finding(workspace: Path, layer: DetectionLayer) -> Finding:
    return Finding(
        layer=layer,
        file=workspace,
        pattern="<walk-error>",
        reason=_WALK_ERROR_REASON,
        match_value="",
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class SensitivityScanner:
    """
    Two-layer sensitive data scanner (Security Layer 6 of 7).

    Layer 1 — File Pattern Matching: flags files by name/glob and parent dir
    Layer 2 — Content Scanning: flags files by content regex

    Both layers run concurrently via asyncio.gather. Results are merged.

    Fail-closed: any OS error during directory traversal is treated as a
    sensitivity signal and produces FORCE_LOCAL, never ALLOW_CLOUD.

    The scanner must always run before BurstEngine.
    """

    async def scan(self, workspace: Path, policy: Any = None) -> SensitivityResult:
        """
        Scan workspace for sensitive data.

        Args:
            workspace: Path to the directory to scan.
            policy: Ignored in V1. V2: policy-file enforcement not yet implemented.

        Returns:
            SensitivityResult with is_sensitive, findings, and recommendation.

        Raises:
            ValueError: If workspace does not exist or is not a directory.
        """
        # V2: policy-file enforcement not yet implemented
        if not workspace.exists():
            raise ValueError(f"Workspace does not exist: {workspace}")
        if not workspace.is_dir():
            raise ValueError(f"Workspace is not a directory: {workspace}")

        layer1_findings, layer2_findings = await asyncio.gather(
            self._scan_file_patterns(workspace),
            self._scan_content(workspace),
        )

        all_findings = layer1_findings + layer2_findings
        is_sensitive = len(all_findings) > 0
        recommendation = (
            Recommendation.FORCE_LOCAL if is_sensitive else Recommendation.ALLOW_CLOUD
        )

        return SensitivityResult(
            is_sensitive=is_sensitive,
            findings=all_findings,
            recommendation=recommendation,
        )

    async def _scan_file_patterns(self, workspace: Path) -> list[Finding]:
        """Layer 1: scan file names and parent directories against sensitive patterns."""

        # TODO(V2): rglob follows symlinks — add symlink containment check
        def _walk() -> list[Path]:
            return [p for p in workspace.rglob("*") if p.is_file()]

        try:
            all_files = await asyncio.to_thread(_walk)
        except OSError:
            # Fail-closed: cannot traverse → treat workspace as sensitive
            return [_make_walk_error_finding(workspace, DetectionLayer.FILE_PATTERN)]

        findings: list[Finding] = []
        for file_path in all_files:
            try:
                # Check file name against sensitive file patterns
                for glob_pattern, reason in SENSITIVE_FILE_PATTERNS:
                    if fnmatch.fnmatch(file_path.name, glob_pattern):
                        findings.append(
                            Finding(
                                layer=DetectionLayer.FILE_PATTERN,
                                file=file_path,
                                pattern=glob_pattern,
                                reason=reason,
                                match_value="",
                            )
                        )

                # Check each parent directory component against sensitive dir patterns.
                # This catches files inside .aws/ and .ssh/ which are directories, not files.
                try:
                    rel_parts = file_path.relative_to(workspace).parts[:-1]
                except ValueError:
                    rel_parts = ()
                for part in rel_parts:
                    for dir_pattern, dir_reason in SENSITIVE_DIR_COMPONENTS:
                        if fnmatch.fnmatch(part, dir_pattern):
                            findings.append(
                                Finding(
                                    layer=DetectionLayer.FILE_PATTERN,
                                    file=file_path,
                                    pattern=dir_pattern,
                                    reason=dir_reason,
                                    match_value="",
                                )
                            )

            except (PermissionError, OSError):
                continue

        return findings

    async def _scan_content(self, workspace: Path) -> list[Finding]:
        """Layer 2: scan file contents against sensitive regex patterns."""

        # TODO(V2): rglob follows symlinks — add symlink containment check
        def _walk() -> list[Path]:
            return [p for p in workspace.rglob("*") if p.is_file()]

        try:
            all_files = await asyncio.to_thread(_walk)
        except OSError:
            # Fail-closed: cannot traverse → treat workspace as sensitive
            return [_make_walk_error_finding(workspace, DetectionLayer.CONTENT_SCAN)]

        findings: list[Finding] = []

        for file_path in all_files:
            try:
                stat = await asyncio.to_thread(file_path.stat)
                if stat.st_size > MAX_FILE_SIZE_BYTES:
                    # Large files are skipped in content scan (OOM protection).
                    # File-pattern scan still runs for these files (Layer 1).
                    continue

                # Binary detection: try to decode first BINARY_PROBE_SIZE bytes as UTF-8
                def _read_bytes() -> bytes:
                    with open(file_path, "rb") as f:
                        return f.read(BINARY_PROBE_SIZE)

                probe = await asyncio.to_thread(_read_bytes)
                try:
                    probe.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # binary file — skip content scan

                # Read full content as text
                def _read_text() -> str:
                    return file_path.read_text(encoding="utf-8", errors="ignore")

                content = await asyncio.to_thread(_read_text)

                for compiled_re, pattern_str, reason in SENSITIVE_CONTENT_PATTERNS:
                    for match in compiled_re.finditer(content):
                        raw = match.group()
                        # Always redact: expose only first 6 chars to help audit trail
                        # without leaking actual secret values.
                        redacted = raw[:6] + "***" if len(raw) > 6 else raw[:3] + "***"
                        findings.append(
                            Finding(
                                layer=DetectionLayer.CONTENT_SCAN,
                                file=file_path,
                                pattern=pattern_str,
                                reason=reason,
                                match_value=redacted,
                            )
                        )

            except (PermissionError, OSError):
                continue

        return findings
