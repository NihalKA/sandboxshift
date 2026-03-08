# SensitivityScanner — Developer Reference

> **Security Layer 6 of 7** — Sensitive Data Detection  
> Runs unconditionally **before** `BurstEngine` on every sandbox invocation.

---

## Overview

`SensitivityScanner` is the enforcement point for SandboxShift's core security promise:

> *If a task touches sensitive data, it never leaves the developer's machine.*

The scanner inspects the workspace directory using two independent detection layers and returns a structured `SensitivityResult`. The result's `recommendation` field directly constrains the `BurstEngine`:

- `FORCE_LOCAL` → cloud execution is blocked regardless of available RAM.
- `ALLOW_CLOUD` → the `BurstEngine` proceeds with its normal local/cloud decision.

### Position in the Execution Flow

```
SandboxManager
  │
  ├─► SensitivityScanner.scan(workspace)      ← this component
  │       ├─ Layer 1: File Pattern Matching
  │       └─ Layer 2: Content Scanning (concurrent)
  │               └─► SensitivityResult
  │                       └─► recommendation: FORCE_LOCAL | ALLOW_CLOUD
  │
  └─► BurstEngine.decide(ram, sensitivity_result)
          └─► BurstDecision: { mode: "local" | "cloud", reason: str }
```

The scanner has **zero external dependencies** — it uses only the Python standard library (`asyncio`, `pathlib`, `re`, `fnmatch`, `dataclasses`).

---

## Quick Start

```python
import asyncio
from pathlib import Path
from src.sandbox.detection import SensitivityScanner, Recommendation

async def main():
    scanner = SensitivityScanner()
    result = await scanner.scan(workspace=Path("/my/project"))

    if result.is_sensitive:
        print(f"Workspace is sensitive — {result.recommendation.value}")
        for line in result.explain():
            print(" ", line)
    else:
        print("No sensitive data detected — cloud execution allowed")

asyncio.run(main())
```

Example output when secrets are found:

```
Workspace is sensitive — force_local
  [file_pattern] /my/project/.env: Environment variable file may contain secrets (pattern: .env)
  [content_scan] /my/project/config.py: AWS Access Key ID detected (pattern: AKIA[0-9A-Z]{16}) [evidence: AKIAJ6***]
```

---

## API Reference

### `SensitivityScanner`

The top-level scanner class. Stateless — safe to instantiate once and reuse.

```python
class SensitivityScanner:
    async def scan(
        self,
        workspace: Path,
        policy: Any = None,
    ) -> SensitivityResult:
        ...
```

#### `scan(workspace, policy=None)`

Scans `workspace` for sensitive data using both detection layers concurrently.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `workspace` | `pathlib.Path` | Yes | Absolute path to the directory to scan. Must exist and be a directory. |
| `policy` | `Any` | No | Accepted for API compatibility. **Ignored in V1** — policy-file enforcement is a V2 feature. |

**Returns:** `SensitivityResult`

**Raises:**
- `ValueError` — if `workspace` does not exist or is not a directory.

