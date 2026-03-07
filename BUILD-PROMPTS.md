# SandboxShift — V1 Build Prompts

8 prompts in order. One per session.
Always use the **Orchestrator** agent.
Wait for each to fully complete before starting the next.

---

## How To Use This File

1. Open Copilot Chat in VS Code
2. Select **Orchestrator** from the agent dropdown
3. Copy the prompt for the current session
4. Paste it and press Enter
5. Lock your screen — come back when you get an email or it reports done
6. Review what was built, merge if happy
7. Move to the next prompt

---

## Prompt 1 — SensitivityScanner

> **What it builds:** The sensitive data detector. Scans workspace for secrets,
> credentials, and sensitive files BEFORE any cloud decision is made.
> This is Layer 6 of the security model and must be built first.

```
Read AGENTS.md and architecture/decisions/ADR-001-system-architecture.md
to fully understand the project before doing anything.

Build the SensitivityScanner component for SandboxShift V1.

This component:
- Scans a given workspace directory for sensitive data
- Detects sensitive FILE PATTERNS: .env, *.pem, *.key, *.p12,
  credentials.json, *secret*, *token*, ~/.aws, ~/.ssh
- Detects sensitive CONTENT PATTERNS: AWS keys (AKIA[0-9A-Z]{16}),
  private key headers (-----BEGIN * PRIVATE KEY-----),
  password= or secret= assignments, internal IP ranges (10.x, 192.168.x)
- Returns: { is_sensitive: bool, findings: list[str], recommendation: "force_local" | "allow_cloud" }
- Always explains WHY it flagged something

Files to create:
  src/sandbox/detection/sensitivity.py
  src/sandbox/detection/__init__.py
  tests/sandbox/detection/test_sensitivity.py

Follow the full agent chain:
  Planner → ADR-002 → Coder → Reviewer → Security scan → Docs

Do not proceed to implementation until ADR-002 is written.
Do not mark done until Reviewer approves and Security scan is clear.
Report back when fully complete with a summary of what was built.
```

---

## Prompt 2 — BurstEngine

> **What it builds:** The decision engine that decides LOCAL vs CLOUD
> before every sandbox run. Uses SensitivityScanner result + available RAM.

```
Read AGENTS.md and all ADRs in architecture/decisions/ before starting.

Build the BurstEngine component for SandboxShift V1.

This component:
- Takes two inputs: SensitivityResult (from SensitivityScanner) + available system RAM
- Decision logic:
    if sensitivity.is_sensitive → force LOCAL, reason: "sensitive data detected"
    elif available_ram_gb >= threshold → run LOCAL, reason: "sufficient RAM"
    else → burst to CLOUD, reason: "insufficient RAM ({available}GB < {threshold}GB)"
- Default RAM threshold: 4GB (configurable via sandboxshift.yaml)
- Returns: { mode: "local" | "cloud", reason: str, confidence: "forced" | "preferred" }
- "forced" = sensitivity triggered (cannot be overridden)
- "preferred" = RAM-based decision (can be overridden with --force-local or --force-cloud)

Files to create:
  src/sandbox/burst/engine.py
  src/sandbox/burst/__init__.py
  tests/sandbox/burst/test_engine.py

Imports and uses: SensitivityScanner from src/sandbox/detection/sensitivity.py

Follow the full agent chain:
  Planner → ADR-003 → Coder → Reviewer → Security scan → Docs

Do not mark done until Reviewer approves and Security scan is clear.
Report back when fully complete with a summary of what was built.
```

---

## Prompt 3 — PodmanRuntime

> **What it builds:** The local sandbox runtime using Podman.
> Runs agent tasks in isolated rootless containers on the developer's machine.

