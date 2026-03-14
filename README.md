# SandboxShift

<div align="center">

**Run AI agent sandboxes locally. When your machine can't handle it, it automatically bursts to your own AWS. Your data never touches anyone else's servers.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688.svg)](https://fastapi.tiangolo.com)
[![Podman](https://img.shields.io/badge/runtime-Podman-892CA0.svg)](https://podman.io)
[![Chainguard](https://img.shields.io/badge/images-Chainguard-FF6B35.svg)](https://chainguard.dev)

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

## Quick Start

```bash
# Install
pip install sandboxshift

# Start the API server
uvicorn sandboxshift.api:app --factory --host 127.0.0.1 --port 8000

# Run a task (REST API)
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "workspace": "/path/to/your/project",
    "task": "pytest tests/",
    "allowed_hosts": ["pypi.org"]
  }'
```

See [Getting Started](docs/getting-started.md) for a full walkthrough including cloud burst setup.

---

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                      Your Machine                        │
│                                                          │
│   POST /run { workspace, task }                          │
│            │                                             │
│            ▼                                             │
│   ┌─────────────────┐                                    │
│   │  Pre-flight      │                                   │
│   │  1. Scan for     │── Sensitive data? ── Force local  │
│   │     secrets      │                                   │
│   │  2. Check RAM    │── RAM ok? ─────────── Run local   │
│   │  3. Decide mode  │── RAM tight? ──── Burst to YOUR   │
│   └─────────────────┘                      Fargate       │
│                                                          │
│   Either way: Hardened sandbox, full audit log           │
│               Your data, your infrastructure             │
└─────────────────────────────────────────────────────────┘
```

Mode is decided **before** the task starts. There is no mid-execution switching (V1 design).

---

## Security Model (7 Layers)

Defence in depth — every layer adds independent protection:

| Layer | What It Does |
|-------|-------------|
| 1. Chainguard base image | Zero-CVE base images, rebuilt nightly |
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
|-------------------|-------------|
| `requirements.txt` | `sandboxshift/runtime-python:3.11` |
| `package.json` | `sandboxshift/runtime-node:20` |
| Multiple found | `sandboxshift/runtime-multi` |

Images are Chainguard-based (zero-CVE, non-root, minimal). See [images/](images/) for Dockerfiles and build instructions.

---

## Configuration

Create `sandboxshift.yaml` in your project root:

```yaml
sandbox:
  runtime: auto       # auto, local, or cloud
  timeout: 1800       # seconds before killing sandbox

workspace:
  mount: ./src        # only this directory is visible to agent

network:
  allow:
    - pypi.org
    - api.github.com
  block_all_others: true

resources:
  cpu: 2
  memory: 4GB

sensitivity:
  level: auto         # auto-detect sensitive data
```

Full reference: [docs/configuration.md](docs/configuration.md)

---

## Cloud Burst Setup

Cloud burst requires a one-time Terraform apply to provision resources in **your** AWS account.

```bash
cd terraform/fargate
terraform init && terraform apply
```

Then set 6 environment variables on the API server:

| Env var | Source |
|---------|--------|
| `FARGATE_CLUSTER_ARN` | `terraform output -raw cluster_arn` |
| `FARGATE_TASK_DEFINITION_ARN` | `terraform output -raw task_def_arn` |
| `FARGATE_SUBNET_IDS` | `terraform output -json subnet_ids \| jq -r 'join(",")'` |
| `FARGATE_SECURITY_GROUP_IDS` | `terraform output -json security_group_ids \| jq -r 'join(",")'` |
| `FARGATE_LOG_GROUP` | `terraform output -raw log_group` |
| `FARGATE_REGION` | `terraform output -raw region` |

If any env var is missing, SandboxShift silently falls back to local-only mode.

Full walkthrough: [docs/getting-started.md#cloud-burst-setup](docs/getting-started.md#cloud-burst-setup)

---

## Deployment Modes

| Mode | When To Use |
|------|-------------|
| `auto` | Smart switching — default; BurstEngine decides based on RAM |
| `local` | Always run on your machine |
| `cloud` | Always run on your Fargate |

---

## API Reference

```bash
# Run a task
POST /run
{
  "workspace": "/path/to/project",
  "task": "pytest tests/",
  "mode": "auto",          # optional
  "allowed_hosts": ["pypi.org"],  # optional
  "timeout_seconds": 1800  # optional
}

# Health check
GET /health

# Audit log (last N entries)
GET /audit?limit=50
```

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
- [x] README and getting started docs

### V2 — Next
- [ ] gVisor integration (Layer 3)
- [ ] Mid-execution migration with checkpoints
- [ ] MCP server (Claude Desktop, Cursor integration)
- [ ] LLM-based sensitivity classifier
- [ ] Grafana observability dashboard

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
