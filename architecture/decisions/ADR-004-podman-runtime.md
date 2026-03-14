# ADR-004: PodmanRuntime Design

## Status
Accepted

## Date
2026-03-14

---

## Context

`PodmanRuntime` is the V1 implementation of the abstract `Runtime` interface defined
in ADR-001. It is Steps 3–6 of the `SandboxManager` pipeline — the adaptor that
actually provisions, executes in, and destroys a local container sandbox.

### Position in the Execution Pipeline

```
SandboxManager
  │
  ├─ 1. SensitivityScanner.scan(workspace)        → SensitivityResult     (ADR-002)
  │
  ├─ 2. BurstEngine.decide(...)                   → BurstDecision("local") (ADR-003)
  │
  ├─ 3. PodmanRuntime.provision(workspace, config) → instance_id ◄── This ADR
  │
  ├─ 4. PodmanRuntime.execute(instance_id, task, config) → TaskResult ◄── This ADR
  │
  ├─ 5. AuditLogger.record(all_actions)
  │
  └─ 6. PodmanRuntime.destroy(instance_id)                          ◄── This ADR
```

`PodmanRuntime` is only instantiated when `BurstDecision.mode == "local"`. The cloud
path (`FargateRuntime`) is a separate adaptor built in a later prompt.

### Why Podman

Decision #1 (closed): Podman was chosen for rootless, daemonless sandboxing.
Rootless Podman runs without a privileged system daemon. Every container is owned by a
normal user process, and no Linux capability is granted beyond what an unprivileged
user already has. This directly eliminates the most dangerous attack surface of
Docker's docker.sock model.

### Why subprocess, Not podman-py

`podman-py` is a Python binding for Podman's REST API. It requires the Podman socket
to be running, has a complex mocking surface in tests, and adds a non-stdlib dependency
that obscures what system calls are being made. Using `subprocess.run` against the
Podman CLI:

- Is trivially mockable in tests via `unittest.mock.patch("subprocess.run", …)`
- Documents the exact CLI invocation (auditable, reviewable, reproducible)
- Requires zero additional PyPI dependencies (`subprocess` is stdlib)
- Matches how operators debug Podman issues — they run the same CLI

### Why Chainguard Base Images

Decision #2 (closed): Chainguard images have zero CVEs, minimal dependencies, and
ship a Software Bill of Materials (SBOM). Agent code runs in a distroless environment
with no package manager, no shell tooling, and no unnecessary binaries — the smallest
possible attack surface inside the container.

### Image Auto-Detection

Requiring users to write Dockerfiles defeats the goal of a zero-config developer
experience. Auto-detection from workspace markers (files that already exist in real
projects) selects the right runtime image with no user input.

### Network Architecture

Rootless Podman containers cannot modify the host network namespace. The userspace
networking stack `slirp4netns` provides outbound connectivity without elevated
privileges. Blocking DNS by default and injecting only pre-resolved host entries for
allowed domains ensures that the container can reach only the domains the policy
permits — even if the container's code tries to resolve other names.

---

## Decision

### Core Approach

`PodmanRuntime` implements the three-method `Runtime` ABC using the Podman CLI via
`subprocess.run`, wrapped in `asyncio.to_thread` to stay non-blocking. Each sandbox
follows a strict lifecycle:

```
provision()  →  generate instance_id, detect image, resolve DNS, store state
execute()    →  build and run `podman run` command, capture output, return TaskResult
destroy()    →  run `podman rm -f {instance_id}`, idempotent on already-removed container
```

### instance_id Format

`"ss-{uuid4().hex[:12]}"` — 16-character string that is:
- Unique per sandbox instance
- Valid as a Podman container name (alphanumeric and hyphens only)
- Recognisable in `podman ps` output as a SandboxShift container

### Image Auto-Detection Algorithm

```
INPUTS: workspace: Path

FOR each marker in [requirements.txt, package.json, go.mod]
    check if (workspace / marker).exists()

found_count = number of markers present

if found_count > 1:
    → "sandboxshift/runtime-multi"

elif found_count == 1:
    requirements.txt → "cgr.dev/chainguard/python:latest"
    package.json     → "cgr.dev/chainguard/node:latest"
    go.mod           → "cgr.dev/chainguard/go:latest"

else:
    → "cgr.dev/chainguard/python:latest"   # default
```

