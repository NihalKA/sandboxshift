# SandboxShift — Master Agent Context

> This file is the shared brain for all agents working on SandboxShift.
> Every agent reads this before doing anything. Never contradict decisions made here.
> When a decision is made by the human, record it in the Decisions Log below.

---

## What Is SandboxShift?

SandboxShift is a **self-hosted AI agent sandbox with automatic local/cloud bursting**.

### One-line pitch
> "Run AI agent sandboxes locally. When your machine can't handle it, it automatically
> bursts to your own AWS. Your data never touches anyone else's servers."

---

## The Problem It Solves

When AI agents (Claude Code, any LLM agent) run on a developer's machine they can:
- Read any file including SSH keys, AWS credentials, .env files
- Make any network call to anywhere
- Destroy the filesystem
- Freeze an 8GB machine running heavy models

Existing solutions either:
- Own your infrastructure (E2B, Modal, Daytona) — your code goes to their servers
- Only work locally (Docker Sandboxes, DevContainers) — machine still gets crushed
- Are too complex (Kubernetes Agent Sandbox) — not developer friendly

### Our Differentiator
```
Everyone else:  Your code → Their cloud → Their servers
SandboxShift:   Your code → Your local OR Your AWS → You own everything
```

---

## Core Features

### 1. Auto Burst (The Key Innovation)
- Local RAM sufficient → Run sandbox locally (free)
- Local RAM tight → Burst to YOUR AWS Fargate (cents per session)
- Decision made UPFRONT before task starts — no mid-execution switching
- If task fails mid-way → checkpoint saved → resume from checkpoint (V1)
- Seamless mid-execution migration → V2 feature

### 2. Sensitive Data Detection (4 Layers)
- Layer 1: File pattern matching (.env, .pem, ~/.aws, ~/.ssh)
- Layer 2: Content scanning (API keys, passwords, internal IPs)
- Layer 3: User policy file (.sandboxshift/policy.yaml)
- Layer 4: LLM classifier using tiny local model (V2 feature)
- Result: Sensitive task? Force local. Always. Explain why to user.

### 3. Observability (The Gap Nobody Fills)
- Full audit trail: which files read/written, commands run, network calls attempted
- Why each decision was made
- Where it ran (local vs cloud) and cost per session
- Human-readable, exportable for compliance

### 4. Model Agnostic
- Works with any LLM: Ollama, Claude API, OpenAI, any OpenAI-compatible endpoint
- The sandbox wraps any agent — not tied to one LLM

---

## Tech Stack (Locked Decisions)

```
Core API:        FastAPI (Python)
Local Runtime:   Podman (rootless, daemonless — better security than Docker)
Security Layer:  gVisor (syscall interception on top of Podman)
Base Images:     Chainguard (zero-CVE base images)
Cloud Burst:     AWS Fargate (your AWS account, pay per use)
IaC:             Terraform (provisions AWS resources)
Observability:   OpenTelemetry → CloudWatch / Grafana
CLI:             Python CLI (sandboxshift run ...)
LLM:             Pluggable — Ollama or any API
State:           Local JSON + S3 for cloud sessions
```

---

## Pre-Built Runtime Images

SandboxShift ships ready-made images. NO Dockerfile needed from users.

```
sandboxshift/runtime-python:3.11
sandboxshift/runtime-node:20
sandboxshift/runtime-java:21
sandboxshift/runtime-go:1.22
sandboxshift/runtime-rust:latest
sandboxshift/runtime-multi        ← python + node + go in one image
```

Auto-detection logic:
- Found requirements.txt → python runtime
- Found package.json → node runtime
- Found pom.xml / build.gradle → java runtime
- Found go.mod → go runtime
- Found multiple → multi runtime

---

## Deployment Modes

```
--mode local    → Pure Podman on developer's machine
--mode cloud    → Pure AWS Fargate (their account)
--mode auto     → Decides based on available RAM (DEFAULT)
```

---

## Sandbox Configuration (sandboxshift.yaml)

```yaml
sandbox:
  runtime: auto           # auto-detected from workspace
  timeout: 1800           # kill after 30 mins
  mode: auto              # local first, cloud if needed

workspace:
  mount: ./src            # only this folder
  readonly: false

network:
  allow:
    - pypi.org
    - api.github.com
  block_all_others: true

resources:
  cpu: 2
  memory: 4GB

sensitivity:
  level: auto             # auto-detect sensitive data
```

---

## Security Architecture (7 Layers — Defence in Depth)

