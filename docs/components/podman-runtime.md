# PodmanRuntime — Developer Reference

> **Runtime Step 3 of 6** — Local Sandbox Runtime  
> Runs immediately after `BurstEngine` returns `mode="local"`. Never invoked when `BurstEngine` returns `mode="cloud"`.

---

## Overview

`PodmanRuntime` is the V1 local execution backend for SandboxShift. It implements the `Runtime` ABC defined in `src/sandbox/runtime/base.py` and is responsible for the complete lifecycle of a local sandbox: image selection, container provisioning, task execution, and teardown. It runs agent tasks inside rootless Podman containers using zero-CVE Chainguard base images, with unconditional CPU, RAM, and network limits enforced at the Podman CLI layer. Every lifecycle event — provision, execute, destroy, and DNS failure — is written to the `AuditLogger` (Security Layer 7). `PodmanRuntime` is only ever invoked in local mode; cloud execution is handled by `FargateRuntime` (V2).

All Podman CLI calls are dispatched through `asyncio.to_thread(subprocess.run, ...)` so the async event loop is never blocked during container operations. `subprocess.run` is the sole integration point with the host system — tests mock it exclusively and no real containers are created during the test suite.

### Position in the Execution Pipeline

```
SandboxManager
  │
  ├─► 1. SensitivityScanner.scan(workspace)           → SensitivityResult
  │
  ├─► 2. BurstEngine.decide(sensitivity_result, ...)   → BurstDecision
  │
  ├─► 3. PodmanRuntime.provision(workspace, config)    → instance_id
  │           ↑ This component (local path only)
  ├─► 4. PodmanRuntime.execute(instance_id, task, ...)  → TaskResult
  ├─► 5. AuditLogger.record(all_actions)
  └─► 6. PodmanRuntime.destroy(instance_id)
```

---

## Image Auto-Detection

`provision()` calls `_detect_image(workspace)` before starting any container. The function walks the workspace root for well-known marker files and selects the most appropriate Chainguard image. This happens once per `provision()` call and the result is stored in `_InstanceState` for the lifetime of that sandbox.

| Workspace Marker | Selected Image |
|------------------|----------------|
| `requirements.txt` | `cgr.dev/chainguard/python:latest` |
| `package.json` | `cgr.dev/chainguard/node:latest` |
| `go.mod` | `cgr.dev/chainguard/go:latest` |
| Multiple markers | `sandboxshift/runtime-multi` |
| None found | `cgr.dev/chainguard/python:latest` (default) |

**Detection rules:**

- Markers are checked as direct children of the workspace root — no recursive search.
- If exactly one marker is found, the corresponding single-language image is used.
- If two or more markers are found simultaneously, `sandboxshift/runtime-multi` is selected. This image is a **V2 deliverable** and is not yet built. Running a multi-marker workspace in V1 will fail at container start.
- If no marker is found, the default Python image is used. This is a safe fallback; Python is the most common agent runtime.
- Image constants are hardcoded in `_MARKER_IMAGES` and `_DEFAULT_IMAGE`. There is no user override mechanism — this is intentional (Security Layer 1).

---

## Security Model

`PodmanRuntime` directly enforces four of the seven SandboxShift security layers. The remaining three are handled by upstream components or deferred to V2.

### Layer 1 — Chainguard Base Images (Enforced)

All images are hardcoded as module-level constants:

```python
_DEFAULT_IMAGE = "cgr.dev/chainguard/python:latest"
_MARKER_IMAGES = {
    "requirements.txt": "cgr.dev/chainguard/python:latest",
    "package.json":     "cgr.dev/chainguard/node:latest",
    "go.mod":           "cgr.dev/chainguard/go:latest",
}
```

Callers cannot pass a custom image to `provision()`. `SandboxConfig` has no `image` field. The only way to change the selected image is to modify the source code — an intentional friction point that prevents supply-chain substitution.

### Layer 2 — Rootless Execution (Enforced)

The container user is hardcoded as the Chainguard nonroot UID:

```python
_NONROOT_USER = "65532:65532"  # Chainguard nonroot UID:GID — never root
```

`--user 65532:65532` is always present in the `podman run` command. It is not configurable. In addition, `--security-opt no-new-privileges` is always appended, preventing `setuid` binary escalation even if present in the image.

`--privileged` is **never** passed. The flag does not appear anywhere in this module — this is enforced by construction, not by a runtime check.

### Layer 3 — gVisor Syscall Interception (Deferred — V2)

gVisor integration is intentionally absent from V1. When V2 adds it, `--runtime=runsc` will be injected into the `podman run` command at this layer. No action is required from callers — `PodmanRuntime` will be updated internally.

### Layer 4 — Network Policy (Enforced)