```
Read AGENTS.md and all ADRs in architecture/decisions/ before starting.

Build the PodmanRuntime component for SandboxShift V1.

This component:
- Implements the abstract Runtime interface (create base.py first if it doesn't exist)
- Provisions a rootless Podman container using Chainguard base images
- Auto-detects the right runtime image from workspace:
    requirements.txt → cgr.dev/chainguard/python:latest
    package.json     → cgr.dev/chainguard/node:latest
    go.mod           → cgr.dev/chainguard/go:latest
    multiple found   → sandboxshift/runtime-multi (note: this image is planned, mock it for now)
    none found       → cgr.dev/chainguard/python:latest (default)
- Mounts ONLY the specified workspace directory (nothing else)
- Enforces resource limits: CPU and RAM caps from sandboxshift.yaml
- Enforces network policy: default deny-all, only whitelisted domains allowed
- All actions logged via AuditLogger stub (we will complete AuditLogger in Prompt 6)
- Returns: TaskResult { exit_code: int, stdout: str, stderr: str, duration_seconds: float }

Security requirements (non-negotiable):
- NEVER use --privileged flag
- NEVER run as root inside container
- ALWAYS set CPU and RAM limits
- ALWAYS apply network policy

Files to create:
  src/sandbox/runtime/base.py        (abstract Runtime interface)
  src/sandbox/runtime/podman.py      (PodmanRuntime implementation)
  src/sandbox/runtime/__init__.py
  tests/sandbox/runtime/test_podman.py  (mock podman-py, no real containers in tests)

Follow the full agent chain:
  Planner → ADR-004 → Coder → Reviewer → Security scan → Docs

Do not mark done until Reviewer approves and Security scan is clear.
Report back when fully complete with a summary of what was built.
```

---

## Prompt 4 — FargateRuntime

> **What it builds:** The cloud burst runtime using AWS Fargate in the
> user's own AWS account. Only used when BurstEngine decides mode = "cloud".

```
Read AGENTS.md and all ADRs in architecture/decisions/ before starting.
Pay special attention to ADR-004 (Runtime interface) before implementing.

Build the FargateRuntime component for SandboxShift V1.

This component:
- Implements the same abstract Runtime interface as PodmanRuntime
- Provisions an AWS Fargate task in the user's own AWS account
- Uses the same Chainguard-based images as PodmanRuntime
- Reads AWS config from environment (AWS_PROFILE or IAM role) — never hardcoded
- Uploads workspace to a temporary S3 bucket (user's account), mounts into task
- Enforces the same network policy and resource limits as local mode
- Destroys the Fargate task AND S3 objects immediately after completion
- Returns same TaskResult format as PodmanRuntime

Security requirements (non-negotiable):
- Never accept AWS credentials as direct input — only from environment/profile
- Always destroy Fargate task after completion — no lingering resources
- Always delete S3 workspace objects after task completes
- Never run task with more IAM permissions than needed (principle of least privilege)
- Network policy: same whitelist as local mode

Files to create:
  src/sandbox/runtime/fargate.py
  tests/sandbox/runtime/test_fargate.py  (mock boto3 — no real AWS calls in tests)

Terraform files to create (skeleton only — full IaC in a later session):
  terraform/fargate/main.tf     (ECS cluster, task definition skeleton)
  terraform/fargate/variables.tf
  terraform/fargate/outputs.tf

Follow the full agent chain:
  Planner → ADR-005 → Coder → Reviewer → Security scan → Docs

Do not mark done until Reviewer approves and Security scan is clear.
Report back when fully complete with a summary of what was built.
```

---

## Prompt 5 — SandboxManager

> **What it builds:** The central orchestrator that wires all components together.
> This is the main entry point for running a sandbox task end to end.

