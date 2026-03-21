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

## Installation

**Prerequisites:** Python 3.11+, [Podman](https://podman.io/getting-started/installation) (rootless)

```bash
git clone https://github.com/NihalKA/sandboxshift
cd sandboxshift

# Local mode only (no AWS needed)
./sandboxshift-setup.sh local

# Local + cloud burst (requires AWS CLI + Terraform)
./sandboxshift-setup.sh cloud
```

The setup script handles everything: installs the Python package, builds runtime images into Podman, and (for cloud) creates ECR repos, pushes images, runs Terraform, and writes `~/.sandboxshift/fargate.env` (auto-loaded by the CLI — no `export` needed).

---

## Quick Start

```bash
# Run a Python task (local sandbox via Podman)
sandboxshift run /path/to/your/project "pytest tests/"

# Run a Node.js server (local)
sandboxshift run /path/to/node-app "node index.js" --port 3000

# Force cloud burst (Fargate)
sandboxshift run /path/to/project "python main.py" --ram-threshold 999999

# Run a cloud server and tail logs live
sandboxshift run /path/to/node-app "node index.js" --port 3000 --ram-threshold 999999
# Ctrl+C to stop tailing (server keeps running)
sandboxshift stop <instance_id>
```

See [Getting Started](docs/getting-started.md) for a full walkthrough.

---

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                      Your Machine                        │
│                                                          │
│   sandboxshift run /workspace "task"                     │
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

Images are built locally into Podman by `sandboxshift-setup.sh`. For cloud burst, `runtime-multi` is also pushed to your ECR. See [images/](images/) for Dockerfiles.

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

Run the one-line setup:

```bash
./sandboxshift-setup.sh cloud
```

That's it. The script:
1. Builds runtime images and pushes `runtime-multi` to your ECR
2. Runs `terraform apply` to provision the ECS cluster, S3 bucket, IAM roles, and security groups in your AWS account
3. Writes `~/.sandboxshift/fargate.env` with all 8 connection variables
4. The CLI auto-loads `fargate.env` on every run — no `export` commands needed

**Prerequisites for cloud:** AWS CLI configured (`aws configure`), Terraform, jq.

Full walkthrough: [docs/getting-started.md](docs/getting-started.md)

---

## Deployment Modes

| Mode | When To Use |
|------|-------------|
| `auto` | Smart switching — default; BurstEngine decides based on RAM |
| `local` | Always run on your machine |
| `cloud` | Always run on your Fargate |

---

## CLI Reference

```bash
# Run a task
sandboxshift run <workspace> <task> [options]
  --port PORT|HOST:CONTAINER   Expose a port (repeat for multiple)
  --allow FQDN                 Allow outbound to this domain (repeat for multiple)
  --timeout N                  Kill after N seconds (default: 1800)
  --memory-mb N                Memory limit in MB (default: 512)
  --cpu N                      CPU limit (default: 1.0)
  --setup CMD                  Run this command before the task
  --skip-sensitivity-check     Skip sensitive data scan
  --ram-threshold N            Burst to cloud if local RAM below N MB

# Stop a running cloud server
sandboxshift stop <instance_id>

# View audit log
sandboxshift audit tail [--lines N]
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
- [x] One-script setup (`sandboxshift-setup.sh`)

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
