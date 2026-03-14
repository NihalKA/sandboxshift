# Configuration Reference

Full reference for `sandboxshift.yaml` and environment variables.

---

## sandboxshift.yaml

Create this file in your project root to set per-project defaults.

```yaml
sandbox:
  runtime: auto       # auto | local | cloud
  timeout: 1800       # seconds before killing sandbox

workspace:
  mount: ./src        # path relative to project root
  readonly: false     # if true, mount is read-only inside container

network:
  allow:
    - pypi.org
    - api.github.com
  block_all_others: true

resources:
  cpu: 2              # CPU cores
  memory: 4GB         # memory limit

sensitivity:
  level: auto         # auto | force_local
```

---

## sandbox

### `sandbox.runtime`

Controls the sandbox execution mode.

| Value | Behaviour |
|-------|----------|
| `auto` | BurstEngine decides: local if RAM sufficient, cloud if tight (Decision #5) |
| `local` | Always run on your machine via Podman |
| `cloud` | Always run on your AWS Fargate |

Mode is decided **once, before the task starts**. There is no mid-execution switching (V1 design decision).

### `sandbox.timeout`

Seconds before the sandbox is killed. Default: `1800` (30 minutes).

---

## workspace

### `workspace.mount`

Path to mount into the sandbox. Relative to the project root. **Only this directory is visible to the agent** — nothing else on your filesystem.

Sensitive paths are rejected at the API boundary and CLI boundary:
- `~/.aws`, `~/.ssh`, `~/.gnupg`
- `/etc`, `/proc`, `/sys`, `/root`

### `workspace.readonly`

If `true`, mount the workspace read-only inside the container. Default: `false`.

---

## network

### `network.allow`

List of FQDNs the sandbox may reach outbound. **FQDNs only — bare IP addresses are rejected** (Decision #39, prevents SSRF against AWS IMDS at `169.254.169.254`).

```yaml
network:
  allow:
    - pypi.org
    - files.pythonhosted.org
    - api.github.com
```

### `network.block_all_others`

Always `true` in V1. The security group and Podman network policy enforce a default-deny outside the allowlist.

---

## resources

### `resources.cpu`

CPU cores allocated to the sandbox. Maps to:
- **Podman**: `--cpus` flag
- **Fargate**: CPU units (1 core = 1024 units)

Valid range (CLI): `0.25` – `64.0`. Default: `2`.

### `resources.memory`

Memory limit. Maps to:
- **Podman**: `--memory` flag
- **Fargate**: memory MiB

Valid range (CLI): `128` MB – `65536` MB (64 GB). Default: `4096` MB.

---

## sensitivity

### `sensitivity.level`

| Value | Behaviour |
|-------|----------|
| `auto` | Run all detection layers — file patterns + content scanning (Decision #6) |
| `force_local` | Skip BurstEngine, always use local regardless of RAM |

`off` is not available in V1 — sensitivity detection cannot be disabled (fail-closed design).

**Scan error behaviour (Decision #9):** If the sensitivity scan itself fails (e.g., a file permission error), SandboxShift forces local execution. A scan error never silently allows cloud execution.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FARGATE_CLUSTER_ARN` | — | Required for cloud burst. ECS cluster ARN from `terraform output cluster_arn`. |
| `FARGATE_TASK_DEFINITION_ARN` | — | Required for cloud burst. ECS task definition ARN from `terraform output task_def_arn`. |
| `FARGATE_SUBNET_IDS` | — | Required for cloud burst. Comma-separated subnet IDs from `terraform output -json subnet_ids`. |
| `FARGATE_SECURITY_GROUP_IDS` | — | Required for cloud burst. Comma-separated SG IDs from `terraform output -json security_group_ids`. |
| `FARGATE_LOG_GROUP` | — | Required for cloud burst. CloudWatch log group from `terraform output log_group`. |
| `FARGATE_REGION` | — | Required for cloud burst. AWS region from `terraform output region`. |
| `SANDBOXSHIFT_AUDIT_LOG` | `~/.sandboxshift/audit.log` | Override the audit log file path. Useful in CI pipelines. |

**If any of the 6 `FARGATE_*` variables are missing**, SandboxShift silently falls back to local-only mode. This is intentional — cloud burst requires explicit opt-in via Terraform provisioning.

**Default RAM threshold:** 4 GB of available system RAM (Decision #16). When available RAM drops below this, BurstEngine chooses cloud (if configured). This threshold is not currently user-configurable via `sandboxshift.yaml` in V1.

---

## Sensitive File Patterns Detected

SensitivityScanner (Layer 2 of the security model) checks for these patterns:

**File name patterns:**
- `.env`, `.env.*`
- `*.pem`, `*.key`, `*.p12`
- `credentials.json`, `*secret*`, `*token*`
- Files inside `~/.aws`, `~/.ssh`

**Content patterns:**
- AWS access keys: `AKIA[0-9A-Z]{16}`
- Private key headers: `-----BEGIN * PRIVATE KEY-----`
- Password assignments: `password=`, `secret=`
- Internal IP ranges: `10.x.x.x`, `192.168.x.x`

When any pattern matches, `sensitivity_reasons` in the response explains exactly what was found and why local execution was forced.