Network behaviour is determined entirely by `SandboxConfig.network_allow`:

| `network_allow` | Podman flags | Effect |
|-----------------|--------------|--------|
| `[]` (empty) | `--network=none` | Complete network isolation. No outbound or inbound traffic. |
| `["pypi.org", ...]` | `--network=slirp4netns --dns=none --add-host=pypi.org:{ip} ...` | Outbound allowed only to pre-resolved IPs. DNS is blocked; only injected hosts are reachable. |

When `network_allow` is non-empty, each domain is resolved via `socket.getaddrinfo()` **before** the container starts, inside `provision()`. The resolved IPv4 addresses are stored in `_InstanceState.resolved_hosts` and injected as `--add-host` entries. If a domain fails to resolve, it is silently dropped and an `dns_resolution_failed` audit event is written — the container still starts with the remaining hosts.

`--dns=none` ensures the container cannot perform its own DNS lookups even via slirp4netns. Outbound traffic is constrained to the explicit `--add-host` allow-list only.

> **Host requirement:** `slirp4netns` must be installed separately on the host OS for non-`--network=none` mode. On most Linux distributions this is a package install (`apt install slirp4netns`). macOS users running Podman Desktop have it bundled.

### Layer 5 — Resource Limits (Enforced)

`--cpus` and `--memory` are always present:

```
--cpus {config.cpu_limit}
--memory {config.memory_limit_mb}m
```

Defaults from `SandboxConfig`: `cpu_limit=2.0`, `memory_limit_mb=4096`. These flags are unconditional — there is no code path that omits them.

Task timeout is enforced by `subprocess.run(timeout=state.config.timeout_seconds)`. If the task exceeds the timeout, `subprocess.TimeoutExpired` propagates to the caller. Callers (i.e., `SandboxManager`) must call `destroy()` in their `finally` block — `execute()` does not call `destroy()` on timeout.

### Layer 6 — Sensitive Data Detection (Delegated)

`PodmanRuntime` has no knowledge of whether the workspace contains sensitive data. Sensitivity detection is the responsibility of `SensitivityScanner` (Step 1) and `BurstEngine` (Step 2), which run before `PodmanRuntime` is ever invoked. By the time `provision()` is called, the pipeline has already guaranteed that cloud execution is not occurring for a sensitive workspace. `PodmanRuntime` trusts this guarantee.

> **Warning:** Direct instantiation of `PodmanRuntime` bypasses `SensitivityScanner` entirely. Only invoke `PodmanRuntime` directly in tests or tooling where you control the workspace. Production use must go through `SandboxManager`.

### Layer 7 — Audit Trail (Enforced)

Every state transition is recorded via `AuditLogger.record()`:

| Event | When | Fields recorded |
|-------|------|-----------------|
| `provision` | After state is stored | `instance_id`, `image`, `workspace` |
| `dns_resolution_failed` | Per unresolvable domain in `network_allow` | `instance_id`, `domain` |
| `execute` | After task completes | `instance_id`, `task`, `exit_code`, `duration_seconds` |
| `destroy` | After `podman rm` attempt | `instance_id` |

`destroy()` always records an audit event, even if the container was already removed by `--rm` or if the `podman rm` call failed.

---

## API Reference

### `SandboxConfig`

Defined in `src/config.py`. Configuration for a sandbox run. All fields have defaults — construct with `SandboxConfig()` for a zero-network, 2-CPU, 4 GB Python sandbox.

```python
@dataclass
class SandboxConfig:
    cpu_limit:          float     = 2.0
    memory_limit_mb:    int       = 4096
    network_allow:      list[str] = field(default_factory=list)
    timeout_seconds:    int       = 1800
    workspace_readonly: bool      = False
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cpu_limit` | `float` | `2.0` | Number of CPUs allocated via `--cpus`. Fractional values are valid (e.g. `0.5`). |
| `memory_limit_mb` | `int` | `4096` | RAM cap in megabytes, passed as `--memory {n}m`. |
| `network_allow` | `list[str]` | `[]` | FQDNs the container may reach outbound. Empty list = `--network=none`. |
| `timeout_seconds` | `int` | `1800` | Maximum task wall-clock time (30 min). Raises `subprocess.TimeoutExpired` on breach. |
| `workspace_readonly` | `bool` | `False` | If `True`, workspace is mounted `:ro` inside the container. |

---

### `TaskResult`

Defined in `src/sandbox/runtime/base.py`. The return type of `execute()`.

```python
@dataclass
class TaskResult:
    exit_code:        int
    stdout:           str
    stderr:           str
    duration_seconds: float
```

| Field | Type | Description |
|-------|------|-------------|
| `exit_code` | `int` | Container exit code. `0` = success. Non-zero is returned, not raised. |
| `stdout` | `str` | Full captured standard output. |
| `stderr` | `str` | Full captured standard error. |
| `duration_seconds` | `float` | Wall-clock seconds measured around the `subprocess.run` call. |

