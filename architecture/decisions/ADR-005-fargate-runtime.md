# ADR-005: FargateRuntime Design

## Status
Accepted

## Date
2026-03-14

---

## Context

`FargateRuntime` is the V1 cloud implementation of the abstract `Runtime` interface
defined in ADR-001. It is invoked by `SandboxManager` when `BurstDecision.mode == "cloud"` —
meaning the local machine has insufficient RAM and the task must be executed in the
user's own AWS account via ECS Fargate.

### Position in the Execution Pipeline

```
SandboxManager
  │
  ├─ 1. SensitivityScanner.scan(workspace)    → SensitivityResult       (ADR-002)
  │      ↓ If FORCE_LOCAL → PodmanRuntime     (ADR-004)
  │
  ├─ 2. BurstEngine.decide(...)              → BurstDecision("cloud")   (ADR-003)
  │
  ├─ 3. FargateRuntime.provision(workspace, config) → instance_id   ◄── This ADR
  │
  ├─ 4. FargateRuntime.execute(instance_id, task, config) → TaskResult ◄── This ADR
  │
  ├─ 5. AuditLogger.record(all_actions)
  │
  └─ 6. FargateRuntime.destroy(instance_id)                          ◄── This ADR
```

`FargateRuntime` is only instantiated when `BurstDecision.mode == "cloud"`. The local
path (`PodmanRuntime`) runs when RAM is sufficient or when `SensitivityScanner` forces
local execution.

### The Ownership Constraint

SandboxShift's core promise: _the user always owns all infrastructure_. This means:

- The ECS cluster and task definition are **pre-provisioned** by Terraform in the
  user's own AWS account. `FargateRuntime` never creates AWS accounts, IAM users, or
  long-lived resources.
- The S3 bucket created per task is **ephemeral** — created at `provision()` and
  permanently deleted at `destroy()`.
- `FargateRuntime` never touches a shared SandboxShift cloud. All API calls go to the
  user's own account using their own credentials.

### Why Fargate

Decision #3 (closed): AWS Fargate was chosen for cloud burst.

Fargate provides fully managed, serverless container execution. The user pays only for
the seconds their task runs — there are no EC2 instances to manage or keep warm. Fargate
runs inside the user's own VPC, so network policy enforcement is delegated to the VPC's
security groups (provisioned by Terraform), which is the correct architectural boundary
for network controls in a cloud context.

### Why S3 for Workspace Transfer

When a task bursts to Fargate, the workspace directory on the local machine must be
transferred to the container. S3 was chosen because it is durable, cheap, and natively
accessible from within an ECS Fargate task without any sidecar or host tooling.

### Why CloudWatch Logs for Output Capture

ECS Fargate tasks do not have an interactive stdout/stderr pipe back to the caller.
Container output is collected by the ECS platform and shipped to CloudWatch Logs via the
`awslogs` log driver configured in the task definition. `FargateRuntime.execute()`
retrieves log events after the task stops, reconstructing stdout and stderr strings.

---

## Decision

### Core Approach

`FargateRuntime` implements the three-method `Runtime` ABC using `boto3` to call AWS
APIs via `asyncio.to_thread`. Each sandbox follows a strict lifecycle:

```
provision()  →  generate instance_id, create ephemeral S3 bucket, upload workspace, store state
execute()    →  run ECS Fargate task, poll until STOPPED, fetch CloudWatch logs, return TaskResult
destroy()    →  stop ECS task (idempotent), delete all S3 objects+bucket, clear state
```

### instance_id Format

`"ss-{uuid4().hex[:12]}"` — identical format to `PodmanRuntime`. This is intentional:
`SandboxManager` is runtime-agnostic and must not distinguish between local and cloud
instance IDs.

### Credential Model

`boto3.Session()` is constructed with **no explicit credentials**. The standard boto3
credential chain resolves credentials in this order:

1. IAM task role (when running inside Fargate)
2. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` environment variables (user-supplied)
3. `AWS_PROFILE` → `~/.aws/credentials` (developer workstation)
4. EC2 instance profile / container credentials

`FargateRuntime.__init__` explicitly does **not** accept `aws_access_key_id` or
`aws_secret_access_key` parameters. If no credentials are configured, boto3 raises
`botocore.exceptions.NoCredentialsError`; this propagates to the caller as a
configuration error. (Decision #22)

### S3 Bucket Design

One temporary bucket per task, named `sandboxshift-{instance_id}`:

| Property | Value | Reason |
|----------|-------|--------|
| Region | Same as `FargateRuntime.region` | Low latency to ECS task; data sovereignty |
| Public access | All four BlockPublicAccess flags = `True` | Security Layer 1 |
| Encryption | AES256 SSE | Security; no KMS in V1 for simplicity |
| Versioning | OFF | Ephemeral content; versioning adds cost without benefit |
| Max size | 500 MB total workspace | Prevents runaway upload costs |

Files are uploaded using `s3.put_object`. Files matching sensitive patterns (`.env`,
`.pem`, `*.key`) are skipped at upload time as a belt-and-suspenders check.

### ECS Fargate Task Design

`execute()` calls `ecs.run_task` with container overrides that inject:

| Override | Value |
|----------|-------|
| `command` | `["/bin/sh", "-c", task]` |
| `environment` (added) | `SS_BUCKET={bucket_name}`, `SS_PREFIX=workspace/`, `SS_TASK_ID={instance_id}` |

The task definition image is **not** overridden in V1. Image auto-detection is run for
the audit record only. (Decision #27)

Polling uses `ecs.describe_tasks` in a loop with 5-second sleep until `STOPPED` status
or `config.timeout_seconds` is exceeded. `TimeoutError` is raised on timeout.
(Decision #28)

### Log Retrieval Design

CloudWatch log stream: `sandboxshift/{instance_id}`. `get_log_events` called once after
task stops. On any error: `TaskResult` with empty `stdout`/`stderr`, audit warning
recorded, no raise. (Decision #24)

In V1, ECS `awslogs` driver combines stdout/stderr into one stream. `stderr` is returned
as `""`. V2 may use `awsfirelens` for split streams.

### Exit Code Extraction

`describe_tasks` → `containers[0].exitCode`. If absent: sentinel `-1` + audit warning.
(Decision #26)

### Network Policy

Fargate network enforcement happens at the VPC/security-group level (Terraform).
`FargateRuntime` passes `subnet_ids` and `security_group_ids` into `ecs.run_task`.
`config.network_allow` is recorded in audit only — not dynamically enforced in V1.

---

## Options Considered

### Option A: Credential Approach

| Option | Description | Decision |
|--------|-------------|----------|
| **No explicit creds (chosen)** | `boto3.Session()` with no args — standard credential chain | **Chosen** |
| Constructor params | Accept `aws_access_key_id`, `aws_secret_access_key` in `__init__` | Rejected — hardcoded creds violate Security Layer 1 |
| Env var reader | `FargateRuntime` reads `os.environ["AWS_ACCESS_KEY_ID"]` itself | Rejected — redundant with boto3's built-in chain |

### Option B: Workspace Transfer Approach

| Option | Description | Decision |
|--------|-------------|----------|
| **S3 (chosen)** | Upload to ephemeral S3 bucket; task downloads on start | **Chosen** — durable, cheap, natively accessible from Fargate |
| EFS | Mount an EFS volume shared between caller and Fargate task | Rejected — requires persistent infrastructure |
| Sidecar container | Run a file-server sidecar alongside the agent container | Rejected — complex; requires NAT or VPC peering |
| Git bundle | Pack workspace as a git bundle, store in S3 | Rejected — requires git on caller and inside container |

### Option C: Log Retrieval Approach

| Option | Description | Decision |
|--------|-------------|----------|
| **CloudWatch Logs (chosen)** | `awslogs` driver in task def; `get_log_events` after task stops | **Chosen** — standard Fargate pattern |
| S3 log upload | Task writes stdout/stderr to S3 before exit | Rejected — requires custom entrypoint; fragile on crash |
| ECS exec | Use ECS Exec (SSM) to stream live output | Rejected — requires Session Manager plugin; V2 candidate |
| No logs | Return empty stdout/stderr always | Rejected — defeats observability value prop |

---

## Security Architecture

| Layer | Mechanism | Fargate Implementation |
|-------|-----------|------------------------|
| 1 | Chainguard base images | Task definition uses ECR-hosted Chainguard images (Terraform-configured) |
| 2 | No root daemon | Fargate is fully managed; no EC2 root daemon exists |
| 3 | gVisor syscall interception | N/A in V1 — Fargate does not support gVisor; V2 candidate |
| 4 | Network policy | VPC security groups (Terraform) enforce outbound allow-list; SG IDs passed at `run_task` time |
| 5 | Resource limits | ECS task definition sets CPU/memory from `SandboxConfig.cpu_limit` and `memory_limit_mb` |
| 6 | Sensitive data detection | `SensitivityScanner` upstream forces local for sensitive workspaces |
| 7 | Audit trail | All `provision`, `execute`, `destroy` events recorded via `AuditLogger.record()` |

Additional controls:
- S3: all public access blocked, AES256 SSE, no bucket policy grants
- Credentials: standard boto3 chain only — no hardcoded or injected secrets
- Workspace scrub: sensitive-pattern files skipped at upload
- Ephemeral bucket: fully deleted at `destroy()`

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Required constructor param missing or empty | `ValueError` immediately |
| `execute()` with unknown `instance_id` | `RuntimeError(f"unknown instance_id: {instance_id}")` |
| S3 `create_bucket` fails | `RuntimeError` wrapping original error |
| S3 upload fails | `RuntimeError` |
| Workspace does not exist | `FileNotFoundError` |
| Workspace exceeds 500 MB | `ValueError` before any S3 call |
| ECS `run_task` fails | `RuntimeError` wrapping original error |
| Task polling exceeds `timeout_seconds` | `TimeoutError` — caller calls `destroy()` in `finally` |
| CloudWatch logs unavailable | `TaskResult` with empty strings; audit warning; no raise |
| ECS `exitCode` absent | Sentinel `exit_code = -1`; audit warning |
| `destroy()` any error | Swallowed — never raises |

---

## V1 Scope Exclusions

| Feature | Notes |
|---------|-------|
| ECR image override at `run_task` time | Image pre-configured in task def; auto-selection is V2 |
| Dynamic per-task network policy | VPC SGs are static; per-task CIDR injection is V2 |
| Checkpoint + resume mid-execution | V2 |
| Live log streaming | V2; requires ECS Exec + SSM plugin |
| KMS encryption for S3 | V1 uses AES256 SSE; KMS is V2 |
| Multi-region burst | V1 single region only |
| gVisor / Firecracker | Not supported by Fargate in V1 |
| Workspace download inside container | Handled by Terraform-provisioned task definition startup script |

---

## Implementation File Table

| File | Purpose |
|------|---------|
| `src/sandbox/runtime/fargate.py` | `FargateRuntime` implementation |
| `tests/sandbox/runtime/test_fargate.py` | 28-test suite; all boto3 calls mocked |
| `terraform/fargate/main.tf` | ECS cluster + Fargate task definition skeleton |
| `terraform/fargate/variables.tf` | Input variables skeleton |
| `terraform/fargate/outputs.tf` | Outputs consumed by `FargateRuntime.__init__` |
| `pyproject.toml` | Add `boto3>=1.34` to `[project.dependencies]` |

---

## Decisions Added

| # | Decision | Choice | Reason | Date |
|---|----------|--------|--------|------|
| 22 | FargateRuntime credential model | No constructor creds; boto3 chain only | Hardcoded creds violate Security Layer 1; matches AWS IAM best practices | 2026-03-14 |
| 23 | Workspace transfer mechanism | S3 `put_object` per file | Durable, ephemeral, natively accessible inside Fargate | 2026-03-14 |
| 24 | Log retrieval mechanism | CloudWatch Logs `get_log_events` post-stop | Standard Fargate pattern; no extra infrastructure | 2026-03-14 |
| 25 | S3 bucket encryption | AES256 SSE (no KMS) | Sufficient for ephemeral task data; KMS adds V1 complexity | 2026-03-14 |
| 26 | Exit code on missing ECS exitCode | Sentinel -1 + audit warning | Never mask failures silently | 2026-03-14 |
| 27 | V1 image selection | Passthrough (audit-only; task def controls image) | ECR images don't exist yet; full auto-selection deferred to V2 | 2026-03-14 |
| 28 | ECS task polling interval | 5-second sleep between `describe_tasks` calls | Balances latency vs. API call cost | 2026-03-14 |

---

## Consequences

### Positive

- `SandboxManager` selects between `PodmanRuntime` and `FargateRuntime` with no interface changes.
- User owns all AWS resources; SandboxShift is a pure client of the user's account.
- Ephemeral S3 buckets guarantee no persistent data exposure.
- Credential model matches AWS security best practices.

### Negative / Trade-offs

- Cold-start latency: Fargate task startup adds 20–60 seconds vs. local Podman.
- S3 costs: per-task bucket create/destroy — negligible per operation but nonzero.
- CloudWatch log retrieval: only available after task stops; no live progress in V1.
- Network policy is static: `config.network_allow` not dynamically enforced in V1.

---

## References

- ADR-001: Overall System Architecture
- ADR-002: SensitivityScanner Design
- ADR-003: BurstEngine Design
- ADR-004: PodmanRuntime Design
- Decisions Log #3 (AWS Fargate), #2 (Chainguard), #22–#28 (this ADR)