```
Read AGENTS.md and ALL ADRs in architecture/decisions/ before starting.
All previous components must exist before building this.

Build the SandboxManager component for SandboxShift V1.

This component is the main orchestrator. It runs this lifecycle for every task:

  1. SensitivityScanner.scan(workspace)      → get sensitivity result
  2. BurstEngine.decide(sensitivity, ram)    → get mode decision (local/cloud)
  3. Log the decision with reason
  4. Runtime.provision(config)               → start sandbox (Podman or Fargate)
  5. Runtime.execute(task)                   → run the task
  6. Runtime.destroy()                       → always destroy, even on failure
  7. Return TaskResult + full audit summary

Key behaviours:
- Step 6 (destroy) MUST run even if step 5 fails — use try/finally
- If BurstEngine returns mode="cloud" but AWS is not configured → fail gracefully
  with clear message: "Cloud mode requires AWS configuration. Run: sandboxshift init --aws"
- Timeout enforcement: kill sandbox if task exceeds config timeout
- Config loaded from sandboxshift.yaml if present, defaults if not

Files to create:
  src/sandbox/manager.py
  src/sandbox/__init__.py
  tests/sandbox/test_manager.py  (mock all runtime and scanner dependencies)

Also create:
  src/config.py    (loads and validates sandboxshift.yaml using Pydantic)
  sandboxshift.yaml  (example config in repo root)

Follow the full agent chain:
  Planner → ADR-006 → Coder → Reviewer → Security scan → Docs

Do not mark done until Reviewer approves and Security scan is clear.
Report back when fully complete with a summary of what was built.
```

---

## Prompt 6 — AuditLogger

> **What it builds:** The full audit trail system. Every sandbox action is
> logged — what ran, where it ran, what it accessed, what it cost.

```
Read AGENTS.md and all ADRs in architecture/decisions/ before starting.

Build the AuditLogger component for SandboxShift V1.

This component:
- Logs every significant sandbox event as a structured JSON entry
- Events to log:
    SCAN_STARTED     { workspace, timestamp }
    SCAN_COMPLETE    { is_sensitive, findings, duration_ms }
    BURST_DECISION   { mode, reason, confidence, available_ram_gb }
    SANDBOX_START    { mode, image, resource_limits, network_policy }
    TASK_COMPLETE    { exit_code, duration_seconds, stdout_lines, stderr_lines }
    SANDBOX_DESTROY  { success, timestamp }
    ERROR            { stage, error_message, stack_trace }
- Writes to append-only log file: .sandboxshift/audit.log
- Log file is NOT writable from inside the sandbox (enforced by mount config)
- Also outputs human-readable summary at end of each run
- Each run gets a unique session_id (UUID)

Files to create:
  src/observability/audit.py
  src/observability/__init__.py
  tests/observability/test_audit.py

Then go back and wire AuditLogger into SandboxManager:
  Update src/sandbox/manager.py to use real AuditLogger
  (replace the stub that was used in Prompt 5)
  Update tests/sandbox/test_manager.py accordingly

Follow the full agent chain:
  Planner → ADR-007 → Coder → Reviewer → Security scan → Docs

Do not mark done until Reviewer approves and Security scan is clear.
Report back when fully complete with a summary of what was built.
```

---

## Prompt 7 — FastAPI Layer

> **What it builds:** The HTTP API that exposes SandboxShift over REST.
> Enables programmatic use via Python SDK or any HTTP client.

```
Read AGENTS.md and all ADRs in architecture/decisions/ before starting.

Build the FastAPI layer for SandboxShift V1.

Endpoints to create:

POST /sandbox/run
  Body: { task: str, workspace: str, config?: SandboxConfig }
  Response: { session_id: str, mode: str, result: TaskResult, audit_summary: list }

GET /sandbox/{session_id}/status
  Response: { session_id: str, status: "running" | "complete" | "failed", result?: TaskResult }

GET /health
  Response: { status: "ok", version: str, podman_available: bool, aws_configured: bool }

GET /audit/{session_id}
  Response: { session_id: str, events: list[AuditEvent] }

Requirements:
- All request/response bodies use Pydantic models
- Async endpoints throughout (async def)
- Proper HTTP status codes (422 for validation, 500 for runtime errors)
- Request validation with clear error messages
- OpenAPI docs auto-generated (FastAPI default — verify they work)
- No auth in V1 (local use only) — note this clearly in docs

Files to create:
  src/api/main.py              (FastAPI app, startup/shutdown)
  src/api/routes/sandbox.py    (sandbox endpoints)
  src/api/routes/health.py     (health endpoint)
  src/api/routes/audit.py      (audit endpoint)
  src/api/models/schemas.py    (all Pydantic models)
  src/api/__init__.py
  tests/api/test_sandbox.py
  tests/api/test_health.py

Follow the full agent chain:
  Planner → ADR-008 → Coder → Reviewer → Security scan → Docs

Do not mark done until Reviewer approves and Security scan is clear.
Report back when fully complete with a summary of what was built.
```