This logic lives in a module-level `_detect_image(workspace: Path) -> str` function
so it is independently unit-testable.

### Podman run Command Structure

```
podman run
  --name            {instance_id}
  --rm                                  # auto-remove after exit (belt-and-suspenders)
  --user            65532:65532         # Chainguard nonroot UID/GID (never root)
  --cpus            {config.cpu_limit}
  --memory          {config.memory_limit_mb}m
  --network         {network_flag}      # "none" or "slirp4netns" — see below
  [--dns=none]                          # only when slirp4netns
  [--add-host=domain:ip ...]            # one per resolved allowed domain
  --volume          {workspace}:/workspace{":ro" if readonly}
  --workdir         /workspace
  --security-opt    no-new-privileges   # block privilege escalation inside container
  {image}
  /bin/sh -c {task}
```

`--privileged` is **never** included. This is enforced by construction — the flag does
not appear anywhere in the codebase.

### Network Policy Algorithm

```
INPUTS: config.network_allow: list[str]

if network_allow is empty:
    flags = ["--network=none"]
    # Complete network isolation — no DNS, no outbound

else:
    flags = ["--network=slirp4netns", "--dns=none"]
    for each domain in network_allow:
        ip = socket.getaddrinfo(domain, None)[0][4][0]
        if resolution succeeds:
            flags += [f"--add-host={domain}:{ip}"]
        else:
            emit audit warning, skip domain
```

DNS resolution runs inside `asyncio.to_thread` during `provision()` and the results
are stored in `_InstanceState.resolved_hosts: dict[str, str]` for use during
`execute()`.

### _InstanceState Internal Data Structure

A private `dataclass` (not exported) that stores all state between `provision()` and
`execute()`:

```python
@dataclass
class _InstanceState:
    image:          str
    workspace:      Path
    config:         SandboxConfig
    resolved_hosts: dict[str, str]   # domain → resolved IPv4 string
```

`PodmanRuntime` keeps `self._instances: dict[str, _InstanceState]` indexed by
`instance_id`.

### TaskResult

```python
@dataclass
class TaskResult:
    exit_code:        int
    stdout:           str
    stderr:           str
    duration_seconds: float
```

Duration is measured with `time.perf_counter()` around the `asyncio.to_thread`
call — includes only container execution time, not provision time.

### AuditLogger Usage

`PodmanRuntime.__init__` accepts an optional `AuditLogger` and defaults to
`AuditLogger()` (the stub). Each method calls `self._audit.record(event: dict)`:

| Method | event dict keys |
|--------|----------------|
| `provision()` | `event`, `instance_id`, `image`, `workspace` |
| `provision()` — DNS failure | `event`, `instance_id`, `domain` |
| `execute()` | `event`, `instance_id`, `task`, `exit_code`, `duration_seconds` |
| `destroy()` | `event`, `instance_id` |

The `event` key holds a string: `"provision"`, `"dns_resolution_failed"`,
`"execute"`, or `"destroy"`.

### Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `workspace` does not exist when `provision()` is called | Raise `FileNotFoundError` immediately, before any subprocess call |
| `execute()` called with unknown `instance_id` | Raise `RuntimeError("unknown instance_id: {instance_id}")` |
| `destroy()` called with unknown `instance_id` | No-op — log audit event and return |
| `podman rm -f` exits non-zero in `destroy()` | Swallow — container may already be gone (`--rm` flag) |
| `podman run` exits non-zero in `execute()` | Return `TaskResult` with that `exit_code` — do NOT raise |
| Domain DNS resolution fails in `provision()` | Skip that domain, emit audit warning event, continue |
| `subprocess.run` raises `TimeoutExpired` in `execute()` | Let it propagate — SandboxManager calls `destroy()` in its `finally` block |

---

## Options Considered

### Option Set A — Runtime Library Choice

| Option | Description | Verdict |
|--------|-------------|---------|
| A1 — subprocess (chosen) | `subprocess.run` to Podman CLI | ✅ Chosen — trivially mockable, zero extra deps, documents exact CLI invocation |
| A2 — podman-py | Python bindings for Podman REST API | ❌ Requires Podman socket; complex to mock; adds non-stdlib dependency |
| A3 — asyncio.create_subprocess_exec | Native async subprocess | ❌ More complex stream handling; subprocess.run+to_thread is simpler and equally correct for V1 |
| A4 — Docker SDK + Podman compat socket | Docker SDK against Podman's Docker-compatible socket | ❌ Adds Docker SDK dependency; requires Podman socket; two indirections to debug |

