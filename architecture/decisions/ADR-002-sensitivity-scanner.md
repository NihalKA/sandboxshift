# ADR-002: SensitivityScanner Design

## Status
Accepted

## Date
2026-03-08

---

## Context

SandboxShift's core security promise is: **if a task touches sensitive data, it never leaves the developer's machine.**

The `SensitivityScanner` (Security Layer 6 of 7) is the enforcement point for that promise. It runs before the `BurstEngine` makes any local/cloud decision. If it returns `is_sensitive: true`, the `BurstEngine` is constrained to `mode: local` regardless of available RAM.

### The Problem It Solves

AI agents working on real codebases routinely touch:
- `.env` files containing API keys and database passwords
- SSH private keys and TLS certificates
- AWS credentials files
- Files containing hardcoded secrets or internal IP ranges

If such a workspace were sent to AWS Fargate (even the user's own account), the blast radius of a future misconfiguration would be catastrophic. The scanner must catch these cases before any cloud provisioning begins.

### Why This Needs a Dedicated Component

- It must run **before** `BurstEngine.decide()` — this ordering is enforced by `SandboxManager`
- It must be **independently testable** without standing up any sandbox infrastructure
- It must **explain its findings** so the user understands why a task was forced local
- It must be **fast** — adding meaningful latency to every run is unacceptable
- It must be **complete enough for V1** without requiring an LLM or network call

---

## Decision

Implement `SensitivityScanner` as a two-layer, async, pure-Python scanner that:

1. **Layer 1 — File Pattern Matching**: Walks the workspace directory tree and flags any file whose name or path matches a known sensitive pattern (glob-based).
2. **Layer 2 — Content Scanning**: Reads the text content of non-binary files under a size limit and searches for regex patterns known to indicate secrets.

Each detected issue produces a structured `Finding` that records what was found, where, which layer caught it, and a human-readable reason. The aggregate `SensitivityResult` exposes `is_sensitive`, all `findings`, and a `recommendation` field that directly drives the `BurstEngine`.

### Data Structures

```python
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class DetectionLayer(str, Enum):
    FILE_PATTERN = "file_pattern"   # Layer 1
    CONTENT_SCAN = "content_scan"   # Layer 2


class Recommendation(str, Enum):
    FORCE_LOCAL  = "force_local"
    ALLOW_CLOUD  = "allow_cloud"


@dataclass
class Finding:
    layer: DetectionLayer           # which layer caught this
    file: Path                      # absolute path of the offending file
    pattern: str                    # the pattern/regex that matched
    reason: str                     # human-readable explanation
    match_value: str = ""           # the actual matched string (redacted for secrets)


@dataclass
class SensitivityResult:
    is_sensitive:   bool
    findings:       list[Finding]   = field(default_factory=list)
    recommendation: Recommendation  = Recommendation.ALLOW_CLOUD

    def explain(self) -> list[str]:
        """Return human-readable strings for each finding (used by CLI/API)."""
        return [
            f"[{f.layer.value}] {f.file.name}: {f.reason} (pattern: {f.pattern})"
            for f in self.findings
        ]
```

### Layer 1 — File Pattern Matching

**Algorithm:**
1. Recursively walk the workspace using `Path.rglob("*")`.
2. For each file encountered, test its name (not full path) against each glob in the sensitive patterns list.
3. If matched, create a `Finding(layer=FILE_PATTERN, ...)` and add it to results.
4. Continue walking — collect all matches, do not short-circuit (complete picture matters for the audit trail).

**Sensitive file patterns (V1):**

| Pattern         | Reason                                   |
|----------------|------------------------------------------|
| `.env`          | Environment variable files with secrets  |
| `*.env`         | Variant env files (`.env.local`, etc.)   |
| `*.pem`         | TLS/SSL certificates and private keys    |
| `*.key`         | Generic private key files                |
| `*.p12`         | PKCS#12 keystore files                   |
| `credentials`   | AWS credentials file (no extension)      |
| `credentials.json` | GCP / OAuth credential files          |
| `*secret*`      | Any file with "secret" in its name       |
| `*token*`       | Any file with "token" in its name        |
| `.aws`          | AWS config directory                     |
| `.ssh`          | SSH key directory                        |

Pattern matching uses Python's `fnmatch.fnmatch(filename, pattern)` — no external dependencies.

### Layer 2 — Content Scanning

**Algorithm:**
1. Iterate over all files in the workspace (same walk as Layer 1, or reuse results).
2. Skip files that exceed the size limit (default: **1 MB**) — large files are unlikely to be plain-text secrets and would hurt performance.
3. Detect binary files by attempting to decode the first 8 KB as UTF-8; skip if decoding fails.
4. Read the full text content of accepted files.
5. Apply each compiled regex pattern against the content.
6. For each match, create a `Finding` with a **redacted** `match_value` (first 6 chars + `***`) to avoid logging actual secrets.

**Sensitive content patterns (V1):**

| Pattern                          | Detects                                   |
|----------------------------------|-------------------------------------------|
| `AKIA[0-9A-Z]{16}`               | AWS Access Key IDs                        |
| `-----BEGIN .* PRIVATE KEY-----` | PEM-encoded private key headers           |
| `(?i)password\s*=\s*\S+`        | Hardcoded password assignments            |
| `(?i)secret\s*=\s*\S+`          | Hardcoded secret assignments              |
| `(?i)api[_-]?key\s*=\s*\S+`     | Hardcoded API key assignments             |
| `\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b` | RFC-1918 10.x.x.x internal IP range  |
| `\b192\.168\.\d{1,3}\.\d{1,3}\b` | RFC-1918 192.168.x.x internal IP range  |

All regexes are compiled once at module load time (`re.compile`) for performance.

### SensitivityScanner Class

```
class SensitivityScanner:
    async def scan(self, workspace: Path, policy: Policy) -> SensitivityResult
```

- Runs Layer 1 and Layer 2 **concurrently** using `asyncio.gather`.
- Merges findings from both layers.
- Sets `is_sensitive = len(findings) > 0`.
- Sets `recommendation = FORCE_LOCAL if is_sensitive else ALLOW_CLOUD`.
- The `Policy` parameter is accepted for API compatibility (defined in ADR-001) but **ignored in V1** — policy-file enforcement is a V2 feature.

### File I/O Strategy

- File walking uses `Path.rglob` (sync) wrapped in `asyncio.to_thread` to avoid blocking the event loop.
- File reading uses `asyncio.to_thread(path.read_bytes)` for Layer 2 content scanning.
- This keeps the interface `async` without requiring `aiofiles` as an external dependency.

---

## Options Considered

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| Single-pass scan (patterns + content in one walk) | Marginally fewer filesystem calls | Harder to test layers independently; layers are coupled | Rejected |
| Two-pass scan (Layer 1 then Layer 2, sequentially) | Simple, easy to reason about | Layer 2 must wait for Layer 1 to complete entirely | Rejected |
| Two-pass scan (Layer 1 and Layer 2 concurrently, chosen) | Independent, testable, faster on large workspaces | Slightly more complex coroutine wiring | **Accepted** |
| Sync implementation | Simpler code | Blocks event loop during large workspace scans; incompatible with FastAPI's async context | Rejected |
| External library (detect-secrets, truffleHog) | Comprehensive coverage | Adds dependency, harder to audit, overkill for V1 | Rejected |
| LLM classifier | Catches semantic secrets (e.g. obfuscated keys) | Requires model download, network, latency — V2 feature | Deferred to V2 |

---

## Consequences

**Easier:**
- Testing: each layer (`_scan_file_patterns`, `_scan_content`) can be tested in complete isolation
- Extending patterns: adding a new file glob or regex requires only one-line additions to the pattern lists
- Auditability: every finding includes the file, pattern, and reason — the `AuditLogger` can log the full `SensitivityResult` unchanged
- Performance: concurrent layer execution; binary/large-file skipping keeps runtime low
- Zero new dependencies: uses only Python standard library (`re`, `fnmatch`, `pathlib`, `asyncio`, `dataclasses`)

**Harder:**
- False negatives are possible: obfuscated or stored secrets (base64, split across lines) will not be caught by regex in V1
- No user override in V1: a user cannot whitelist a file to allow cloud execution even if it matches a pattern (policy file is V2)
- Binary detection is heuristic: the UTF-8 decode probe could miss some edge cases

---

## V1 Scope — Explicit Exclusions

The following are **out of scope** for V1 and must not be implemented until explicitly requested:

| Feature | Target Version | Reason Deferred |
|---------|---------------|-----------------|
| LLM-based semantic classifier (Layer 3) | V2 | Requires local model, adds latency and dependencies |
| User policy file (`.sandboxshift/policy.yaml`) — Layer 4 | V2 | Requires policy schema, parser, and override logic |
| Entropy-based secret detection (Shannon entropy scan) | V2 | Higher false-positive rate; needs tuning |
| Git history scanning | V2 | Scope and performance concerns for V1 |
| Symlink handling | V2 | Adds complexity; attacker surface in V1 is workspace root only |
| Per-pattern severity levels | V2 | Binary `is_sensitive` flag is sufficient for V1 routing decisions |

---

## Relationship to Other Components

```
SandboxManager
  │
  ├─► SensitivityScanner.scan(workspace)   ← This ADR
  │       └─► SensitivityResult
  │               └─► recommendation: FORCE_LOCAL | ALLOW_CLOUD
  │
  └─► BurstEngine.decide(ram, sensitivity_result)
          └─► BurstDecision: { mode: "local" | "cloud", reason: str }
```

`SensitivityScanner` always runs **before** `BurstEngine`. The `BurstEngine` receives the full `SensitivityResult` and must honour `FORCE_LOCAL` unconditionally.

---

## Implementation Location

| File | Purpose |
|------|---------|
| `src/sandbox/detection/sensitivity.py` | `Finding`, `SensitivityResult`, `SensitivityScanner` |
| `src/sandbox/detection/__init__.py` | Public exports from the detection package |
| `tests/sandbox/detection/test_sensitivity.py` | Full test suite for both layers and combined scanner |