---

### `Runtime` (ABC)

Defined in `src/sandbox/runtime/base.py`. All SandboxShift runtime adaptors implement this interface.

```python
class Runtime(abc.ABC):
    @abc.abstractmethod
    async def provision(self, workspace: Path, config: SandboxConfig) -> str: ...

    @abc.abstractmethod
    async def execute(self, instance_id: str, task: str, config: SandboxConfig) -> TaskResult: ...

    @abc.abstractmethod
    async def destroy(self, instance_id: str) -> None: ...
```

| Method | Returns | Description |
|--------|---------|-------------|
| `provision(workspace, config)` | `str` | Prepare the sandbox. Returns an opaque `instance_id`. |
| `execute(instance_id, task, config)` | `TaskResult` | Run a shell command in the sandbox. |
| `destroy(instance_id)` | `None` | Tear down the sandbox. Must be called even if `execute()` raised. |

---

### `PodmanRuntime`

```python
class PodmanRuntime(Runtime):
    def __init__(self, audit_logger: AuditLogger | None = None) -> None: ...

    async def provision(self, workspace: Path, config: SandboxConfig) -> str: ...

    async def execute(
        self,
        instance_id: str,
        task: str,
        config: SandboxConfig,
    ) -> TaskResult: ...

    async def destroy(self, instance_id: str) -> None: ...
```

#### `__init__(audit_logger=None)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `audit_logger` | `AuditLogger \| None` | `None` | Audit logger instance. Defaults to `AuditLogger()` (V1 stub). Pass a custom instance in tests or when integrating with a structured logging backend. |

Instantiation is cheap. A single `PodmanRuntime` instance can be reused across multiple concurrent sandbox sessions — each session gets its own `_InstanceState` keyed by `instance_id`.

#### `provision(workspace, config) -> str`

Prepares a sandbox for the given workspace. Does **not** start a container — container start is deferred to `execute()`.

**Steps (in order):**
1. Validates `workspace.exists()`. Raises `FileNotFoundError` if not.
2. Auto-detects the Chainguard image from workspace markers.
3. Generates a unique `instance_id` (`"ss-{12 hex chars}"`).
4. Resolves DNS for each domain in `config.network_allow` (via `socket.getaddrinfo`, run in a thread).
5. Stores `_InstanceState` keyed by `instance_id`.
6. Records a `provision` audit event.
7. Returns `instance_id`.

| Raises | Condition |
|--------|----------|
| `FileNotFoundError` | `workspace` path does not exist. |

#### `execute(instance_id, task, config) -> TaskResult`

Runs a shell command inside the provisioned sandbox using `podman run`. The `config` parameter is accepted for ABC compatibility but unused in V1 — all execution parameters are read from the stored `_InstanceState`.

| Raises | Condition |
|--------|----------|
| `RuntimeError` | `instance_id` was not returned by a prior `provision()` call. |
| `subprocess.TimeoutExpired` | Task exceeded `config.timeout_seconds`. Caller must call `destroy()`. |

A non-zero `exit_code` is **not** raised — it is returned inside `TaskResult`.

#### `destroy(instance_id) -> None`

Runs `podman rm -f {instance_id}` and removes the instance from internal state. **Idempotent and never raises.** All errors from the subprocess call are silently suppressed — the container may already be gone due to the `--rm` flag on `podman run`, or `podman` may not be on `PATH` in CI environments. Unknown `instance_id` values are a safe no-op.

---

## Podman Command Reference

The exact command built by `execute()` for a given `_InstanceState`:

```
podman run
  --name {instance_id}
  --rm
  --user 65532:65532
  --cpus {config.cpu_limit}
  --memory {config.memory_limit_mb}m
  --volume {workspace}:/workspace[:ro]
  --workdir /workspace
  --security-opt no-new-privileges
  [--network=none]
  [--network=slirp4netns --dns=none --add-host={domain}:{ip} ...]
  {image}
  /bin/sh -c "{task}"
```

**Notes:**

- `--rm` removes the container automatically on exit. `destroy()` calls `podman rm -f` as a belt-and-suspenders cleanup for force-killed containers.
- `[:ro]` is appended to the volume mount only when `config.workspace_readonly=True`.
- Exactly one of `--network=none` or the `--network=slirp4netns` group is present — never both.
- `--add-host` entries are emitted for every domain in `resolved_hosts` (domains that resolved successfully during `provision()`). Domains that failed to resolve are absent.
- The task string is passed verbatim as the argument to `/bin/sh -c`. Shell injection is the caller's responsibility — `SandboxManager` sanitises task strings before passing them to `execute()`.