### Option Set B — Networking Approach

| Option | Description | Verdict |
|--------|-------------|---------|
| B1 — slirp4netns + DNS block + add-host (chosen) | Userspace NAT, DNS disabled, pre-resolved IPs injected | ✅ Chosen — rootless-compatible, surgical domain allowlist, no host changes |
| B2 — --network=none for all tasks | Complete isolation always | ❌ Prevents legitimate network use (pypi.org, api.github.com) |
| B3 — nftables / iptables rules | Kernel-level packet filtering | ❌ Requires root; incompatible with rootless Podman |
| B4 — Podman network create with DNS plugin | Custom CNI network per sandbox | ❌ Requires rootful Podman or privileged CNI plugins; too complex for V1 |
| B5 — --network=host | Share host network namespace | ❌ Security non-starter: container can reach any host port |

### Option Set C — Image Auto-Detection Approach

| Option | Description | Verdict |
|--------|-------------|---------|
| C1 — Workspace marker files (chosen) | Check presence of requirements.txt, package.json, go.mod | ✅ Chosen — zero config, works with existing real-world projects |
| C2 — User-specified image in config | User writes image name in sandboxshift.yaml | ❌ Defeats zero-config goal; requires users to know Chainguard image names |
| C3 — Shebang scanning | Read first line of .py/.js/.go files | ❌ Fragile, slow, many edge cases |
| C4 — File extension sampling | Count .py/.js/.go files | ❌ Every project has mixed extensions; ambiguous on polyglot repos |

### Option Set D — Container Lifecycle Model

| Option | Description | Verdict |
|--------|-------------|---------|
| D1 — `podman run` per task (chosen) | Container created, runs task, exits, is removed | ✅ Chosen — simplest lifecycle, no idle container to leak |
| D2 — `podman create` + `start` + `exec` | Long-lived container, multiple execs | ❌ Adds complexity; idle containers consume RAM |
| D3 — `podman play kube` (Kubernetes YAML) | K8s-style pod definition | ❌ Overkill for V1 |

### Option Set E — Duration Measurement

| Option | Description | Verdict |
|--------|-------------|---------|
| E1 — time.perf_counter() (chosen) | Monotonic high-resolution timer around subprocess call | ✅ Chosen — monotonic, nanosecond precision, stdlib |
| E2 — datetime.now() | Wall clock | ❌ Not monotonic; affected by NTP adjustments |
| E3 — subprocess result resource stats | psutil or resource module | ❌ Measures CPU time not wall duration; inconsistent cross-platform |

---

## Security Architecture

Each non-negotiable security requirement and how `PodmanRuntime` meets it:

| Requirement | How PodmanRuntime Enforces It |
|-------------|-------------------------------|
| 1. NEVER `--privileged` | Flag is never constructed in the command builder — not absent conditionally, never present at all. |
| 2. NEVER root inside container | `--user 65532:65532` is hardcoded (Chainguard `nonroot` UID/GID). Not configurable at runtime. |
| 3. ALWAYS CPU and RAM limits | `--cpus {config.cpu_limit}` and `--memory {config.memory_limit_mb}m` are unconditional. `SandboxConfig` has non-zero defaults. |
| 4. ALWAYS network policy | `--network=none` (empty allowlist) or `--network=slirp4netns` + `--dns=none` + `--add-host`. No path produces `--network=host`. |
| 5. Only workspace mounted | `--volume {workspace}:/workspace` is the sole `--volume` flag. No other host paths. |
| 6. No sensitive host paths | `provision()` validates that `workspace` exists before proceeding. Only the specified path is mounted. |
| 7. Audit trail | Every `provision`, `execute`, and `destroy` event is passed to `AuditLogger.record()`. |

Additionally:
- `--security-opt=no-new-privileges` prevents any `setuid` binary inside the container
  from escalating privileges.
- `--rm` ensures stopped containers are auto-removed, preventing resource leaks even if
  `destroy()` is never called.