---

## Prompt 8 — CLI

> **What it builds:** The command line interface. The primary way users
> interact with SandboxShift. `sandboxshift run --task "..." --workspace ./src`

```
Read AGENTS.md and all ADRs in architecture/decisions/ before starting.

Build the CLI for SandboxShift V1.

Commands to implement:

sandboxshift run
  --task TEXT          Task description for the agent  [required]
  --workspace PATH     Directory to mount              [default: current dir]
  --mode TEXT          local | cloud | auto            [default: auto]
  --timeout INT        Seconds before killing sandbox  [default: 1800]
  --allow DOMAIN       Allow outbound domain (repeatable)
  --config PATH        Path to sandboxshift.yaml
  --dry-run            Show decision without running

sandboxshift init
  --aws-profile TEXT   AWS profile to use              [default: default]
  Sets up AWS resources via Terraform for cloud burst mode

sandboxshift audit
  --session-id TEXT    Show audit log for a session
  --last INT           Show last N sessions            [default: 10]

sandboxshift config
  --show               Print current resolved config
  --init               Create a sandboxshift.yaml in current directory

Requirements:
- Built with Click (not argparse)
- Rich library for pretty terminal output (tables, colours, progress)
- Clear error messages — never show a raw Python traceback to the user
- sandboxshift run should show a live progress indicator while sandbox runs
- Output modes: human-readable (default) and --json for scripting
- Entry point: sandboxshift (registered in pyproject.toml)

Files to create:
  src/cli/main.py
  src/cli/output.py     (Rich formatting helpers)
  src/cli/__init__.py
  tests/cli/test_cli.py (use Click's test runner)
  pyproject.toml        (package definition, dependencies, entry points)
  requirements.txt      (pinned dependencies)

After CLI is built, also create:
  docs/getting-started.md   (end-to-end: install → init → first run)
  docs/configuration.md     (full sandboxshift.yaml reference)

Follow the full agent chain:
  Planner → ADR-009 → Coder → Reviewer → Security scan → Docs

Do not mark done until Reviewer approves, Security scan is clear,
and `sandboxshift --help` works correctly.
Report back when fully complete with a full summary of V1.
```

---

## After All 8 Prompts — V1 Complete

When Prompt 8 is done, your repo will have:

```
src/
├── api/               ← FastAPI REST layer
├── sandbox/
│   ├── detection/     ← SensitivityScanner
│   ├── burst/         ← BurstEngine
│   ├── runtime/       ← PodmanRuntime + FargateRuntime
│   └── manager.py     ← SandboxManager (wires everything)
├── observability/     ← AuditLogger
├── cli/               ← sandboxshift CLI
└── config.py          ← config loader

tests/                 ← full test suite (80%+ coverage)
terraform/             ← AWS Fargate infrastructure
architecture/
└── decisions/         ← ADR-001 through ADR-009
docs/                  ← getting started, config reference
```

**Total agent work time estimate: 4-8 hours**
**Your actual effort: 8 copy-pastes + PR reviews**

---

## Rules While Building

- Always use **Orchestrator** — never switch to other agents manually
- Never start the next prompt until the current one is fully complete and reviewed
- If an agent creates a GitHub issue asking you something — reply there, not in chat
- If something looks wrong in a PR — say so in the PR review, Orchestrator will fix it
- Trust the process — the agents read AGENTS.md and all ADRs before every task