```
Layer 1: Chainguard base image     → Zero CVEs inside container
Layer 2: Podman rootless           → No root daemon
Layer 3: gVisor                    → Syscall interception
Layer 4: Network policy            → Whitelist only approved endpoints
Layer 5: Resource limits (cgroups) → CPU/RAM caps enforced
Layer 6: Sensitive data detection  → Never send secrets to cloud
Layer 7: Audit trail               → Full record of all agent actions
```

---

## Target Users

```
V1: Individual DevOps/platform engineer (primary — build for this person first)
V2: Small teams and startups
V3: Enterprises in regulated industries (banks, hospitals, defense)
    → K8s mode, compliance logs, FIPS, air-gapped deployment
```

---

## Repository Structure

```
sandboxshift/
├── AGENTS.md                    ← this file (shared agent brain)
├── README.md                    ← user-facing docs
├── pyproject.toml               ← project metadata + dev dependencies
├── sandboxshift.yaml            ← example config
├── .github/
│   ├── agents/                  ← custom agent definitions
│   │   ├── architect.agent.md
│   │   ├── coder.agent.md
│   │   ├── docs.agent.md
│   │   ├── reviewer.agent.md
│   │   └── security.agent.md
│   └── ISSUE_TEMPLATE/
│       └── blocked.md           ← agent raises issues here when blocked
├── architecture/                ← ADRs and system design docs
│   └── decisions/
│       ├── ADR-001-system-architecture.md
│       ├── ADR-002-sensitivity-scanner.md
│       ├── ADR-003-burst-engine.md
│       └── ADR-004-podman-runtime.md
├── src/
│   ├── config.py                ← SandboxConfig dataclass (shared by all runtimes)
│   ├── api/                     ← FastAPI endpoints
│   ├── observability/           ← audit trail, metrics
│   │   ├── __init__.py
│   │   └── audit.py             ← AuditLogger (V1 stub; replaced in Prompt 6)
│   ├── sandbox/                 ← core sandbox logic
│   │   ├── runtime/             ← podman, gvisor, fargate adapters ✓ BUILT (podman)
│   │   │   ├── __init__.py
│   │   │   ├── base.py          ← Runtime ABC + TaskResult
│   │   │   └── podman.py        ← PodmanRuntime ✓ BUILT
│   │   ├── burst/               ← burst decision engine ✓ BUILT
│   │   │   ├── __init__.py
│   │   │   └── engine.py
│   │   └── detection/           ← sensitive data detection ✓ BUILT
│   │       ├── __init__.py
│   │       └── sensitivity.py
│   └── cli/                     ← sandboxshift CLI
├── terraform/                   ← AWS infrastructure
├── images/                      ← Chainguard-based runtime images
├── tests/
│   └── sandbox/
│       ├── runtime/             ← PodmanRuntime tests ✓ BUILT
│       │   └── test_podman.py
│       ├── burst/               ← BurstEngine tests ✓ BUILT
│       │   └── test_engine.py
│       └── detection/           ← SensitivityScanner tests ✓ BUILT
│           └── test_sensitivity.py
└── docs/
    ├── index.md
    └── components/
        ├── burst-engine.md
        ├── podman-runtime.md
        └── sensitivity-scanner.md
```

---

## Build Phases

### Phase 1 — V1 (Current Focus)
- [ ] Core FastAPI server
- [x] Podman sandbox adapter (local mode) — **COMPLETE** (2026-03-14)
- [x] Burst decision engine (RAM check → local or cloud) — **COMPLETE** (2026-03-14)
- [ ] AWS Fargate adapter (cloud mode)
- [x] Sensitive data detection (Layer 1 + 2) — **COMPLETE** (2026-03-08)
- [ ] Basic audit trail
- [ ] Python CLI (sandboxshift run)
- [ ] Pre-built runtime images (python, node, multi)
- [ ] Terraform for AWS setup
- [ ] README and getting started docs

### Phase 2 — V2
- [ ] gVisor integration
- [ ] Checkpoint + resume mid-execution migration
- [ ] LLM-based sensitivity classifier
- [ ] MCP server (plug into Claude Desktop, Cursor)
- [ ] Grafana dashboard
- [ ] Java + Go + Rust runtimes

### Phase 3 — V3
- [ ] Kubernetes mode (Helm chart)
- [ ] Firecracker microVM support
- [ ] FIPS compliance
- [ ] Air-gapped deployment mode
- [ ] Multi-tenant support
- [ ] Compliance audit export (SOC2, ISO27001)

---

## Decisions Log

> All decisions made by Nihal (human owner) are recorded here.
> Agents must never re-open a closed decision unless Nihal explicitly says so.

