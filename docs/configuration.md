# Configuration Reference

Full reference for `sandboxshift.yaml`, CLI precedence, and cloud environment variables.

For install and CLI examples, see [installation.md](installation.md) and [usage.md](usage.md).

---

## sandboxshift.yaml

Create `sandboxshift.yaml` in your project root to set per-project defaults.

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
  upload_allow:              # filenames to upload to S3 even though they match sensitive patterns
    - .env                   # exact filename match only — not a glob
    - .env.staging           # each overridden file is recorded in the audit log
                             # CLI --allow-file overrides this list entirely when set

env:                         # environment variables injected into the container
  COMPONENTS_UI_NPM_TOKEN: "ghp_xxx"  # solves yarn .npmrc ${TOKEN} expansion errors
  NODE_ENV: production       # works in both local (Podman) and cloud (Fargate)
                             # values are NEVER written to the audit log
                             # CLI --env KEY=VAL overrides this entire section when set

network:
  # LOCAL ONLY — enforced via --dns=none + --add-host in Podman.
  # In cloud (Fargate), outbound access is controlled by the AWS Security Group
  # provisioned by Terraform at setup time. This list is audited but not enforced.
  allow:
    - pypi.org
    - npmjs.com
    - api.github.com
  # Use ["*"] to allow ALL outbound traffic (local) — disables Security Layer 4
  # allow:
  #   - "*"

resources:
  # -- Container limits: applied to BOTH local and cloud --
  cpu: 2
  memory: 4GB

  # -- Host requirements (burst triggers) --
  min_cpu: 4
  min_memory: 8GB

ports:
  - 3000:3000
  - 8080:80
```

---

## Key facts

- YAML is merged with CLI flags — CLI wins on conflicts, except `ports`, which are combined
- `env:` values are injected into the sandbox and are never written to the audit log
- `resources.cpu` and `resources.memory` apply to both local and cloud runs
- `resources.min_cpu` and `resources.min_memory` are host requirements that can force cloud
- `network.allow` is enforced locally; in cloud it is recorded but not enforced by SandboxShift itself
- `workspace.upload_allow` explicitly allowlists sensitive filenames for cloud upload

---

## YAML vs CLI precedence

| Setting | YAML key | CLI flag | Priority |
|---------|----------|----------|----------|
| Run mode | `sandbox.mode` | `--mode` | CLI wins. `--mode auto` defers to YAML. Neither can override sensitive-data detection. |
| Timeout | `sandbox.timeout` | `--timeout` | CLI wins |
| Setup command | `sandbox.setup` | `--setup` | CLI wins |
| Skip scan | `sandbox.skip_sensitivity_check` | `--skip-sensitivity-check` | CLI wins |
| Env vars | `env:` | `--env` | CLI replaces YAML entirely when set |
| Network allow | `network.allow` | `--allow` | CLI replaces YAML entirely |
| CPU limit | `resources.cpu` | `--cpu` | CLI wins |
| Memory limit | `resources.memory` | `--memory-mb` | CLI wins |
| Ports | `ports` | `--port` | Combined (YAML + CLI, deduped) |
| Readonly mount | `workspace.readonly` | no CLI flag | YAML only |
| Upload allow-list | `workspace.upload_allow` | `--allow-file` | CLI replaces YAML entirely when set |
| Min CPU | `resources.min_cpu` | no CLI flag | YAML only |
| Min memory | `resources.min_memory` | no CLI flag | YAML only |
| RAM threshold | no YAML key | `--ram-threshold` | CLI only |
| Audit log path | no YAML key | `--audit-log` | CLI / env var only |

---

## Fargate valid CPU / memory combinations

When running in cloud mode, `resources.cpu` and `resources.memory` must be a valid Fargate combination.

| CPU (`resources.cpu`) | Min memory | Max memory |
|-----------------------|-----------|------------|
| 0.25 vCPU | 512 MB | 2 GB |
| 0.5 vCPU | 1 GB | 4 GB |
| 1 vCPU | 2 GB | 8 GB |
| 2 vCPU | 4 GB | 16 GB |
| 4 vCPU | 8 GB | 30 GB |
| 8 vCPU | 16 GB | 60 GB |
| 16 vCPU | 32 GB | 120 GB |

For the full list and increment rules, see the [AWS Fargate task size documentation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html).

---

## Cloud environment variables

These are read by the CLI from `~/.sandboxshift/fargate.env` after cloud setup.

### Required for cloud runs

| Variable | Description |
|----------|-------------|
| `FARGATE_CLUSTER_ARN` | ECS cluster ARN |
| `FARGATE_EXECUTION_ROLE_ARN` | ECS task execution role ARN |
| `FARGATE_TASK_ROLE_ARN` | ECS task role ARN |
| `FARGATE_SUBNET_IDS` | Comma-separated subnet IDs |
| `FARGATE_SECURITY_GROUP_IDS` | Comma-separated security group IDs |
| `FARGATE_LOG_GROUP` | CloudWatch log group |
| `FARGATE_REGION` | AWS region |
| `FARGATE_WORKSPACE_BUCKET` | S3 bucket used for workspace staging |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `FARGATE_SERVER_SECURITY_GROUP_ID` | — | Extra security group used for server tasks |
| `FARGATE_TASK_FAMILY` | `sandboxshift-sandbox` | ECS task family prefix |
| `FARGATE_ECR_IMAGE` | runtime image from setup | Container image used for cloud runs |
| `SANDBOXSHIFT_AUDIT_LOG` | `~/.sandboxshift/audit.log` | Override audit log path |

If the required `FARGATE_*` values are missing, SandboxShift falls back to local-only mode.

---

## Protected local paths

Workspaces inside these locations are rejected:

- `~/.aws`
- `~/.ssh`
- `~/.gnupg`
- `/etc`
- `/proc`
- `/sys`
- `/root`

---

## Sensitive file patterns detected

Sensitivity scanning checks for these common patterns before allowing cloud execution.

### File name patterns

- `.env`, `.env.*`
- `*.pem`, `*.key`, `*.p12`
- `credentials.json`, `*secret*`, `*token*`
- files inside `~/.aws` and `~/.ssh`

### Content patterns

- AWS access keys like `AKIA...`
- private key headers
- password and secret assignments
- internal IP ranges such as `10.x.x.x` and `192.168.x.x`

If any pattern matches, SandboxShift forces local execution.
