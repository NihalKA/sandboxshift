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
Security Layer:  gVisor (syscall interception on top of Podman) — V2
Base Images:     Docker Hub official slim (python:3.11-slim, node:20-slim)
                 Chainguard planned for V2 (zero-CVE, SBOM)
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
sandboxshift/runtime-java:21       ← V2
sandboxshift/runtime-go:1.22       ← V2
sandboxshift/runtime-rust:latest   ← V2
sandboxshift/runtime-multi         ← python + node in one image (V1)
```

Auto-detection logic (Decision #19):
- Found requirements.txt → python runtime
- Found package.json → node runtime
- Found multiple → multi runtime
- pom.xml / build.gradle → java runtime (V2)
- go.mod → go runtime (V2)

---

## Deployment Modes

```
--mode local    → Pure Podman on developer's machine
--mode cloud    → Pure AWS Fargate (their account)
--mode auto     → Decides based on available RAM (DEFAULT)
```

YAML equivalent: `sandbox.mode: local|cloud|auto` (CLI `--mode` wins when explicitly set).

---

## Sandbox Configuration (sandboxshift.yaml)

```yaml
sandbox:
  runtime: auto           # auto-detected from workspace
  timeout: 1800           # kill after 30 mins
  mode: auto              # local | cloud | auto (default: auto)

workspace:
  mount: ./src            # only this folder
  readonly: false

network:
  allow:
    - pypi.org
    - api.github.com
  # Use ["*"] to allow all outbound traffic (disables Security Layer 4)

resources:
  cpu: 2
  memory: 4GB
  min_cpu: 4              # burst to cloud if local has fewer CPUs
  min_memory: 8GB         # burst to cloud if local has less available RAM

sensitivity:
  level: auto             # auto-detect sensitive data
```

---

## Security Architecture (7 Layers — Defence in Depth)

```
Layer 1: Official slim base image  → Minimal packages, non-root user (UID 10000)
                                     (Chainguard zero-CVE images planned for V2)
Layer 2: Podman rootless           → No root daemon
Layer 3: gVisor                    → Syscall interception (V2)
Layer 4: Network policy            → Whitelist only approved endpoints
Layer 5: Resource limits (cgroups) → CPU/RAM caps enforced
Layer 6: Sensitive data detection  → Never send secrets to cloud
Layer 7: Audit trail               → Full record of all agent actions
```

---

## Target Users

```
V1: Individual developer (primary — build for this person first)
V2: Small teams and startups
V3: Enterprises in regulated industries (banks, hospitals, defense)
    → K8s mode, compliance logs, FIPS, air-gapped deployment