**Does not raise** on OS errors during traversal — those are caught internally and converted to a sentinel `Finding` (see [Fail-Closed Behaviour](#fail-closed-behaviour)).

---

### `SensitivityResult`

The aggregate result returned by `SensitivityScanner.scan()`.

```python
@dataclass
class SensitivityResult:
    is_sensitive:   bool
    findings:       list[Finding]  = field(default_factory=list)
    recommendation: Recommendation = Recommendation.ALLOW_CLOUD
```

| Field | Type | Description |
|-------|------|-------------|
| `is_sensitive` | `bool` | `True` if one or more findings were produced by either layer. |
| `findings` | `list[Finding]` | All structured findings from both layers. Empty list when `is_sensitive` is `False`. |
| `recommendation` | `Recommendation` | `FORCE_LOCAL` when `is_sensitive` is `True`, otherwise `ALLOW_CLOUD`. Directly consumed by `BurstEngine`. |

#### `SensitivityResult.explain() → list[str]`

Returns one human-readable string per finding, suitable for the audit log or CLI output.

Each line follows the format:
```
[<layer>] <full_file_path>: <reason> (pattern: <pattern>)[evidence: <redacted_match>]
```

The `evidence` suffix is included only when `Finding.match_value` is non-empty (i.e., for content-scan matches). Match values are always redacted: only the first 6 characters of the actual match are shown, followed by `***`.

**Example output:**
```python
result.explain()
# [
#   "[file_pattern] /project/.env: Environment variable file may contain secrets (pattern: .env)",
#   "[content_scan] /project/app.py: AWS Access Key ID detected (pattern: AKIA[0-9A-Z]{16}) [evidence: AKIAJ6***]",
#   "[content_scan] /project/app.py: Hardcoded password assignment detected (pattern: (?i)password\\s*=\\s*\\S+) [evidence: passwo***]",
# ]
```

---

### `Finding`

A single detected issue produced by either layer.

```python
@dataclass
class Finding:
    layer:       DetectionLayer
    file:        Path
    pattern:     str
    reason:      str
    match_value: str = ""
```

| Field | Type | Description |
|-------|------|-------------|
| `layer` | `DetectionLayer` | Which layer produced this finding (`FILE_PATTERN` or `CONTENT_SCAN`). |
| `file` | `pathlib.Path` | Absolute path of the file that triggered the finding. |
| `pattern` | `str` | The glob pattern (Layer 1) or regex string (Layer 2) that matched. |
| `reason` | `str` | Human-readable description of why this is sensitive. |
| `match_value` | `str` | Redacted form of the matched string. Empty for Layer 1 findings. For Layer 2 findings: first 6 chars + `***`. |

---

### `DetectionLayer`

```python
class DetectionLayer(str, Enum):
    FILE_PATTERN = "file_pattern"   # Layer 1 — filename/path glob matching
    CONTENT_SCAN = "content_scan"   # Layer 2 — regex content scanning
```

---

### `Recommendation`

```python
class Recommendation(str, Enum):
    FORCE_LOCAL  = "force_local"   # workspace is sensitive — block cloud execution
    ALLOW_CLOUD  = "allow_cloud"   # no sensitive data found — cloud execution permitted
```

The `BurstEngine` must honour `FORCE_LOCAL` unconditionally. It may not override this value based on RAM availability or any other signal.

---

## Detection Rules

### Layer 1 — File Pattern Rules

Matched via `fnmatch.fnmatch` against the **file name** (not the full path), except for directory component rules which match against each segment of the path relative to the workspace root.

| Pattern | What It Detects | Example Match |
|---------|-----------------|---------------|
| `.env` | Bare environment file | `.env` |
| `*.env` | Suffixed env variants | `.env.local`, `.env.production` |
| `*.pem` | TLS certificates and private keys | `server.pem`, `ca.pem` |
| `*.key` | Generic private key files | `id_rsa.key`, `server.key` |
| `*.p12` | PKCS#12 keystores | `keystore.p12`, `client.p12` |
| `credentials` | AWS credentials file (extensionless) | `credentials` |
| `credentials.json` | GCP / OAuth credential files | `credentials.json` |
| `*secret*` | Any filename containing "secret" | `app_secret.txt`, `my-secret-config` |
| `*token*` | Any filename containing "token" | `github_token`, `token.json` |
| `.aws` *(dir)* | Any file nested inside `.aws/` | `.aws/credentials`, `.aws/config` |
| `.ssh` *(dir)* | Any file nested inside `.ssh/` | `.ssh/id_rsa`, `.ssh/known_hosts` |

Directory component rules (`.aws`, `.ssh`) are matched against each parent path segment individually so that deeply nested files (e.g., `home/user/.aws/credentials`) are still caught.

### Layer 2 — Content Pattern Rules

All regexes are compiled once at module load time with `re.compile`. Applied to the full text content of files ≤ 1 MB that successfully decode as UTF-8.

| Regex | What It Detects | Example Match |
|-------|-----------------|---------------|
| `AKIA[0-9A-Z]{16}` | AWS Access Key IDs | `AKIAIOSFODNN7EXAMPLE` |
| `-----BEGIN .+ PRIVATE KEY-----` | PEM private key headers | `-----BEGIN RSA PRIVATE KEY-----` |
| `(?i)password\s*=\s*\S+` | Hardcoded password assignments | `password=hunter2`, `PASSWORD = secret` |
| `(?i)secret\s*=\s*\S+` | Hardcoded secret assignments | `secret=abc123`, `SECRET="xyz"` |
| `(?i)api[_-]?key\s*=\s*\S+` | Hardcoded API key assignments | `api_key=sk-...`, `API-KEY = abc` |
| `\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b` | RFC-1918 10.x.x.x internal IPs | `10.0.0.1`, `10.128.4.22` |
| `\b192\.168\.\d{1,3}\.\d{1,3}\b` | RFC-1918 192.168.x.x internal IPs | `192.168.1.1`, `192.168.0.254` |

Match values written to `Finding.match_value` are always redacted before storage: `raw[:6] + "***"` (or `raw[:3] + "***"` if the match is 6 characters or shorter). Actual secret values are never stored or logged.

---

## Fail-Closed Behaviour

**Security invariant:** an OS error during workspace traversal must never result in `ALLOW_CLOUD`.

If `Path.rglob()` raises an `OSError` (permission denied, broken mount, etc.) in either layer, the layer catches the exception and returns a sentinel `Finding` with `pattern="<walk-error>"`. This finding causes `is_sensitive` to be `True` and `recommendation` to be `FORCE_LOCAL`.

```
Directory traversal raises OSError
        │
        ▼
 sentinel Finding injected
  layer = FILE_PATTERN | CONTENT_SCAN
  pattern = "<walk-error>"
  reason = "Directory traversal failed — treating workspace as sensitive (fail-safe)."
        │
        ▼
 is_sensitive = True
 recommendation = FORCE_LOCAL
```

This means the scanner fails **closed** (to the safe state) rather than open. A workspace that cannot be read is treated as if it contains secrets.

Individual file-level `PermissionError` or `OSError` exceptions (raised when opening a single file, not the walk itself) are silently skipped — the file is omitted from both layers and scanning continues.

---

## Integration with BurstEngine

`SandboxManager` orchestrates the sequence. The scanner always runs first:

```python
# Pseudocode — SandboxManager.run()
scanner = SensitivityScanner()
result  = await scanner.scan(workspace)

burst_engine = BurstEngine()
decision = burst_engine.decide(
    available_ram=get_available_ram(),
    sensitivity_result=result,      # ← scanner output passed directly
)

# BurstEngine contract:
# if result.recommendation == FORCE_LOCAL → decision.mode is always "local"
# if result.recommendation == ALLOW_CLOUD → normal RAM-based decision applies
```

The `BurstEngine` receives the complete `SensitivityResult` (not just the recommendation) so it can include the scanner's findings in its own decision explanation.

---

## V1 Limitations

The following capabilities are **not implemented** in V1. Do not attempt to use or extend them until explicitly requested.

| Limitation | Status | Target |
|------------|--------|--------|
| LLM-based semantic classifier (catches obfuscated or split secrets) | Not implemented | V2 |
| User policy file (`.sandboxshift/policy.yaml`) to whitelist patterns | Not implemented | V2 |
| Entropy-based secret detection (Shannon entropy scan) | Not implemented | V2 |
| Git history scanning | Not implemented | V2 |
| Symlink containment (symlinks are currently followed by `rglob`) | Not hardened | V2 |
| Per-pattern severity levels (currently binary: sensitive or not) | Not implemented | V2 |
| Files > 1 MB are **not** content-scanned (Layer 2 skips them; Layer 1 still checks their names) | By design | V1 limit |
| Binary files are skipped in Layer 2 (detected via UTF-8 decode probe on first 8 KB) | By design | V1 limit |

---

## Running Tests

Install the project in editable mode with development dependencies, then run the detection test suite:

```bash
pip install -e ".[dev]"
pytest tests/sandbox/detection/ -v
```

The test suite contains 38 tests covering:

- Layer 1: each sensitive file pattern and directory component rule
- Layer 2: each content regex, including redaction correctness
- Combined: concurrent layer execution and result merging
- Fail-closed: `OSError` during traversal produces `FORCE_LOCAL`
- Edge cases: empty workspace, files > 1 MB, binary files, files the scanner cannot read

To run with coverage:

```bash
pytest tests/sandbox/detection/ -v --cov=src/sandbox/detection --cov-report=term-missing
```

---

## Related Files

| File | Purpose |
|------|---------|
| [src/sandbox/detection/sensitivity.py](../../src/sandbox/detection/sensitivity.py) | Full implementation |
| [src/sandbox/detection/__init__.py](../../src/sandbox/detection/__init__.py) | Public package exports |
| [tests/sandbox/detection/test_sensitivity.py](../../tests/sandbox/detection/test_sensitivity.py) | Test suite (38 tests) |
| [architecture/decisions/ADR-002-sensitivity-scanner.md](../../architecture/decisions/ADR-002-sensitivity-scanner.md) | Design decisions and rationale |
