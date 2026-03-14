# BurstEngine — Developer Reference

> **Scheduling Step 2 of 6** — Burst Decision Engine  
> Runs immediately after `SensitivityScanner` on every sandbox invocation, before any runtime is provisioned.

---

## What It Does

`BurstEngine` is the single component responsible for deciding whether a sandbox task runs locally (on the developer's machine via Podman) or bursts to cloud (on the user's own AWS Fargate). It makes that decision **once, upfront**, before any runtime is provisioned. There is no mid-execution switching in V1.

The decision has two distinct phases. First, it checks the `SensitivityResult` produced by `SensitivityScanner`. If the scanner found sensitive data, `BurstEngine` returns `mode="local"` immediately, unconditionally, and does not read RAM. Sensitivity enforcement is a security hard-stop, not an advisory hint — it cannot be overridden by any caller or configuration.

Second, if cloud execution is permitted (no sensitive data found), `BurstEngine` reads the system's available RAM via `psutil`. If available RAM meets or exceeds the configured threshold, the task runs locally for free. If RAM is tight, it bursts to Fargate — costing the user a few cents per session while keeping their local machine responsive. If the RAM read itself fails, the engine defaults to local (`confidence="forced"`) rather than risking an unknown resource state on cloud infrastructure.

### Position in the Execution Pipeline

```
SandboxManager
  │
  ├─► 1. SensitivityScanner.scan(workspace)          → SensitivityResult
  │
  ├─► 2. BurstEngine.decide(sensitivity_result, ...)  → BurstDecision
  │           ↑ This component
  │
  ├─► 3. Runtime.provision(config)
  │           PodmanRuntime  (mode="local")
  │           FargateRuntime (mode="cloud")
  ├─► 4. Runtime.execute(task)                        → TaskResult
  ├─► 5. AuditLogger.record(all_actions)
  └─► 6. Runtime.destroy()
```

---

## Decision Logic

The decision algorithm is strictly ordered. Steps are evaluated top-to-bottom; the first matching branch wins.

| # | Condition | `mode` | `confidence` | Example `reason` string |
|---|-----------|--------|--------------|-------------------------|
| 1 | Sensitive data detected (`FORCE_LOCAL`) | `local` | `forced` | `"sensitive data detected"` |
| 2 | RAM read failed (`psutil` error) | `local` | `forced` | `"RAM read failed — defaulting to local"` |
| 3 | Available RAM ≥ threshold | `local` | `preferred` | `"sufficient RAM (8.0GB >= 4.0GB)"` |
| 4 | Available RAM < threshold | `cloud` | `preferred` | `"insufficient RAM (2.0GB < 4.0GB)"` |

### `confidence` Values Explained

**`"forced"`** — The decision cannot be overridden by any caller, flag, or future CLI argument. It is always the result of a security or safety constraint (sensitive data detected, or RAM state unknown). `BurstDecision.confidence == "forced"` means the pipeline treats this as final.

**`"preferred"`** — The decision is advisory. It reflects the best available heuristic (available RAM vs threshold) but is not a hard security requirement. In V2, CLI flags such as `--force-local` and `--force-cloud` will be permitted to override `preferred` decisions. They will never be permitted to override `forced` decisions.

### Sensitivity Takes Absolute Priority