| # | Decision | Choice | Reason | Date |
|---|----------|--------|--------|------|
| 1 | Local runtime engine | Podman | Rootless, daemonless, K8s-native | 2026-03-07 |
| 2 | Base images | Chainguard | Zero CVEs, SBOM, supply chain security | 2026-03-07 |
| 3 | Cloud provider | AWS Fargate | Nihal has AWS experience, pay-per-use | 2026-03-07 |
| 4 | Core language | Python/FastAPI | Nihal's existing strength | 2026-03-07 |
| 5 | V1 switching strategy | Decide upfront, fail gracefully | Simpler, reliable, no data loss | 2026-03-07 |
| 6 | Sensitivity detection | 4-layer approach | Layered, explain decisions to user | 2026-03-07 |
| 7 | IaC tool | Terraform | Nihal's existing experience | 2026-03-07 |
| 8 | Project name | SandboxShift | Describes local/cloud shifting | 2026-03-07 |
| 9 | SensitivityScanner fail behaviour | Fail-closed (OSError → FORCE_LOCAL) | Scan error must never silently allow cloud execution | 2026-03-08 |
| 10 | .aws/.ssh detection strategy | Check parent dir components, not filename | rglob returns files only; directories themselves are never matched | 2026-03-08 |
| 11 | Python project config | pyproject.toml (PEP 621) | Standard, ruff/mypy/pytest config in one place; no requirements.txt sprawl | 2026-03-08 |
| 12 | BurstEngine FORCE_LOCAL enforcement | BurstEngine enforces it (not SandboxManager) | Single point of enforcement; testable and auditable in isolation | 2026-03-14 |
| 13 | RAM reading library | psutil.virtual_memory().available | Cross-platform (Linux + macOS); .available is correct metric (not .total or .free) | 2026-03-14 |
| 14 | BurstDecision mutability | frozen=True dataclass | Prevents post-decision tampering; safe to pass across coroutines | 2026-03-14 |
| 15 | BurstEngine RAM failure behaviour | Fail-closed (RuntimeError → mode=local, confidence=forced) | Unknown RAM state must never allow cloud execution | 2026-03-14 |
| 16 | Default RAM threshold | 4 GB (configurable at BurstEngine construction time) | Sufficient for typical Python/Node agent sandbox on 8 GB developer machine | 2026-03-14 |
| 17 | PodmanRuntime subprocess interface | stdlib subprocess (not podman-py) | Zero dependencies; trivially mockable in tests; CLI is transparent and auditable | 2026-03-14 |
| 18 | PodmanRuntime network policy | slirp4netns + --dns=none + pre-resolved --add-host | Only rootless-compatible option; DNS blocked at container level; IPs resolved once at provision time | 2026-03-14 |
| 19 | PodmanRuntime image selection | Workspace marker auto-detection (_detect_image) | Zero-config UX; multiple markers → runtime-multi; no user-supplied image override | 2026-03-14 |
| 20 | AuditLogger V1 | No-op stub in src/observability/audit.py | Placeholder for real implementation in Prompt 6 (basic audit trail); all callers already wired | 2026-03-14 |
| 21 | Shared config dataclass | SandboxConfig in src/config.py | Single source of truth for all runtimes (PodmanRuntime, FargateRuntime, SandboxManager) | 2026-03-14 |

---

## Rules All Agents Must Follow

1. **Never lose context** — always read this file before starting any task
2. **Never re-open closed decisions** — check Decisions Log first
3. **Minimal scope** — do only what the task asks, nothing more
4. **Always write tests** — no code without tests
5. **Raise issues when blocked** — use the blocked issue template, never guess
6. **Commit small** — one logical change per commit
7. **Document decisions** — if you make a minor decision, add it to Decisions Log
8. **Security first** — never suggest weakening any of the 7 security layers
9. **V1 focus** — do not implement V2/V3 features unless explicitly asked
10. **Ask before deleting** — never delete files without human confirmation

---

## How Agents Communicate With Nihal

When any agent is blocked and needs a human decision:
1. Create a GitHub Issue using the blocked issue template
2. Title format: `🚫 BLOCKED: [Agent Name] — [Short question]`
3. Label: `needs-human-decision`
4. Nihal gets notified by email automatically
5. Nihal replies in the issue comments
6. Agent reads reply and continues
7. Agent updates Decisions Log with the outcome

**Nihal's contact:** GitHub issue notifications → email
**Response time expectation:** Nihal will reply when available — agents should continue other unblocked tasks while waiting.
