# SandboxShift

## ⚡ 5-second demo

```bash
sandboxshift run . "npm install"
```

Runs in an isolated sandbox — not on your system.
If your machine is low on resources, it automatically runs in your AWS.

**Run untrusted code safely — without breaking your system.**

---

## Installation

```bash
git clone https://github.com/NihalKA/sandboxshift
cd sandboxshift
chmod +x sandboxshift-setup.sh
./sandboxshift-setup.sh
```


Full setup:

- [docs/installation.md](docs/installation.md) — prerequisites, Podman setup, AWS credentials, PATH setup, and Terraform/cloud setup
- [docs/getting-started.md](docs/getting-started.md) — first local run and first cloud run

---

## Quick Start

```bash
# Run locally in a sandbox
sandboxshift run /path/to/project "python main.py"

# Force a cloud run in your AWS account
sandboxshift run /path/to/project "python main.py" --mode cloud

# Run a server
sandboxshift run /path/to/node-app "node index.js" --port 3000 --mode cloud
```

More examples:

- [docs/usage.md](docs/usage.md) — quick start, CLI flags, env vars, cloud/local control, audit commands
- [docs/getting-started.md](docs/getting-started.md) — guided first run walkthrough

---

## Problem

Running untrusted or AI-generated code directly on your machine can:

- read files you did not mean to expose, like `.env`, SSH keys, or cloud credentials
- make network calls to places you did not intend
- install or change things on your machine
- use enough CPU or RAM to slow down or freeze your laptop
- leave you unsure what actually ran and where it ran

## Why SandboxShift?

- runs **locally first** when your machine has enough resources
- uses **your AWS account** automatically when local resources are not enough
- runs in **your environment**, not on our servers
- keeps execution **isolated from your machine**
- gives you a **fresh disposable environment** for each run

---

## How It Works

```
┌──────────────────────────────────────────────────────────────┐
│                         Your Machine                         │
│                                                              │
│  sandboxshift run /workspace "task"                          │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────┐                                         │
│  │   Pre-flight    │── Sensitive data? ──► Force local       │
│  │  1. Scan        │                                         │
│  │  2. Check RAM   │── Enough RAM? ──────► Run local         │
│  │  3. Decide mode │                                         │
│  └─────────────────┘── Low RAM? ─────────► Burst to AWS      │
│                                                              │
│  Either way: isolated sandbox + full audit log               │
│              your data, your infrastructure                  │
└──────────────────────────────────────────────────────────────┘
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
| 6. Sensitive data detection | Secrets never leave your machine by default |
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

Configuration lives in `sandboxshift.yaml` in your workspace root.

For the full reference, see:

- [docs/configuration.md](docs/configuration.md) — full YAML example, CLI precedence, Fargate CPU/memory rules, and cloud env vars
- [docs/usage.md](docs/usage.md) — CLI flags and advanced examples

---

## Documentation

- [docs/installation.md](docs/installation.md) — installation, Podman, AWS credentials, Terraform/cloud setup
- [docs/getting-started.md](docs/getting-started.md) — first local run, first cloud run, audit log walkthrough
- [docs/usage.md](docs/usage.md) — quick start, CLI flags, env vars, allow-file, audit commands
- [docs/configuration.md](docs/configuration.md) — `sandboxshift.yaml`, CLI precedence, cloud env vars

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
- [ ] **Compose runtime** — `sandboxshift-compose.yml` + `sandboxshift compose up` command. Run multiple repos and sidecar services (MySQL, MongoDB, Redis, Postgres) inside one shared sandbox network. All containers reach each other via `localhost`. Works identically on local (Podman pod) and cloud (ECS multi-container task). Each repo keeps its own `sandboxshift.yaml`; the compose file sits above and wires them together. See [ADR-006](architecture/decisions/ADR-006-compose-runtime.md).
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
