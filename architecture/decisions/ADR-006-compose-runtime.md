# ADR-006: Compose Runtime — Multi-Service Sandboxes

## Status
Accepted (V2 — not yet implemented)

## Date
2026-03-24

---

## Context

### The Problem

V1 `sandboxshift run` executes a single workspace as a single container. Real
applications are rarely a single service. A typical backend project under active
development might require:

- A NestJS API server (its own Git repo)
- A Next.js frontend (its own Git repo)
- A MySQL database
- A MongoDB database

Today, a developer testing the NestJS app on Fargate gets `connect ETIMEDOUT`
because the database isn't available inside the sandbox network. Their workaround is
a remote database in another AWS account — which means the sandbox must reach external
hosts, widening the network attack surface and coupling the sandbox to production
infrastructure.

### The Requirement

Developers need a way to declare all services that make up their application — both
their own code and infrastructure sidecars — and run all of them inside the **same
sandbox network**. Everything talks over `localhost`. No external database needed.

The mechanism should:
1. Work identically on local (Podman) and cloud (Fargate).
2. Not force all repos into a monorepo.
3. Compose per-repo `sandboxshift.yaml` files rather than replacing them.
4. Stay consistent with the existing `sandboxshift run` mental model.

---

## Decision

Introduce a **`sandboxshift-compose.yml`** file and a `sandboxshift compose up` CLI
command that orchestrates multiple services inside a single shared sandbox network.

The compose file is **repository-independent** — it can live at the root of a monorepo,
in a separate infra repo, or anywhere on the developer's machine. It references other
workspaces by relative path.

---

## Compose File Format

```yaml
# sandboxshift-compose.yml

version: 1

services:
  # ── application services ──────────────────────────────────────────────
  backend:
    workspace: ./nestjs-backend      # path to workspace (sandboxshift.yaml loaded from here)
    task: "yarn start:prod"
    port: 3000
    depends_on:
      - mysql
      - mongodb

  frontend:
    workspace: ./nextjs-frontend
    task: "yarn start"
    port: 3001
    depends_on:
      - backend

  # ── infrastructure sidecars ───────────────────────────────────────────
  mysql:
    image: mysql:8.0
    port: 3306
    env:
      MYSQL_ROOT_PASSWORD: test
      MYSQL_DATABASE: myapp
    healthcheck: "mysqladmin ping -h localhost -u root -ptest"

  mongodb:
    image: mongo:7
    port: 27017
    env:
      MONGO_INITDB_DATABASE: myapp
    healthcheck: "mongosh --eval 'db.runCommand({ ping: 1 })'"
```

### Field reference

| Field | Required | Description |
|-------|----------|-------------|
| `workspace` | one of `workspace`/`image` | Path to a local directory containing a `sandboxshift.yaml`. The repo's own config (timeout, setup, network allow, etc.) is inherited. |
| `image` | one of `workspace`/`image` | Docker Hub or ECR image to run as a sidecar. No workspace upload. |
| `task` | if `workspace` | Shell command to run in the workspace container. |
| `port` | no | Single container port to expose on the host. `HOST:CONTAINER` form also accepted. |
| `env` | no | Environment variables injected into the container. |
| `healthcheck` | no | Shell command polled until exit 0 before dependent services start. |
| `depends_on` | no | List of service names that must be healthy before this service starts. |

---

## Network Model

The shared-network design is the core architectural insight:

```
Local (Podman pod):
  podman pod create --name ss-<id> -p 3000:3000 -p 3001:3001 -p 3306:3306 -p 27017:27017
    → mysql container        (wait for healthcheck)
    → mongodb container      (wait for healthcheck)
    → backend container      (yarn start:prod — connects to localhost:3306, localhost:27017)
    → frontend container     (yarn start — talks to localhost:3000)

Cloud (ECS task with multiple container definitions):
  register_task_definition() with 4 containerDefinitions in one task
    → mysql/mongodb: plain image containers, no S3 bootstrap
    → backend/frontend: runtime-node:20 containers with S3 download bootstrap
  All share a single ENI → localhost between all containers
  ECS dependsOn with HEALTHY condition handles ordering natively
```

All containers in a compose session reach each other via **`localhost:<port>`** —
identical on local and cloud. App config (DB URLs etc.) does not change between
environments.

---

## How It Composes Per-Repo sandboxshift.yaml

Each `workspace:` service inherits the settings from that workspace's
`sandboxshift.yaml`. The compose file only specifies what is **cross-service**:

```
service.task            → overrides (or provides) the task command
service.port            → merged with workspace ports:
service.depends_on      → compose-level ordering only
workspace sandboxshift.yaml:
  sandbox.timeout       → inherited
  sandbox.setup         → inherited (runs before service.task)
  workspace.readonly    → inherited
  network.allow         → inherited (local enforcement per container)
  resources.cpu/memory  → inherited
```

