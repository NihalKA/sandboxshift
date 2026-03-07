# ADR-001: Overall System Architecture

## Status
Accepted

## Context
SandboxShift needs a core architecture that supports:
- Running agent tasks in isolated sandboxes
- Deciding between local and cloud execution automatically
- Detecting sensitive data before any cloud decision
- Maintaining a full audit trail
- Being extensible to multiple runtimes (Podman, Fargate, K8s)

## Decision

Adopt a **pluggable runtime adapter pattern** with a central **SandboxManager** orchestrator.

### Component Diagram

```
CLI / API Request
       │
       ▼
┌─────────────────────────────────────────┐
│           SandboxManager                │
│                                         │
│  1. SensitivityScanner.scan(workspace)  │
│  2. BurstEngine.decide(ram, sensitive)  │
│  3. Runtime.provision(config)           │
│  4. Runtime.execute(task)               │
│  5. AuditLogger.record(all actions)     │
│  6. Runtime.destroy()                   │
└─────────────────────────────────────────┘
       │                    │
       ▼                    ▼
┌────────────┐      ┌──────────────┐
│  Podman    │      │   Fargate    │
│  Runtime   │      │   Runtime   │
│  (local)   │      │   (cloud)    │
└────────────┘      └──────────────┘
```

### Key Interfaces

```python
class Runtime(ABC):
    async def provision(self, config: SandboxConfig) -> SandboxInstance
    async def execute(self, instance: SandboxInstance, task: Task) -> TaskResult
    async def destroy(self, instance: SandboxInstance) -> None

class BurstEngine:
    async def decide(self, workspace: Path, config: SandboxConfig) -> BurstDecision
    # BurstDecision: { mode: "local" | "cloud", reason: str }

class SensitivityScanner:
    async def scan(self, workspace: Path, policy: Policy) -> SensitivityResult
    # SensitivityResult: { is_sensitive: bool, findings: list[str] }

class AuditLogger:
    def record(self, event: AuditEvent) -> None
    # AuditEvent: { timestamp, action, target, result, mode }
```

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Monolithic execution engine | Simple | Not extensible, hard to test |
| Pluggable runtime adapters (chosen) | Extensible, testable, clean separation | Slightly more initial code |
| Direct Podman CLI calls | Simple to start | Not testable, not portable |

## Consequences

**Easier:**
- Adding new runtimes (K8s, Firecracker) — just implement Runtime interface
- Testing — each component can be mocked independently
- Reasoning about security — SensitivityScanner always runs before BurstEngine

**Harder:**
- More interfaces to define upfront
- State management across async components

## Implementation Order (V1)

1. `SensitivityScanner` — must be first, gates everything else
2. `BurstEngine` — uses scanner result
3. `PodmanRuntime` — local execution
4. `SandboxManager` — wires them together
5. `FargateRuntime` — cloud execution
6. `AuditLogger` — woven through all steps
7. FastAPI layer — exposes via HTTP
8. CLI — wraps the API

## Date
2026-03-07