---

## Usage Example

Minimal Python example showing the full `provision → execute → destroy` lifecycle:

```python
import asyncio
from pathlib import Path
from sandboxshift.config import SandboxConfig
from sandboxshift.sandbox.runtime.podman import PodmanRuntime

async def run_task():
    runtime = PodmanRuntime()
    config = SandboxConfig(network_allow=["pypi.org"])

    instance_id = await runtime.provision(Path("./my-project"), config)
    try:
        result = await runtime.execute(instance_id, "python main.py", config)
        print(f"Exit: {result.exit_code}")
        print(result.stdout)
    finally:
        await runtime.destroy(instance_id)

asyncio.run(run_task())
```

Example outputs for different workspace configurations:

```
# Workspace contains requirements.txt
Exit: 0
Hello from a Chainguard Python sandbox

# Workspace contains package.json
Exit: 0
Node v20.x running in cgr.dev/chainguard/node:latest

# Task fails (non-zero exit) — no exception raised
Exit: 1
(stderr captured in result.stderr)
```

**Running multiple sandboxes concurrently:**

```python
async def run_parallel():
    runtime = PodmanRuntime()  # one instance, many sessions
    config = SandboxConfig()

    ids = await asyncio.gather(
        runtime.provision(Path("./project-a"), config),
        runtime.provision(Path("./project-b"), config),
    )
    results = await asyncio.gather(
        runtime.execute(ids[0], "python a.py", config),
        runtime.execute(ids[1], "python b.py", config),
    )
    await asyncio.gather(runtime.destroy(ids[0]), runtime.destroy(ids[1]))
```

---

## V1 Limitations

The following are explicitly deferred. Do not implement them unless explicitly requested.

- **gVisor not yet integrated** — Security Layer 3 (`--runtime=runsc`) is a V2 feature. Syscall interception is absent in V1.
- **`sandboxshift/runtime-multi` image not yet built** — Workspaces with multiple language markers will fail at container start in V1. V2 deliverable.
- **`/bin/sh` assumed present** — The task is always wrapped in `/bin/sh -c`. Chainguard `go:latest` is a distroless image that may not include a shell. A shell-included variant (`cgr.dev/chainguard/go:latest-dev`) may be required for Go workspaces in practice.
- **`slirp4netns` must be installed separately** — Required on the host for any `network_allow` non-empty config. Not bundled with SandboxShift. Absent `slirp4netns` causes `podman run` to fail at container start.
- **Direct instantiation bypasses SensitivityScanner** — Constructing `PodmanRuntime()` directly and calling `provision()` skips Steps 1 and 2 of the pipeline. `SandboxManager` (V1 Prompt 5) is the only safe caller in production.
- **No checkpoint/resume** — If `execute()` times out or the host machine crashes, work is lost. Mid-execution migration and checkpoint/resume are V2 features (Decision #5).

---

## Running The Tests

```bash
pytest tests/sandbox/runtime/ -v
```

The runtime test suite contains **40 tests** covering:

- Image auto-detection for each marker file and the multiple-marker case
- `provision()` success and `FileNotFoundError` on missing workspace
- DNS resolution success, failure (skipped domain + audit event), and empty `network_allow` path
- `execute()` success with zero and non-zero exit codes
- `execute()` with readonly workspace (`:ro` suffix)
- `execute()` timeout propagation (`subprocess.TimeoutExpired`)
- `execute()` with unknown `instance_id` (`RuntimeError`)
- `destroy()` idempotency: unknown ID, already-removed container, subprocess exception
- Network flag construction: `--network=none` vs slirp4netns + `--add-host`
- Full `podman run` command structure validation
- Audit event presence and field correctness for all four event types

To run with coverage:

```bash
pytest tests/sandbox/runtime/ -v --cov=src/sandbox/runtime --cov-report=term-missing
```

---

## Related Files

| File | Purpose |
|------|---------|
| [src/sandbox/runtime/podman.py](../../src/sandbox/runtime/podman.py) | Full implementation |
| [src/sandbox/runtime/base.py](../../src/sandbox/runtime/base.py) | `Runtime` ABC and `TaskResult` dataclass |
| [src/config.py](../../src/config.py) | `SandboxConfig` dataclass |
| [src/observability/audit.py](../../src/observability/audit.py) | `AuditLogger` used for Security Layer 7 |
| [tests/sandbox/runtime/](../../tests/sandbox/runtime/) | Test suite (40 tests) |
| [docs/components/burst-engine.md](burst-engine.md) | `BurstEngine` reference — component that runs before `PodmanRuntime` |
| [docs/components/sensitivity-scanner.md](sensitivity-scanner.md) | `SensitivityScanner` reference — component that runs before `BurstEngine` |