```

---

## Repository Structure

```
sandboxshift/
├── AGENTS.md                    ← this file (shared agent brain)
├── README.md                    ← user-facing docs ✓ BUILT
├── sandboxshift-setup.sh        ← one-script setup ✓ BUILT
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
│       ├── ADR-004-podman-runtime.md
│       └── ADR-005-fargate-runtime.md
├── src/
│   ├── config.py                ← SandboxConfig dataclass (shared by all runtimes)
│   ├── config_loader.py         ← load_workspace_config() — parses sandboxshift.yaml
│   ├── api/                     ← FastAPI endpoints ✓ BUILT
│   │   ├── __init__.py          ← exports create_app
│   │   ├── models.py            ← RunRequest, RunResponse, HealthResponse, AuditEntry
│   │   ├── routes.py            ← POST /run, GET /health, GET /audit
│   │   └── app.py               ← create_app() factory with async lifespan wiring
│   ├── cli/                     ← sandboxshift CLI ✓ BUILT
│   │   ├── __init__.py          ← exports main
│   │   └── main.py              ← argparse CLI: run + list + stop + audit tail subcommands
│   ├── observability/           ← audit trail, metrics
│   │   ├── __init__.py
│   │   └── audit.py             ← AuditLogger ✓ BUILT
│   ├── sandbox/                 ← core sandbox logic
│   │   ├── __init__.py          ← exports SandboxManager, RunResult
│   │   ├── manager.py           ← SandboxManager + RunResult ✓ BUILT
│   │   ├── runtime/             ← podman, fargate adapters ✓ BUILT
│   │   │   ├── __init__.py
│   │   │   ├── base.py          ← Runtime ABC + TaskResult
│   │   │   ├── podman.py        ← PodmanRuntime ✓ BUILT
│   │   │   └── fargate.py       ← FargateRuntime ✓ BUILT
│   │   ├── burst/               ← burst decision engine ✓ BUILT
│   │   │   ├── __init__.py
│   │   │   └── engine.py
│   │   └── detection/           ← sensitive data detection ✓ BUILT
│   │       ├── __init__.py
│   │       └── sensitivity.py
├── terraform/
│   └── fargate/                 ← AWS infrastructure for FargateRuntime ✓ BUILT
│       ├── main.tf              ← ECS cluster, task def, IAM, SG, CW log group, S3 bucket
│       ├── variables.tf         ← all Terraform input variables
│       ├── outputs.tf           ← outputs matching FargateRuntime constructor params
│       └── README.md            ← prerequisites, quick start, env var wiring ✓ BUILT
├── images/                      ← Docker Hub official slim runtime images ✓ BUILT
│   ├── Makefile                 ← build-python, build-node, build-multi, push-all
│   ├── README.md                ← image strategy, selection table, security properties
│   ├── python/
│   │   ├── Dockerfile           ← python:3.11-slim (Docker Hub official)
│   │   └── README.md
│   ├── node/
│   │   ├── Dockerfile           ← node:20-slim (Docker Hub official)
│   │   └── README.md
│   └── multi/
│       ├── Dockerfile           ← python:3.11-slim + NodeSource nodejs 20
│       └── README.md
├── tests/
│   ├── api/                     ← FastAPI layer tests ✓ BUILT
│   │   ├── __init__.py
│   │   └── test_routes.py       ← 29 tests across 8 groups
│   ├── cli/                     ← CLI tests ✓ BUILT
│   │   ├── __init__.py
│   │   └── test_main.py         ← tests across 10 groups
│   ├── observability/           ← AuditLogger tests ✓ BUILT
│   │   ├── __init__.py
│   │   └── test_audit.py        ← AuditLogger tests ✓ BUILT (18 tests)
│   └── sandbox/
│       ├── test_manager.py      ← SandboxManager tests ✓ BUILT (23 tests across 7 groups)
│       ├── runtime/
│       │   ├── test_podman.py   ← PodmanRuntime tests ✓ BUILT (46 tests across 9 groups)
│       │   └── test_fargate.py  ← FargateRuntime tests ✓ BUILT (32 tests)
│       ├── burst/               ← BurstEngine tests ✓ BUILT
│       │   └── test_engine.py
│       └── detection/           ← SensitivityScanner tests ✓ BUILT
│           └── test_sensitivity.py
└── docs/
    ├── index.md                 ← component index ✓ BUILT
    ├── getting-started.md       ← install → first run → cloud burst ✓ BUILT
    ├── configuration.md         ← full sandboxshift.yaml + env var reference ✓ BUILT
    └── components/
        ├── burst-engine.md
        ├── podman-runtime.md
        ├── sensitivity-scanner.md
        └── fargate-runtime.md   ← FargateRuntime docs ✓ BUILT