---

## V1 Scope Exclusions

| Feature | Deferred To | Reason |
|---------|-------------|--------|
| gVisor syscall interception (`--runtime=runsc`) | V2 | Requires gVisor installed; Security Layer 3 is V2 |
| Checkpoint / resume mid-execution | V2 | Decision #5: upfront-only switching in V1 |
| Container re-use across tasks | V2 | `podman run` per task is simplest |
| GPU resource limits (`--gpus`) | V2 | No GPU runtimes in V1 |
| Java / Rust auto-detection | V2 | Those Chainguard images not in V1 set |
| Real `AuditLogger` with persistence | V1 Prompt 6 | Stub used until then |
| `FargateRuntime` | V1 Prompt 4 | Separate component; shares this ABC |
| `--read-only` root filesystem | V2 | Chainguard images may need `/tmp` write access |
| Firecracker microVM | V3 | V3 scope |

---

## Consequences

### Easier

- **Testing**: `subprocess.run` patched in one line — zero real containers in any test.
- **Debugging**: Operators can copy the exact `podman run` command from audit logs and
  run it manually.
- **Security audits**: The full invocation is a single list of strings — easy to review
  for dangerous flags.
- **Extension**: `FargateRuntime` and `KubernetesRuntime` implement the same ABC without
  touching `PodmanRuntime`.
- **Zero-config UX**: Auto-image detection requires no user configuration.
- **Dependency graph**: No new PyPI packages. Entire implementation uses stdlib only.

### Harder

- **Podman availability**: Requires `podman` binary on `PATH`. Runtime fails at
  `provision()` time if absent.
- **`sandboxshift/runtime-multi` not yet published**: Multi-runtime workspaces will fail
  at `execute()` until the image is pushed to a registry.
- **`slirp4netns` availability**: Some minimal environments lack it. `provision()` should
  detect this and fail fast with a clear error.
- **`--add-host` IP staleness**: DNS resolved once at provision time; stale for
  long-running tasks if upstream IP changes.
- **`/bin/sh` assumption**: Task commands are wrapped in `/bin/sh -c`. Chainguard's
  distroless `go:latest` may not include a shell.

---

## Relationship to Other Components

```
SandboxManager
  │
  ├─► SensitivityScanner (ADR-002)    → SensitivityResult
  ├─► BurstEngine (ADR-003)           → BurstDecision(mode="local")
  │
  ├─► PodmanRuntime.provision(workspace, config)
  │       ├─ _detect_image(workspace)        → Chainguard image tag
  │       ├─ _resolve_host(domain) × N       → resolved_hosts dict
  │       └─ returns instance_id
  │
  ├─► PodmanRuntime.execute(instance_id, task, config)
  │       ├─ _build_network_flags(state)     → list[str]
  │       ├─ asyncio.to_thread(subprocess.run, cmd, ...)
  │       └─ returns TaskResult
  │
  └─► PodmanRuntime.destroy(instance_id)
          └─ asyncio.to_thread(subprocess.run, ["podman", "rm", "-f", id], ...)
```

---

## Implementation File Table

| File | Role |
|------|------|
| `src/config.py` | `SandboxConfig` dataclass — shared by all runtimes |
| `src/observability/__init__.py` | Package marker — empty |
| `src/observability/audit.py` | `AuditLogger` stub — `record(event: dict) -> None: pass` |
| `src/sandbox/runtime/__init__.py` | Package exports: `Runtime`, `TaskResult`, `PodmanRuntime` |
| `src/sandbox/runtime/base.py` | `Runtime` ABC + `TaskResult` dataclass |
| `src/sandbox/runtime/podman.py` | `PodmanRuntime` full implementation |
| `tests/sandbox/runtime/__init__.py` | Empty test package marker |
| `tests/sandbox/runtime/test_podman.py` | Full test suite (37 tests) |
| `pyproject.toml` | No new deps — confirm subprocess is stdlib |

---

## References

- ADR-001: Overall System Architecture
- ADR-002: SensitivityScanner Design
- ADR-003: BurstEngine Design
- Decisions Log #1 (Podman), #2 (Chainguard), #4 (Python/FastAPI), #9 (FORCE_LOCAL), #11 (pyproject.toml)
