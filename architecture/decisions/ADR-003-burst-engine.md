# ADR-003: BurstEngine Design

## Status
Accepted

## Date
2026-03-14

---

## Context

SandboxShift's core value proposition is automatic local/cloud bursting: tasks run
locally when possible, and burst to the user's own AWS Fargate when the local machine
cannot handle the load. A single deterministic component — `BurstEngine` — is
responsible for making that choice before any sandbox is provisioned.

### Position in the Execution Pipeline

`BurstEngine` is Step 2 in `SandboxManager`'s orchestration sequence, always preceded
by `SensitivityScanner` (ADR-002):

```
SandboxManager
  │
  ├─ 1. SensitivityScanner.scan(workspace)  → SensitivityResult
  │
  ├─ 2. BurstEngine.decide(sensitivity_result, workspace, config)  → BurstDecision
  │         ↑ This ADR
  │
  ├─ 3. Runtime.provision(config)           → SandboxInstance
  ├─ 4. Runtime.execute(task)               → TaskResult
  ├─ 5. AuditLogger.record(all_actions)
  └─ 6. Runtime.destroy()
```

### Why BurstEngine Exists as a Separate Component

- The decision logic must be **independently testable** without standing up any
  runtime or AWS infrastructure.
- The decision must be **auditable**: both the outcome (`mode`) and its justification
  (`reason`, `confidence`) must be recorded in the audit trail.
- The decision boundary between sensitivity enforcement and resource optimisation must
  be **explicit**: sensitivity is a security hard-stop; RAM is a performance hint.
- Future expansions (GPU availability, spot pricing, cost caps) belong here, not
  scattered across the runtime adapters.

### Relationship to SensitivityScanner