```

---

## Build Phases

### Phase 1 — V1 (Current Focus)
- [x] Core FastAPI server — **COMPLETE** (2026-03-14)
- [x] Podman sandbox adapter (local mode) — **COMPLETE** (2026-03-14)
- [x] Burst decision engine (RAM check → local or cloud) — **COMPLETE** (2026-03-14)
- [x] AWS Fargate adapter (cloud mode) — **COMPLETE** (2026-03-14)
- [x] Sensitive data detection (Layer 1 + 2) — **COMPLETE** (2026-03-08)
- [x] SandboxManager (orchestrator: scan → burst → runtime → provision/execute/destroy) — **COMPLETE** (2026-03-14)
- [x] Basic audit trail — **COMPLETE** (2026-03-14)
- [x] Python CLI (sandboxshift run) — **COMPLETE** (2026-03-14)
- [x] Pre-built runtime images (python, node, multi) — **COMPLETE** (2026-03-14)
- [x] Terraform for AWS setup — **COMPLETE** (2026-03-14)
- [x] README and getting started docs — **COMPLETE** (2026-03-14)
- [x] One-script setup (sandboxshift-setup.sh) — **COMPLETE** (2026-03-21)

### **V1 IS COMPLETE.**

### Phase 2 — V2
- [ ] gVisor integration
- [ ] Chainguard base images (zero-CVE, SBOM, supply chain security)
- [ ] Checkpoint + resume mid-execution migration
- [ ] LLM-based sensitivity classifier
- [ ] MCP server (plug into Claude Desktop, Cursor)
- [ ] Grafana dashboard
- [ ] Java + Go + Rust runtimes
- [ ] Homebrew tap distribution

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
| 2 | Base images | ~~Chainguard~~ → Docker Hub official slim (superseded by #61) | Original intent; changed due to shell availability — see #61 | 2026-03-07 |
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
| 22 | FargateRuntime credential model | boto3.Session() with no constructor creds | IAM role / AWS_PROFILE / env vars only; constructor never accepts aws_access_key_id or aws_secret_access_key | 2026-03-14 |
| 23 | Workspace S3 transfer method | put_object per file (not multipart) | V1 simplicity; 500 MB workspace cap makes multipart unnecessary | 2026-03-14 |
| 24 | CloudWatch log retrieval | get_log_events post-stop | Simplest V1 approach; stdout/stderr combined; avoids streaming complexity | 2026-03-14 |
| 25 | S3 bucket encryption | AES256 SSE (not KMS) | No KMS key management overhead for V1; AES256 is sufficient and free | 2026-03-14 |
| 26 | Missing ECS exitCode sentinel | -1 with audit warning | Distinguishes "process exited 0" from "exitCode unavailable"; auditable | 2026-03-14 |
| 27 | V1 image selection in FargateRuntime | Passthrough — task definition controls image | ECS task def pins the image; workspace marker detection is audit-only in V1 | 2026-03-14 |
| 28 | ECS poll interval | 5-second asyncio.sleep | Balances responsiveness vs API call cost; configurable upgrade in V2 | 2026-03-14 |
| 29 | SandboxManager runtime construction | Dependency injection — pre-constructed Runtime instances passed to __init__ | Keeps FargateRuntime's AWS-specific params (cluster_arn, subnet_ids, …) out of SandboxManager; mirrors Decision #12 single-responsibility pattern; trivially mockable in tests | 2026-03-14 |
| 30 | RunResult.duration_seconds scope | Entire run() wall time (scan + decide + provision + execute + destroy) | Gives operators a single duration metric for billing/alerting; matches user mental model of "how long did my sandbox run?" | 2026-03-14 |
| 31 | run() exception propagation on execute failure | Exception propagates after destroy() runs; no RunResult returned; run_complete not emitted | Consistent with fail-closed principle; callers must handle exceptions explicitly; partial results are never returned | 2026-03-14 |
| 32 | AuditLogger format | JSONL append-to-file (not stdout, not structured logging library) | V1 simplicity; human-readable with `jq`; zero new dependencies; append-only preserves full history; default path ~/.sandboxshift/audit.log | 2026-03-14 |
| 33 | AuditLogger thread safety | threading.Lock per-instance (not global) | Each AuditLogger owns its lock; no shared mutable state between instances; compatible with asyncio (called from sync context inside coroutines) | 2026-03-14 |
| 34 | AuditLogger failure behaviour | record() never raises; entire try block catches Exception (not just OSError) | Audit failure must never crash the runtime; broadened from OSError to Exception to also catch ValueError from circular refs in event dict (json.dumps edge case) | 2026-03-14 |
| 35 | API app instantiation | create_app() factory only — no module-level app instance | Enables clean dependency injection in tests; prevents shared state across test runs | 2026-03-14 |
| 36 | POST /run HTTP error mapping | Non-zero task exit_code → HTTP 200; Python exception → HTTP 500 | Exit code is a task result, not an API error; callers inspect exit_code to determine success | 2026-03-14 |
| 37 | GET /audit JSONL parsing | Invalid lines silently skipped; missing file → empty list | Malformed log lines must never break the audit endpoint; degraded output is better than 500 | 2026-03-14 |
| 38 | API workspace validation | Pydantic @field_validator: exists + resolves symlinks + rejects sensitive paths | Defence-in-depth at API boundary; SensitivityScanner is not the sole guard for path safety | 2026-03-14 |
| 39 | API allowed_hosts validation | FQDN-only; bare IPs rejected; link-local + private ranges blocked | Prevents SSRF against IMDS (169.254.169.254) and internal services reachable from sandbox network | 2026-03-14 |
| 40 | CLI argument parser | argparse (stdlib) — no Click, Typer, or Rich | Zero new runtime dependencies; ships with Python 3.11; no install friction for users | 2026-03-14 |
| 41 | CLI Fargate wiring | Mirrors api/app.py exactly — same 6 env vars, same None-if-missing logic | Single mental model for operators; env var names documented in one place (AGENTS.md) | 2026-03-14 |
| 42 | CLI audit log path resolution | Priority: --audit-log arg → SANDBOXSHIFT_AUDIT_LOG env var → ~/.sandboxshift/audit.log | Consistent with API layer default; env var allows CI override without code changes | 2026-03-14 |
| 43 | CLI async entry point | Named coroutine _run_async() called via asyncio.run() in _cmd_run() | Keeps async surface testable with AsyncMock; hides event loop management from tests | 2026-03-14 |
| 44 | CLI subcommand structure | argparse nested subparsers: sandboxshift {run, audit {tail}} | Extensible for future subcommands (audit export, config validate); matches POSIX conventions | 2026-03-14 |
| 45 | CLI --allow validation | FQDN-only via _validate_allow_hosts(); bare IPs rejected at CLI boundary | CLI users bypass models.py — duplicate guard required to preserve Layer 4 for direct CLI execution | 2026-03-14 |
| 46 | CLI --memory-mb / --cpu bounds | Post-parse validation: memory 128–65536 MB, cpu 0.25–64.0 | CLI users bypass models.py le= constraints; prevents crash-the-host via pathological cgroup values | 2026-03-14 |
| 47 | Runtime image shell requirement | SUPERSEDED by #61 | Was: Chainguard :latest-dev for shell; switched to Docker Hub official slim which has /bin/sh natively | 2026-03-14 |
| 48 | Multi-runtime base image | SUPERSEDED by #61 | Was: cgr.dev/chainguard/wolfi-base; switched to python:3.11-slim + NodeSource | 2026-03-14 |
| 49 | Port exposure YAML loader | `load_workspace_config()` in `src/config_loader.py`; CLI loads it before arg merge; API does not use it | CLI-only in V1; API consumers build SandboxConfig directly | 2026-03-19 |
| 50 | Port host bind address | Always `127.0.0.1` (never `0.0.0.0`) on host side | Prevents exposing sandbox ports on LAN or public interfaces | 2026-03-19 |
| 51 | Streaming subprocess trigger | `subprocess.Popen` + line stream used when `config.ports` non-empty; `subprocess.run(capture_output=True)` kept otherwise | Long-running servers need real-time output; batch tasks benefit from captured stdout | 2026-03-19 |
| 52 | Port conflict detection | `_check_port_available(host_port)` called in `provision()` before container starts; raises `OSError` | Fail-fast before container starts; avoids silent bind failure | 2026-03-19 |
| 53 | PodmanRuntime entrypoint override | Always pass `--entrypoint /bin/sh` before the image name; container command is then `-c <task>` | python:3.11-slim sets ENTRYPOINT ["python"]; without override /bin/sh -c task becomes python /bin/sh -c task, crashing with SyntaxError | 2026-03-19 |
| 54 | Unrestricted network mode | `network_allow: ["*"]` → slirp4netns without --dns=none, no --add-host; audited as network_unrestricted_mode with warning | Opt-in escape hatch for trusted workspaces that need arbitrary internet (e.g. npm install from many CDN hosts); intentionally weakens Layer 4; always logged so the operator can see it happened | 2026-03-20 |
| 55 | min_cpu_required / min_memory_mb_required | SandboxConfig fields (default 0/0.0 = disabled); BurstEngine checks after FORCE_LOCAL, before RAM threshold; violation → cloud, confidence=forced; CPU read failure → cloud, confidence=forced (fail-closed) | Explicit resource minimums are hard requirements — if local can't satisfy them, cloud is the only valid target; CPU read failure treated same as unsatisfied requirement (fail-closed principle); YAML keys: `resources.min_cpu` and `resources.min_memory` | 2026-03-20 |
| 56 | PORT env var auto-injection | When `config.ports` is non-empty, inject `PORT=<container_port>` into container env (Podman via `--env PORT=N`; Fargate via `containerOverrides.environment`); uses first configured container port | Apps read `process.env.PORT` (Node) or `$PORT` (shell) without hardcoding the port number; consistent between local and cloud runtimes | 2026-03-20 |
| 57 | Bare `--port N` CLI shorthand | `_parse_port()` accepts bare integer N and expands to `(N, N)` (host=N, container=N); `HOST:CONTAINER` form still accepted | Removes need to type `--port 3000:3000`; bare number is the common case; HOST:CONTAINER retained for port remapping | 2026-03-20 |
| 58 | S3 upload skip dirs | `_SKIP_DIRS` frozenset in `fargate.py`; `node_modules`, `__pycache__`, `.venv`, `venv`, `env`, `.pytest_cache`, `.tox`, `.eggs`, `dist`, `build`, `.next`, `.nuxt` never uploaded; deps reinstalled in ECS by `_S3_DEPS_BOOTSTRAP`; 500MB cap re-checked against filtered set | node_modules alone can be 6000+ files / hundreds of MB; uploading them wastes S3 bandwidth and time; they are platform-specific (Linux container ≠ macOS host) anyway so uploading would break native addons | 2026-03-21 |
| 59 | Terraform distribution in setup script | Always download pinned Terraform 1.5.7 to `~/.sandboxshift/bin/terraform` using Python `urllib` + `zipfile` (no curl, no unzip, no system Terraform required); version cached — skipped if already correct; all `terraform` invocations in setup script use `$TF_BIN` | Eliminates version mismatch bugs entirely; Python is the only binary dependency needed to bootstrap the download; consistent behaviour regardless of whether user has Terraform installed | 2026-03-21 |
| 60 | Python venv isolation in setup script | Create isolated venv at `~/.sandboxshift/venv/`; install sandboxshift into it; symlink CLI to `~/.sandboxshift/bin/sandboxshift`; user adds `~/.sandboxshift/bin` to PATH once | Keeps user's global Python env clean; single PATH entry exposes both `sandboxshift` CLI and `terraform` binary; works for all developers not just DevOps | 2026-03-21 |
| 61 | V1 base images | Docker Hub official slim: `python:3.11-slim`, `node:20-slim`, multi = `python:3.11-slim` + NodeSource nodejs 20 | Chainguard distroless has no shell; :latest-dev variant (adds BusyBox) proved fragile in practice; Docker Hub official slim images have /bin/sh, apt, pip natively and are always publicly available without auth; non-root UID 10000 added in Dockerfile for Layer 2 security; Chainguard deferred to V2 | 2026-03-21 |
| 62 | Fargate per-run CPU/memory | Pass `cpu_limit` (×1024 → ECS CPU units string) and `memory_limit_mb` (string) as task-level `overrides.cpu`/`overrides.memory` in `ecs.run_task()` | Allows per-run resource sizing without modifying the Terraform task definition; `resources.cpu`/`resources.memory` now work consistently for both local (Podman cgroups) and cloud (ECS task override); Fargate requires valid CPU/memory combinations — invalid combos fail fast at ECS level | 2026-03-22 |
| 63 | `--mode local/cloud/auto` CLI flag + `sandbox.mode` YAML key | `--mode local` → `BurstEngine(ram_threshold_gb=0.0)` (available RAM always ≥ 0 → always local); `--mode cloud` → `BurstEngine(ram_threshold_gb=float("inf"))` (available RAM never ≥ ∞ → always cloud); `--mode auto` (default) → check YAML `sandbox.mode` key (parsed as `sandbox_mode` in config loader dict), then fall back to `--ram-threshold`. CLI `--mode` always wins over YAML. Neither `--mode cloud` nor `sandbox.mode: cloud` can override sensitivity FORCE_LOCAL (Layer 6 is immutable). No changes to `SandboxConfig`, `BurstEngine`, or `SandboxManager` — translation is done entirely in `_run_async()` in `cli/main.py`. | 2026-03-22 |

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