Step 1 short-circuits the entire engine. When `sensitivity_result.recommendation == FORCE_LOCAL`, `BurstEngine` returns without reading RAM. There is no code path through which a `FORCE_LOCAL` recommendation can result in `mode="cloud"`. This is a closed decision (AGENTS.md Decision #9).

---

## Usage

```python
import asyncio
from pathlib import Path
from src.sandbox.burst import BurstEngine
from src.sandbox.detection import SensitivityScanner

async def main():
    workspace = Path("./my-project")
    scanner = SensitivityScanner()
    engine = BurstEngine(ram_threshold_gb=4.0)  # 4 GB is the default

    sensitivity = await scanner.scan(workspace)
    decision = await engine.decide(sensitivity, workspace)

    print(f"Mode:       {decision.mode}")
    print(f"Confidence: {decision.confidence}")
    print(f"Reason:     {decision.reason}")

asyncio.run(main())
```

Example outputs:

```
# Clean workspace, 10 GB available
Mode:       local
Confidence: preferred
Reason:     sufficient RAM (10.0GB >= 4.0GB)

# Clean workspace, 1 GB available
Mode:       cloud
Confidence: preferred
Reason:     insufficient RAM (1.0GB < 4.0GB)

# Workspace contains .env or AWS keys
Mode:       local
Confidence: forced
Reason:     sensitive data detected
```

---

## API Reference

### `BurstDecision`

Immutable result of the burst-or-local decision. Produced by `BurstEngine.decide()` and consumed directly by `SandboxManager` (Step 3 onwards) and written verbatim to the audit trail.

```python
@dataclass(frozen=True)
class BurstDecision:
    mode:       str   # "local" | "cloud"
    reason:     str   # human-readable explanation for audit trail
    confidence: str   # "forced" | "preferred"
```

| Field | Type | Description |
|-------|------|-------------|
| `mode` | `str` | `"local"` — run on this machine. `"cloud"` — burst to AWS Fargate. |
| `reason` | `str` | Human-readable justification, logged verbatim in the audit trail. Includes numeric RAM values for RAM-based decisions. |
| `confidence` | `str` | `"forced"` — security or safety constraint, cannot be overridden. `"preferred"` — heuristic, can be overridden by CLI flags in V2. |

`frozen=True` is intentional. `BurstDecision` instances are passed across coroutines and into the audit logger. Immutability prevents any downstream component from silently altering the decision after it is made. Attempting to set an attribute on a `BurstDecision` raises `dataclasses.FrozenInstanceError` at runtime.

---

### `BurstEngine`

The decision engine. Stateless except for the configured `ram_threshold_gb`. Safe to instantiate once and reuse across requests.

```python
class BurstEngine:
    def __init__(self, ram_threshold_gb: float = 4.0) -> None: ...

    async def decide(
        self,
        sensitivity_result: SensitivityResult,
        workspace: Path,
        config: SandboxConfig | None = None,
    ) -> BurstDecision: ...
```

#### Constructor

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ram_threshold_gb` | `float` | `4.0` | Minimum available RAM in binary gigabytes needed to run locally. Tasks where available RAM is below this value burst to cloud. |

#### `decide(sensitivity_result, workspace, config=None)`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sensitivity_result` | `SensitivityResult` | Yes | Result from `SensitivityScanner.scan()`. Inspected for `recommendation` field. |
| `workspace` | `pathlib.Path` | Yes | Workspace path. **Accepted for ADR-001 interface compatibility but unused in V1.** |
| `config` | `SandboxConfig \| None` | No | Sandbox config. **Accepted for interface compatibility, unused in V1.** |

**Returns:** `BurstDecision`

**Does not raise.** All internal errors (psutil failure) are caught and converted to a `forced` local decision. This method is always safe to `await`.

The `psutil` call runs inside `asyncio.to_thread()` to avoid blocking the event loop, even though the call itself is fast in practice.

---

### `get_available_ram_gb()`

```python
def get_available_ram_gb() -> float:
    ...
```

Reads `psutil.virtual_memory().available` — the amount of RAM the OS can give to new processes immediately without swapping, not total installed RAM — and returns it as a `float` in binary gigabytes (divided by `1024 ** 3`). If `psutil` raises any exception (missing hardware sensor, permission error, platform unsupported), it is re-raised as a `RuntimeError` with a descriptive message. The caller (`BurstEngine.decide()`) catches this and returns a `forced` local decision.

This function is defined at **module level**, not as a method, specifically so that tests can patch it with `unittest.mock.patch("src.sandbox.burst.engine.get_available_ram_gb", ...)` without monkey-patching `psutil` internals. Do not move it inside the class.

---

## Configuration

### `ram_threshold_gb`

The only configurable parameter in V1. Set at `BurstEngine` construction time.

```python
engine = BurstEngine(ram_threshold_gb=8.0)  # require 8 GB before running locally
```

**Default is 4 GB.** This is enough to run a typical Python or Node agent sandbox with reasonable headroom above normal OS + background process consumption. Machines with 8 GB total RAM commonly have 4–6 GB available during active development use.

The threshold is intentionally a constructor parameter rather than a config-file value in V1 — `pyproject.toml` is the single config surface (Decision #11) and the `sandboxshift.yaml` config schema does not yet include a `burst.ram_threshold_gb` key. Integrations and tests can pass custom values without touching any file.

**CLI overrides (`--force-local` / `--force-cloud`)** for `preferred` decisions are a V2 feature. They are not implemented in V1. `forced` decisions can never be overridden by a CLI flag regardless of version.

---

## Security Model

### `FORCE_LOCAL` Is Unconditional (Decision #9)

When `SensitivityScanner` sets `recommendation = FORCE_LOCAL`, `BurstEngine` must return `mode="local"` unconditionally. This contract is enforced by the decision algorithm itself: the FORCE_LOCAL branch is the first check in `decide()`, short-circuiting before any RAM read. There is no code path — no flag, no config value, no RAM level — that bypasses it. This design mirrors the principle established in Decision #9 (AGENTS.md): scan errors and sensitivity findings fail closed, never open.

### Fail-Closed on RAM Errors

If `get_available_ram_gb()` raises — due to a broken platform sensor, a permission restriction, or any other psutil failure — `BurstEngine` returns `mode="local", confidence="forced"`. Unknown resource state is treated as insufficient for cloud trust. The pipeline never crashes at the burst-decision step; it degrades safely to local execution.

### Immutable Decision Record

`BurstDecision` is `frozen=True`. Once produced, the decision object cannot be altered. Any component downstream in the pipeline receives the same `mode`, `reason`, and `confidence` that `BurstEngine` wrote. Tampering (accidental or otherwise) raises `FrozenInstanceError` before any mutation takes effect.

### Relationship to the 7-Layer Security Model

`BurstEngine` is not itself a detection layer. It operates at the junction of **Layer 6** (SensitivityScanner — sensitive data detection) and **Layer 7** (Audit Trail). It enforces the output of Layer 6 by making FORCE_LOCAL binding, and it feeds Layer 7 by producing a structured `BurstDecision` whose `reason` string is logged verbatim in the audit trail — recording not just the outcome but why it was chosen.

---

## Testing

The test suite contains **23 tests** covering:

- `FORCE_LOCAL` unconditionally returns `mode="local", confidence="forced"` regardless of available RAM
- RAM above threshold → `mode="local", confidence="preferred"`
- RAM below threshold → `mode="cloud", confidence="preferred"`
- Exact threshold boundary (RAM == threshold → local)
- `psutil` failure → `mode="local", confidence="forced"`, no exception propagated
- `BurstDecision` immutability (`FrozenInstanceError` on attempted mutation)
- `get_available_ram_gb()` unit tests: correct GB conversion, `RuntimeError` re-raise on psutil failure

To run the burst engine test suite:

```bash
pytest tests/sandbox/burst/ -v
```

To run with coverage:

```bash
pytest tests/sandbox/burst/ -v --cov=src/sandbox/burst --cov-report=term-missing
```

---

## V1 Limitations

The following are explicitly deferred. Do not implement them unless explicitly requested.

- **No CLI override** (`--force-local` / `--force-cloud`) for `preferred` decisions — V2
- **No cost cap enforcement** (max $/session) — requires Fargate pricing API integration — V2
- **No GPU availability check** — no GPU-dependent runtimes exist in V1 — V2
- **No mid-execution migration** (local → cloud checkpoint/resume) — upfront-only decision in V1 (Decision #5) — V2
- **No policy-file–driven RAM threshold** — constructor parameter is sufficient for V1 — V2
- **No multi-region Fargate fallback** — single region in V1 — V2
- **No Spot instance burst mode** — requires additional Terraform config — V2

---

## Related Files

| File | Purpose |
|------|---------|
| [src/sandbox/burst/engine.py](../../src/sandbox/burst/engine.py) | Full implementation |
| [src/sandbox/burst/__init__.py](../../src/sandbox/burst/__init__.py) | Public package exports (`BurstEngine`, `BurstDecision`, `get_available_ram_gb`) |
| [tests/sandbox/burst/test_engine.py](../../tests/sandbox/burst/test_engine.py) | Test suite (23 tests) |
| [architecture/decisions/ADR-003-burst-engine.md](../../architecture/decisions/ADR-003-burst-engine.md) | Design decisions and rationale |
| [docs/components/sensitivity-scanner.md](sensitivity-scanner.md) | SensitivityScanner reference — component that runs before BurstEngine |