`SensitivityScanner` (ADR-002, Decision #9) produces a `Recommendation`:

| Recommendation    | Meaning                                                 |
|-------------------|---------------------------------------------------------|
| `FORCE_LOCAL`     | Sensitive data detected — cloud execution prohibited    |
| `ALLOW_CLOUD`     | No sensitive data found — cloud is permitted if needed  |

`BurstEngine` is the **consumer** of that recommendation. When the recommendation is
`FORCE_LOCAL`, `BurstEngine` must return `mode="local"` unconditionally, regardless
of available RAM. This is a closed decision (Decision #9) — `BurstEngine` has no
authority to reverse it.

### The RAM-Based Decision

When cloud is permitted, the decision is purely pragmatic: if the local machine has
enough free RAM, run locally (free, faster, private). Otherwise, burst to Fargate
(costs cents, frees the local machine).

The threshold default is **4 GB** — enough to run a typical Python/Node agent sandbox
with headroom. It is configurable at `BurstEngine` construction time so integrations
and tests can override it without touching any config file.

### psutil for RAM Reading

Python's standard library (`resource`, `os`) cannot read system-wide available RAM
portably across Linux and macOS. `psutil` is the de-facto standard for this and is
already a transitive dependency in many Python stacks. It must be added to
`[project.dependencies]` in `pyproject.toml`.

All `psutil` calls are wrapped in a dedicated `get_available_ram_gb()` function so
tests can patch it without monkey-patching `psutil` internals.

---

## Decision

### Algorithm

```
INPUTS:
  sensitivity_result: SensitivityResult   (from SensitivityScanner)
  available_ram_gb:   float               (from get_available_ram_gb())
  threshold_gb:       float               (BurstEngine constructor param, default=4.0)

DECISION:
  if sensitivity_result.recommendation == Recommendation.FORCE_LOCAL:
      mode       = "local"
      reason     = "sensitive data detected"
      confidence = "forced"

  elif available_ram_gb >= threshold_gb:
      mode       = "local"
      reason     = f"sufficient RAM ({available_ram_gb:.1f}GB >= {threshold_gb:.1f}GB)"
      confidence = "preferred"

  else:
      mode       = "cloud"
      reason     = f"insufficient RAM ({available_ram_gb:.1f}GB < {threshold_gb:.1f}GB)"
      confidence = "preferred"
```

`confidence = "forced"` means the decision cannot be overridden by any caller
(sensitivity enforcement). `confidence = "preferred"` means the decision is
advisory — a future CLI flag could allow `--force-local` or `--force-cloud` overrides
for RAM-based decisions (V2 feature; not implemented in V1).

### Data Structure: BurstDecision

```python
@dataclass(frozen=True)
class BurstDecision:
    mode:       str   # "local" | "cloud"
    reason:     str   # human-readable explanation for audit trail
    confidence: str   # "forced" | "preferred"
```

`frozen=True` prevents accidental mutation after the decision is made.

### RAM Reading: get_available_ram_gb()

A module-level function (not a method) so it can be patched in tests:

```
def get_available_ram_gb() -> float:
    Reads psutil.virtual_memory().available
    Converts bytes → GB (divide by 1024**3)
    If psutil raises any exception → re-raises as RuntimeError
    (caller: BurstEngine.decide() must handle this)
```

### BurstEngine.decide() Signature (V1)

```
async def decide(
    self,
    sensitivity_result: SensitivityResult,
    workspace: Path,
    config: SandboxConfig | None = None,
) -> BurstDecision
```

`workspace` and `config` are accepted for ADR-001 interface compatibility but are
ignored in V1. RAM is read inside `decide()` via `asyncio.to_thread(get_available_ram_gb)`.

If `get_available_ram_gb()` raises, `decide()` must catch it and return a
`BurstDecision(mode="local", reason="RAM read failed — defaulting to local",
confidence="forced")` so the pipeline never crashes at the burst-decision step.

---

## Options Considered

### Option Set A — Where to place FORCE_LOCAL enforcement

| Option | Description | Verdict |
|--------|-------------|---------|
| A1 — BurstEngine enforces (chosen) | BurstEngine reads `sensitivity_result.recommendation` and hard-stops | ✅ Chosen — single point of enforcement, testable, auditable |
| A2 — SandboxManager enforces | SandboxManager inspects result before calling BurstEngine | ❌ Enforcement scattered across two components, harder to audit |
| A3 — SensitivityScanner returns BurstDecision directly | Scanner skips BurstEngine entirely | ❌ Couples security scanner to scheduling logic — violates single responsibility |

### Option Set B — RAM reading approach

| Option | Description | Verdict |
|--------|-------------|---------|
| B1 — psutil.virtual_memory().available (chosen) | System-wide available RAM, cross-platform | ✅ Chosen — accurate, battle-tested, zero extra dependencies beyond psutil |
| B2 — /proc/meminfo (Linux only) | Direct kernel file read | ❌ Not portable to macOS; V1 must work on both |
| B3 — subprocess("free -g") | Shell command | ❌ Fragile, injection risk, not testable without shell mock |
| B4 — os.sysconf("SC_PHYS_PAGES") | Total (not available) RAM | ❌ Returns total installed RAM, not available — wrong metric |

### Option Set C — Confidence model

| Option | Description | Verdict |
|--------|-------------|---------|
| C1 — Binary forced/preferred (chosen) | "forced" = cannot override, "preferred" = advisory | ✅ Chosen — simple, maps cleanly to Decision #9 |
| C2 — Numeric probability 0.0–1.0 | Probability score | ❌ Misleading — the decision is deterministic, not probabilistic |
| C3 — Single boolean `overridable` | True/False flag | ❌ Less self-documenting in audit logs than named strings |

### Option Set D — BurstDecision mutability

| Option | Description | Verdict |
|--------|-------------|---------|
| D1 — frozen=True dataclass (chosen) | Immutable after creation | ✅ Chosen — prevents post-decision tampering; safe to pass across coroutines |
| D2 — Regular mutable dataclass | Default dataclass | ❌ Risk of accidental mutation in SandboxManager pipeline |
| D3 — TypedDict | Dict-based | ❌ No immutability, no type-safe attribute access |

---

## V1 Scope Exclusions

The following are explicitly deferred and must NOT be implemented by the Coder:

| Feature | Deferred To | Reason |
|---------|-------------|--------|
| `--force-local` / `--force-cloud` CLI override for RAM decisions | V2 | CLI layer not built yet; confidence="preferred" future-proofs it |
| Mid-execution migration (local → cloud checkpoint) | V2 | Decision #5: upfront-only in V1 |
| Cost cap enforcement (max $/session) | V2 | Requires Fargate pricing API integration |
| GPU availability check | V2 | No GPU-dependent runtimes in V1 |
| LLM-based sensitivity override | V2 | Decision #6 Layer 4 deferred |
| Multi-region Fargate fallback | V2 | Single Fargate region in V1 |
| Spot instance burst mode | V2 | Fargate Spot requires additional Terraform config |
| Policy file–driven RAM threshold | V2 | Constructor-param threshold is sufficient for V1 |
| Kubernetes / Firecracker burst | V3 | V3 runtimes not in scope |

---

## Consequences

### Easier

- **Testing**: `get_available_ram_gb()` can be patched with `unittest.mock.patch` —
  no real RAM required in tests.
- **Auditing**: Every `BurstDecision` carries `mode`, `reason`, and `confidence` —
  the audit logger needs no special-casing.
- **Security compliance**: The hard `FORCE_LOCAL` branch is a single `if` statement at
  the top of the decision tree — easy to review and impossible to accidentally bypass.
- **Adding new modes**: A V2 `--force-cloud` override only needs to check that
  `confidence != "forced"` before honouring it — no engine refactoring needed.
- **Integration**: `BurstEngine.decide()` has the exact signature from ADR-001 —
  `SandboxManager` can call it without adaptation.

### Harder

- **psutil dependency**: Adds one PyPI package to `[project.dependencies]`. Must be
  pinned to avoid API drift. (psutil's `virtual_memory()` API has been stable for
  5+ years — low risk.)
- **available vs total RAM nuance**: `psutil.virtual_memory().available` is the right
  metric but can behave unexpectedly on machines with large page caches. The 4 GB
  default threshold provides adequate buffer.
- **Async wrapping**: `psutil.virtual_memory()` is a synchronous call. It must run in
  `asyncio.to_thread()` inside `decide()` to avoid blocking the event loop, even
  though it is fast in practice. This adds a small layer of indirection.

---

## Relationship to Other Components

```
SandboxManager
  │
  ├─► 1. SensitivityScanner.scan(workspace)
  │           └─► SensitivityResult
  │                   └─► recommendation: FORCE_LOCAL | ALLOW_CLOUD
  │
  ├─► 2. BurstEngine.decide(sensitivity_result, workspace, config)
  │           ├─► get_available_ram_gb()  [via asyncio.to_thread]
  │           └─► BurstDecision(mode, reason, confidence)
  │
  └─► 3. Runtime.provision(config)
              PodmanRuntime (mode="local")  OR  FargateRuntime (mode="cloud")
```

---

## Implementation File Table

| File | Role |
|------|------|
| `src/sandbox/burst/__init__.py` | Package init — exports `BurstEngine`, `BurstDecision`, `get_available_ram_gb` |
| `src/sandbox/burst/engine.py` | Full implementation of `BurstDecision`, `get_available_ram_gb`, `BurstEngine` |
| `tests/sandbox/burst/__init__.py` | Empty — marks test package |
| `tests/sandbox/burst/test_engine.py` | Full test suite (23 tests) |
| `pyproject.toml` | Add `psutil>=5.9` to `[project.dependencies]` |

---

## References

- ADR-001: Overall System Architecture
- ADR-002: SensitivityScanner Design
- Decisions Log #9 (AGENTS.md): FORCE_LOCAL is unconditional
- Decisions Log #11 (AGENTS.md): pyproject.toml is the single config file
