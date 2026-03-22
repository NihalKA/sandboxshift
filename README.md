# SandboxShift

<div align="center">

**Run AI agent sandboxes locally. When your machine can't handle it, it automatically bursts to your own AWS. Your data never touches anyone else's servers.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688.svg)](https://fastapi.tiangolo.com)
[![Podman](https://img.shields.io/badge/runtime-Podman-892CA0.svg)](https://podman.io)

</div>

---

## The Problem

When AI agents run on your machine, they can:
- Read your SSH keys, AWS credentials, `.env` files
- Make arbitrary network calls to anywhere
- Freeze an 8GB machine running heavy workloads
- Leave no trace of what they actually did

Existing solutions either send your code to **their** cloud (E2B, Modal, Daytona), or only work locally and still crush your machine (Docker sandboxes, DevContainers).

## The Solution

```
Everyone else:  Your code → Their cloud → Their servers
SandboxShift:   Your code → Your local OR Your AWS → You own everything
```

SandboxShift runs every AI agent task in a hardened sandbox. If your machine has enough RAM, it runs locally — free. If not, it automatically bursts to **your own** AWS Fargate. Your data never leaves your control.

---

## Installation

**Prerequisites — you install these:**

| Requirement | For | Install |
|-------------|-----|---------|
| Python 3.11+ | always | [python.org](https://python.org) |
| Podman (rootless) | always | [podman.io](https://podman.io/getting-started/installation) |
| AWS CLI v2 | cloud burst only | [AWS docs](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) |

**Everything else (Terraform, pip install, venv) is handled automatically by the setup script.**

### Podman setup (macOS / Windows)

On **Linux**, rootless Podman works out of the box after install. On **macOS and Windows**, Podman needs a lightweight VM called a "machine" before it can run containers:

```bash
podman machine init      # create the VM (once, ~500 MB download)
podman machine start     # start it (run this after every reboot, or set it to auto-start)
podman info              # verify it's running — look for "host.os: linux" in the output
```

To start the machine automatically on login:
```bash
podman machine set --rootful=false   # keep rootless mode
# macOS: the machine starts automatically after 'podman machine start' once
```

For full details and troubleshooting see the [Podman Machine documentation](https://docs.podman.io/en/latest/markdown/podman-machine.1.html).

### AWS credentials (cloud burst only)

Before running `./sandboxshift-setup.sh cloud`, your AWS CLI must be authenticated. Two common ways:

**Option A — configure a default profile** (interactive, prompts for your access key and secret):
```bash
aws configure
# AWS Access Key ID: AKIA...
# AWS Secret Access Key: ....
# Default region name: us-east-1
# Default output format: json
```

**Option B — use a named profile** (if you manage multiple AWS accounts):
```bash
export AWS_PROFILE=my-sandboxshift-profile
```

You can also use IAM Identity Center (SSO), environment variables (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`), or an EC2/EC2-equivalent instance role. SandboxShift calls `boto3.Session()` with no hardcoded credentials — it picks up whatever the AWS SDK finds. For full credential setup options see the [AWS CLI configuration docs](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-configure.html).

---

```bash
git clone https://github.com/NihalKA/sandboxshift
cd sandboxshift
chmod +x sandboxshift-setup.sh

# Recommended — auto-detects: cloud if AWS credentials present, local otherwise
./sandboxshift-setup.sh

# Or explicitly:
./sandboxshift-setup.sh local   # local Podman only, no AWS needed
./sandboxshift-setup.sh cloud   # local + full cloud burst setup
```

The setup script does all of this automatically — you run nothing else:
1. Downloads pinned **Terraform 1.5.7** into `~/.sandboxshift/bin/` — never touches your system Terraform
2. Creates an **isolated Python venv** at `~/.sandboxshift/venv/` — your global Python env stays clean
3. Runs **`pip install -e .`** inside that venv — installs sandboxshift without touching your system Python
4. Symlinks the CLI to `~/.sandboxshift/bin/sandboxshift`
5. Builds all runtime images into Podman (`runtime-python`, `runtime-node`, `runtime-multi`)
6. *(cloud only)* Creates ECR repo, pushes image, runs `terraform apply`, writes `~/.sandboxshift/fargate.env`

**One-time PATH setup** (the script will print this reminder if needed):
```bash
echo 'export PATH="$HOME/.sandboxshift/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```
After this, `sandboxshift` works in every terminal. You never need to activate a venv or set env vars manually.

---

## Quick Start

```bash
# Run a Python task (local sandbox via Podman)
sandboxshift run /path/to/your/project "pytest tests/"

# Run a Node.js server (local)
sandboxshift run /path/to/node-app "node index.js" --port 3000

# Force cloud burst to Fargate
sandboxshift run /path/to/project "python main.py" --mode cloud

# Run a cloud server and tail logs live
sandboxshift run /path/to/node-app "node index.js" --port 3000 --mode cloud
# Ctrl+C to stop tailing (server keeps running)
sandboxshift stop <instance_id>
```

See [Getting Started](docs/getting-started.md) for a full walkthrough.

---

## How It Works

```
┌────────────────────────────────────────────────────────┐
│                      Your Machine                        │
│                                                          │
│   sandboxshift run /workspace "task"                     │
│            │                                             │
│            ▼                                             │
│   ┌─────────────────┐                                    │
│   │  Pre-flight      │                                   │
│   │  1. Scan for     │── Sensitive data? ── Force local  │
│   │     secrets      │                                   │
│   │  2. Check RAM    │── RAM ok? ──────────── Run local  │
│   │  3. Decide mode  │── RAM tight? ──── Burst to YOUR   │
│   └─────────────────┘                      Fargate       │
│                                                          │
│   Either way: Hardened sandbox, full audit log           │
│               Your data, your infrastructure             │
└──────────────────────────────────────────────────────────┘
```

Mode is decided **before** the task starts. There is no mid-execution switching (V1 design).

---

## Security Model (7 Layers)

Defence in depth — every layer adds independent protection:

| Layer | What It Does |
|-------|-------------|
| 1. Hardened base image | Official slim images, non-root user (UID 10000), minimal packages |
| 2. Podman rootless | No root daemon, no privilege escalation |
| 3. gVisor syscall interception | Intercepts every system call (V2) |
| 4. Network policy | Default deny-all, explicit FQDN whitelist only |
| 5. Resource limits | Hard CPU and RAM caps via cgroups |
| 6. Sensitive data detection | Secrets never leave your machine |
| 7. Audit trail | Full append-only log of every agent action |

---

## No Dockerfile Needed

SandboxShift auto-detects your language from workspace markers:

| Found in workspace | Runtime used |
|-------------------|--------------|
| `requirements.txt` | `sandboxshift/runtime-python:3.11` |
| `package.json` | `sandboxshift/runtime-node:20` |
| Multiple found | `sandboxshift/runtime-multi` |

Images are built locally into Podman by `sandboxshift-setup.sh`. For cloud burst, `runtime-multi` is also pushed to your ECR. See [images/](images/) for Dockerfiles.

---

## Configuration

Place `sandboxshift.yaml` in your **workspace root** (the same directory you pass to `sandboxshift run`). It is loaded automatically — no flag needed.

```
sandboxshift run /path/to/my-project "node index.js"
                 └── looks for /path/to/my-project/sandboxshift.yaml
```

Full example with every supported key:

```yaml
# sandboxshift.yaml — place in your workspace root

sandbox:
  timeout: 1800              # kill sandbox after this many seconds (default: 1800)
  setup: "npm ci"            # run this before your main task (e.g. install deps)
  skip_sensitivity_check: false  # set true to bypass secret scanning (use with caution)
  mode: auto                 # local | cloud | auto (default: auto)
                             # local  = always run in Podman, ignore RAM
                             # cloud  = always burst to Fargate, ignore RAM
                             # auto   = decide by available RAM (uses --ram-threshold)
                             # CLI --mode overrides this. Never overrides sensitive-data detection.

workspace:
  readonly: false            # true = workspace mounted read-only inside container

network:
  # LOCAL ONLY — enforced via --dns=none + --add-host in Podman.
  # In cloud (Fargate), outbound access is controlled by the AWS Security Group
  # provisioned by Terraform at setup time. This list is audited but not enforced.
  allow:
    - pypi.org               # FQDNs the local container can reach outbound
    - npmjs.com
    - api.github.com
  # Use ["*"] to allow ALL outbound traffic (local) — disables Security Layer 4
  # allow:
  #   - "*"

resources:
  # -- Container limits: applied to BOTH local and cloud --
  # Local:  caps Podman container CPU/RAM via cgroups
  # Cloud:  passed as ECS task-level overrides on every run_task call
  #         Fargate requires valid CPU/memory combinations — see table below.
  cpu: 2                     # CPU cores (local: Podman cgroup; cloud: ECS task override — 2 = 2048 CPU units)
  memory: 4GB                # RAM cap — also accepts "4096MB" or 4096 (MB int)

  # -- Host requirements (burst triggers) --
  # These describe what your local machine must have available.
  # If the requirement is NOT met, SandboxShift forces cloud — regardless of --ram-threshold.
  min_cpu: 4                 # host must have ≥ 4 CPUs, otherwise burst to cloud
  min_memory: 8GB            # host must have ≥ 8GB available RAM, otherwise burst to cloud

ports:
  - 3000:3000                # HOST:CONTAINER — expose container port 3000 on host port 3000
  - 8080:80                  # HOST:CONTAINER — expose container port 80 on host port 8080
```

**Key facts:**
- **YAML is merged with CLI flags** — CLI always wins on conflicts (except `ports`, which are combined)
- `resources.cpu` / `resources.memory` are applied to **both local and cloud** — locally via Podman cgroup caps; in cloud via ECS task-level overrides on every `run_task` call (no Terraform re-apply needed)
- `resources.min_cpu` / `resources.min_memory` are **host requirements** — if your local machine falls short, cloud is forced regardless of `--ram-threshold`
- `network.allow` is **enforced locally** via Podman (`--dns=none` + per-domain `--add-host`). In cloud, it is **recorded in the audit log only** — actual outbound access is controlled by the AWS Security Group provisioned by Terraform (which allows all egress by default in V1)
- `network.allow` with `["*"]` disables the local outbound allowlist — use only for trusted workspaces

### Fargate valid CPU / memory combinations

When running in cloud mode, `resources.cpu` and `resources.memory` must be a valid Fargate combination. Invalid combos are rejected immediately by ECS.

| CPU (`resources.cpu`) | Min memory | Max memory |
|-----------------------|-----------|------------|
| 0.25 vCPU | 512 MB | 2 GB |
| 0.5 vCPU | 1 GB | 4 GB |
| 1 vCPU | 2 GB | 8 GB |
| 2 vCPU | 4 GB | 16 GB |
| 4 vCPU | 8 GB | 30 GB |
| 8 vCPU | 16 GB | 60 GB |
| 16 vCPU | 32 GB | 120 GB |

For the full list and memory increment rules see the [AWS Fargate task size documentation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html).

Full reference: [docs/configuration.md](docs/configuration.md)

---

## Cloud Burst Setup

```bash
./sandboxshift-setup.sh cloud
```

The script manages everything:
1. Downloads Terraform 1.5.7 to `~/.sandboxshift/bin/` (pinned, isolated)
2. Builds `runtime-multi` and pushes it to your ECR
3. Runs `terraform apply` — provisions ECS cluster, S3 bucket, IAM roles, security groups
4. Writes `~/.sandboxshift/fargate.env` with all 8 connection variables
5. The CLI auto-loads `fargate.env` on every run — no `export` needed ever

**Only prerequisite for cloud:** AWS CLI authenticated (see [AWS credentials](#aws-credentials-cloud-burst-only) above).

Full walkthrough: [docs/getting-started.md](docs/getting-started.md)

---

## CLI Reference

```bash
sandboxshift run <workspace> <task> [options]
```

`<workspace>` is the directory to mount. `sandboxshift.yaml` is loaded from that directory automatically if present.

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--mode MODE` | `auto` | Run mode: `local` (always Podman, ignore RAM), `cloud` (always Fargate, ignore RAM), `auto` (decide by available RAM). YAML `sandbox.mode` applies when CLI is `auto`. **Never overrides sensitive-data detection** — if secrets are found, local is always forced. |
| `--port PORT` | — | Expose a port. Accepts bare `3000` (maps 3000→3000) or `HOST:CONTAINER` e.g. `8080:3000`. Repeat for multiple ports. Combined with YAML `ports:`. |
| `--allow FQDN` | — | **Local only.** Allow outbound to this domain in Podman. Repeat for multiple. Use `"*"` for unrestricted. Overrides YAML `network.allow` entirely when set. Has no effect on cloud runs (Fargate uses AWS Security Groups). |
| `--setup CMD` | — | Shell command run before the task (e.g. `"npm ci"`). Overrides YAML `sandbox.setup`. Works in both local and cloud. |
| `--timeout N` | `1800` | Kill sandbox after N seconds. Overrides YAML `sandbox.timeout`. |
| `--memory-mb N` | `512` | Container RAM cap in MB (128–65536). Locally: Podman cgroup cap. Cloud: ECS task-level override per run. Overrides YAML `resources.memory`. Must be a valid Fargate combination with `--cpu` when running in cloud. |
| `--cpu N` | `1.0` | Container CPU cores (0.25–64.0). Locally: Podman cgroup cap. Cloud: ECS task-level override per run. Overrides YAML `resources.cpu`. Must be a valid Fargate combination with `--memory-mb` when running in cloud. |
| `--ram-threshold N` | `1024` | **Auto-mode burst trigger (MB).** If available host RAM < N MB, burst to cloud. Only used when `--mode auto` (and YAML `sandbox.mode: auto`). Use `--mode cloud` / `--mode local` for a cleaner toggle. |
| `--skip-sensitivity-check` | `false` | Skip secret scanning. Combined with YAML `sandbox.skip_sensitivity_check`. |
| `--audit-log PATH` | `~/.sandboxshift/audit.log` | Override audit log file path. Also set via `SANDBOXSHIFT_AUDIT_LOG` env var. |

### Controlling local vs cloud

| Intent | Command |
|--------|--------|
| Auto (default) — local if RAM ≥ 1 GB, else cloud | `sandboxshift run /workspace "task"` |
| Always run locally | `sandboxshift run /workspace "task" --mode local` |
| Always burst to cloud | `sandboxshift run /workspace "task" --mode cloud` |
| Fine-grained RAM threshold | `sandboxshift run /workspace "task" --ram-threshold 8192` |
| Pin workspace to always run locally (YAML) | Set `sandbox.mode: local` in `sandboxshift.yaml` |
| Pin workspace to always burst (YAML) | Set `sandbox.mode: cloud` in `sandboxshift.yaml` |
| Hard cloud requirement via YAML | Set `resources.min_memory: 8GB` in `sandboxshift.yaml` |

> **Note:** `--ram-threshold` compares against **available** RAM right now (not total installed RAM). On a 16 GB machine with many apps open, available might be 4–6 GB.
>
> **Security:** `--mode cloud` and `sandbox.mode: cloud` never override sensitive-data detection. If SandboxShift finds secrets in the workspace, it always forces local regardless of the mode flag.

### Other commands

```bash
# List running cloud server tasks
sandboxshift list

# Stop a running cloud server task
sandboxshift stop <instance_id>

# View the last 20 audit log entries
sandboxshift audit tail

# View the last N entries
sandboxshift audit tail -n 50

# Use a custom audit log path
sandboxshift audit tail --audit-log /tmp/my-audit.log
```

### YAML vs CLI precedence

| Setting | YAML key | CLI flag | Priority |
|---------|----------|----------|----------|
| Run mode | `sandbox.mode` | `--mode` | CLI wins. `--mode auto` (default) defers to YAML; when YAML also absent, uses `--ram-threshold`. Neither can override sensitive-data detection (security Layer 6). |
| Timeout | `sandbox.timeout` | `--timeout` | CLI wins |
| Setup command | `sandbox.setup` | `--setup` | CLI wins |
| Skip scan | `sandbox.skip_sensitivity_check` | `--skip-sensitivity-check` | CLI wins (either true = skip) |
| Network allow | `network.allow` | `--allow` | CLI replaces YAML entirely. **Local enforcement only.** |
| CPU limit | `resources.cpu` | `--cpu` | CLI wins. Applied to both local (Podman cgroup) and cloud (ECS task override per run). |
| Memory limit | `resources.memory` | `--memory-mb` | CLI wins. Applied to both local (Podman cgroup) and cloud (ECS task override per run). |
| Ports | `ports` | `--port` | **Combined** (YAML + CLI, deduped) |
| Readonly mount | `workspace.readonly` | no CLI flag | YAML only |
| Min CPU (burst trigger) | `resources.min_cpu` | no CLI flag | YAML only |
| Min memory (burst trigger) | `resources.min_memory` | no CLI flag | YAML only |
| RAM threshold (fine-grained, auto-mode only) | no YAML key | `--ram-threshold` | CLI only. Only used when both `--mode` and `sandbox.mode` are `auto`. |
| Audit log path | no YAML key | `--audit-log` | CLI / env var only |

---

## Roadmap

### V1 — Current
- [x] Project structure and architecture
- [x] SensitivityScanner (Layer 6)
- [x] BurstEngine (local/cloud decision)
- [x] PodmanRuntime (local sandbox)
- [x] FargateRuntime (cloud burst)
- [x] SandboxManager (orchestrator)
- [x] AuditLogger (append-only trail)
- [x] FastAPI layer (REST API)
- [x] Python CLI (`sandboxshift run`)
- [x] Pre-built runtime images (python, node, multi)
- [x] Terraform AWS setup
- [x] One-script setup (`sandboxshift-setup.sh`)

### V2 — Next
- [ ] gVisor integration (Layer 3)
- [ ] Chainguard base images (zero-CVE, SBOM)
- [ ] Mid-execution migration with checkpoints
- [ ] MCP server (Claude Desktop, Cursor integration)
- [ ] LLM-based sensitivity classifier
- [ ] Grafana observability dashboard
- [ ] Homebrew tap (`brew install nihalka/tap/sandboxshift`)

### V3 — Planned
- [ ] Kubernetes mode (Helm chart)
- [ ] Firecracker microVMs
- [ ] FIPS compliance
- [ ] Air-gapped deployment
- [ ] SOC2 / ISO27001 audit export

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.

---

<div align="center">
Built with ♥ by <a href="https://github.com/NihalKA">Nihal</a>
</div>
