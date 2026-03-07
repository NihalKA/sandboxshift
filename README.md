# SandboxShift

<div align="center">

**Run AI agent sandboxes locally. When your machine can't handle it, it automatically bursts to your own AWS. Your data never touches anyone else's servers.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688.svg)](https://fastapi.tiangolo.com)
[![Podman](https://img.shields.io/badge/runtime-Podman-892CA0.svg)](https://podman.io)
[![Chainguard](https://img.shields.io/badge/images-Chainguard-FF6B35.svg)](https://chainguard.dev)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

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

# One-time AWS setup (for cloud burst mode)
sandboxshift init --aws-profile default

# Run an agent task
sandboxshift run \
  --task "refactor the auth module" \
  --workspace ./src/auth \
  --allow pypi.org api.github.com
```

SandboxShift will:
1. Scan your workspace for sensitive data
2. Check available RAM
3. Decide: run locally or burst to your Fargate
4. Execute in a hardened sandbox
5. Return results + full audit log

---

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                      Your Machine                        │
│                                                          │
│   sandboxshift run --task "..."                          │
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

---

## Security Model (7 Layers)

Defence in depth — every layer adds independent protection:

| Layer | What It Does |
|-------|-------------|
| 1. Chainguard base image | Zero-CVE base images, rebuilt nightly |
| 2. Podman rootless | No root daemon, no privilege escalation |
| 3. gVisor syscall interception | Intercepts every system call |
| 4. Network policy | Default deny-all, explicit whitelist only |
| 5. Resource limits | Hard CPU and RAM caps via cgroups |
| 6. Sensitive data detection | Secrets never leave your machine |
| 7. Audit trail | Full append-only log of every agent action |

---

## No Dockerfile Needed

SandboxShift ships pre-built runtime images. It auto-detects your language:

| Found in workspace | Runtime used |
|-------------------|-------------|
| `requirements.txt` | `sandboxshift/runtime-python:3.11` |
| `package.json` | `sandboxshift/runtime-node:20` |
| `pom.xml` / `build.gradle` | `sandboxshift/runtime-java:21` |
| `go.mod` | `sandboxshift/runtime-go:1.22` |
| Multiple found | `sandboxshift/runtime-multi` |

---

## Configuration

Create `sandboxshift.yaml` in your project root:

```yaml
sandbox:
  runtime: auto       # auto, local, or cloud
  timeout: 1800       # seconds before killing sandbox

workspace:
  mount: ./src        # only this directory is visible to agent
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
  level: auto         # auto-detect sensitive data
```

---

## Deployment Modes

| Mode | When To Use |
|------|-------------|
| `--mode local` | Always run on your machine |
| `--mode cloud` | Always run on your Fargate |
| `--mode auto` | Smart switching — default |

---

## Python SDK

```python
from sandboxshift import Sandbox

async with Sandbox.create(config="sandboxshift.yaml") as sb:
    result = await sb.run("refactor the auth module")
    print(result.audit_summary)
```

---

## Roadmap

### V1 — Current
- [x] Project structure and architecture
- [ ] SensitivityScanner (Layer 6)
- [ ] BurstEngine (local/cloud decision)
- [ ] PodmanRuntime (local sandbox)
- [ ] FargateRuntime (cloud burst)
- [ ] SandboxManager (orchestrator)
- [ ] AuditLogger (append-only trail)
- [ ] FastAPI layer (REST API)
- [ ] CLI (`sandboxshift run`)

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

## Contributing

We welcome contributions! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

Key areas where help is wanted:
- Additional language runtimes (Ruby, PHP, .NET)
- Windows and macOS local runtime support
- More sensitive data detection patterns
- Documentation improvements

---

## Security

Found a vulnerability? Please do not open a public issue.
Read [SECURITY.md](SECURITY.md) for responsible disclosure instructions.

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.

---

<div align="center">
Built with by <a href="https://github.com/NihalKA">Nihal</a>
</div>