The compose file does **not** replace `sandboxshift.yaml` — it sits above it.

---

## CLI Design

```bash
# Start all services (foreground — streams all logs, labelled by service name)
sandboxshift compose up sandboxshift-compose.yml

# Start in the background (server mode)
sandboxshift compose up sandboxshift-compose.yml --detach

# Stop a running compose session
sandboxshift compose down <compose-id>

# Tail logs from a specific service
sandboxshift compose logs <compose-id> backend

# Status of all services in a session
sandboxshift compose ps <compose-id>
```

`sandboxshift compose up` assigns a `compose-id` (e.g. `cs-abc123`) to the session.
All per-service instance IDs are stored under it in `~/.sandboxshift/compose.json`.

---

## What Changes in the Codebase

| Component | Change |
|-----------|--------|
| `src/config.py` | Add `ServiceConfig` dataclass; add `ComposeConfig` dataclass |
| `src/config_loader.py` | Add `load_compose_config()` — parses `sandboxshift-compose.yml` |
| `src/sandbox/runtime/podman.py` | Add pod lifecycle: `create_pod()`, `run_in_pod()`, `destroy_pod()` |
| `src/sandbox/runtime/fargate.py` | Add multi-container task def registration; add ECS `dependsOn` conditions |
| `src/sandbox/compose/` | New module: `ComposeOrchestrator` — drives healthcheck polling, startup ordering, log multiplexing |
| `src/cli/main.py` | Add `sandboxshift compose` subcommand group |
| `AGENTS.md` | Update Decisions Log with compose decisions |

**What does NOT change:** `SandboxManager`, `BurstEngine`, `SensitivityScanner`,
`FargateRuntime.provision/execute/destroy` (single-service path). The compose path is a
separate orchestration layer that calls the existing runtime primitives.

---

## What Is Explicitly Out of Scope

| Feature | Reason | Target |
|---------|--------|--------|
| Git URL workspace sources (`workspace: git@github.com:…`) | Adds clone + auth complexity | V3 |
| Per-service burst decisions (some local, some cloud) | Requires split-network design | V3 |
| Volume mounts between services | Security implications need separate ADR | V3 |
| `sandboxshift compose build` (custom Dockerfiles for sidecars) | Users use their own images | V3 |
| Hot reload / watch mode | V2+ | V3 |

---

## Alternatives Considered

### A — Extend `sandboxshift run` with `--sidecar` flags

Example: `sandboxshift run ./backend "yarn start" --sidecar mysql:8.0 --sidecar mongo:7`

Rejected. Does not support multi-workspace (two independent repos). Becomes unwieldy
with 4+ services. Per-sidecar config (env vars, healthchecks) cannot be expressed
cleanly on the CLI.

### B — Reuse Docker Compose format

Reuse `docker-compose.yml` directly and translate it to Podman/ECS.

Rejected. Docker Compose has no concept of workspace-level `sandboxshift.yaml`
inheritance, no sensitivity scanning, no burst decision. We would need to extend the
Docker Compose format anyway — at which point a separate file with familiar structure
but no compatibility constraints is cleaner.

### C — Kubernetes-style manifest

Rejected for V2. Too much ceremony. V3 K8s mode can accept manifests natively.

---

## Security Considerations

- All compose services run inside the same isolated pod/task — they are not exposed to
  each other's filesystems, only to their shared network.
- `SensitivityScanner` runs against every `workspace:` service before any container
  starts. A single FORCE_LOCAL finding aborts cloud execution for the entire compose
  session (fail-closed).
- Sidecar `image:` services bypass the workspace scanner (no filesystem to scan), but
  are still subject to the same network policy and resource caps.
- Per-service `env:` values are recorded in the audit log.
- `upload_allow_files` applies per workspace service independently.

---

## Consequences

**Positive:**
- Developers can test their full application stack (backend + frontend + databases) in an
  isolated sandbox without touching production infrastructure.
- Local and cloud behaviour is identical — `localhost:3306` works in both.
- Per-repo `sandboxshift.yaml` files are preserved — each repo stays independently
  runnable with `sandboxshift run`.
- ECS multi-container tasks are a natural fit — no new AWS services or Terraform changes
  required.

**Negative:**
- A single compose session has one burst decision — if any one service would force cloud,
  all services must run in cloud (or all local). There is no split.
- Cloud compose tasks consume a large ECS task (sum of all service CPU/memory). Fargate
  minimum task size constraints apply.
- `sandboxshift compose down` must be called explicitly for cloud sessions —
  `sandboxshift stop` only handles single-service runs.
