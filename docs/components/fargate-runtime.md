# FargateRuntime

> Cloud sandbox runtime — runs agent tasks inside the caller's own AWS ECS Fargate cluster.

---

## What It Does

`FargateRuntime` is the V1 cloud sandbox runtime for SandboxShift. When the [BurstEngine](burst-engine.md) decides a task should run in the cloud, `FargateRuntime` is the execution adapter that makes it happen.

Key guarantee: **the user's code never touches shared SandboxShift servers**. Every container runs inside the caller's own AWS account, in their own ECS cluster, billed directly to them. SandboxShift never sees the workload.

```
Developer machine
      │
      ▼
BurstEngine (decides: cloud)
      │
      ▼
FargateRuntime
      │
      ▼
Caller's AWS account
  ├── S3 bucket  (ephemeral, created per session)
  └── ECS Fargate task  (caller's cluster, caller's VPC)
```

---

## Lifecycle

`FargateRuntime` has a strict three-phase lifecycle that matches the `Runtime` ABC defined in `src/sandbox/runtime/base.py`.

### Phase 1 — `provision(workspace, config) → instance_id`

1. **Validates workspace size** — raises `ValueError` if the workspace exceeds 500 MB. Large workspaces are rejected before any AWS call is made.
2. **Creates an ephemeral S3 bucket** named `sandboxshift-{instance_id}` with:
   - AES-256 server-side encryption (SSE)
   - Full S3 public access block (all four flags enabled)
3. **Uploads workspace files** under a `workspace/` prefix, skipping any file that matches `.env`, `.pem`, or `.key` patterns (Security Layer 6).
4. **Stores internal state** (bucket name, instance ID) for use by `execute` and `destroy`.
5. **Emits an audit event** via `AuditLogger`.

Returns an opaque `instance_id` string (UUID4).

### Phase 2 — `execute(instance_id, task, config) → TaskResult`

1. **Launches an ECS Fargate task** against the configured cluster with:
   - `launchType="FARGATE"`
   - Command override: `/bin/sh -c {task}`
   - Environment variables injected into the container:
     - `SS_BUCKET` — name of the ephemeral S3 bucket
     - `SS_PREFIX` — `workspace/`
     - `SS_TASK_ID` — the `instance_id`
2. **Polls `describe_tasks`** every 5 seconds until `lastStatus == "STOPPED"`.
3. **Fetches CloudWatch logs** from the log group for the task. In V1, all output is returned as `stdout`; `stderr` is always an empty string (stdout and stderr are combined in the log stream).
4. Returns a `TaskResult(exit_code, stdout, stderr, duration_seconds)`.

### Phase 3 — `destroy(instance_id) → None`

1. **Stops the ECS task** — idempotent; errors are silently ignored.
2. **Empties and deletes the S3 bucket** — all objects are deleted before the bucket is removed.
3. **Clears internal state**.
4. **Writes an audit event** — this happens inside a `finally` block. The audit record is **guaranteed to fire even if S3 cleanup raises an exception** (Security Layer 7).

`destroy` never raises. Cleanup errors are swallowed after the audit event is written.

---

## AWS Infrastructure Prerequisites

The following AWS resources must exist **before** constructing a `FargateRuntime` instance. These are provisioned by Terraform in `terraform/fargate/` — see [Terraform Setup](#terraform-setup) below.

| Resource | Description |
|----------|-------------|
| ECS Cluster | The cluster where Fargate tasks are launched (`cluster_arn`) |
| ECS Task Definition | Must include a container named `"sandbox"` and a CloudWatch log stream prefix of `"sandboxshift"` (`task_def_arn`) |
| VPC Subnets | Subnets with internet access for the Fargate task network interface (`subnet_ids`) |
| Security Group | Zero-ingress rule; HTTPS-only egress (`security_group_ids`) |
| CloudWatch Log Group | Where task logs are written (`log_group`) |

### Example construction

```python
from sandboxshift.sandbox.runtime.fargate import FargateRuntime

runtime = FargateRuntime(
    cluster_arn="arn:aws:ecs:us-east-1:123456789012:cluster/sandboxshift",
    task_def_arn="arn:aws:ecs:us-east-1:123456789012:task-definition/sandboxshift-sandbox:1",
    subnet_ids=["subnet-abc123", "subnet-def456"],
    security_group_ids=["sg-0123456789abcdef0"],
    region="us-east-1",
    log_group="/sandboxshift/tasks",
)
```

---

## Security Model

SandboxShift uses a 7-layer defence-in-depth security model. Here is how `FargateRuntime` contributes to each layer.

| Layer | Name | FargateRuntime Behaviour |
|-------|------|--------------------------|
| 1 | Chainguard base image | Runtime images (`sandboxshift/runtime-*`) are Chainguard-based, zero-CVE images. The task definition references these images. |
| 2 | Podman rootless | **N/A** — Fargate manages container isolation at the hypervisor level. Podman is a local-only concern. |
| 3 | gVisor | **V2 feature** — gVisor syscall interception is not available in Fargate V1. |
| 4 | Network policy | The security group enforces zero ingress and HTTPS-only egress. This is a hard perimeter control. *V1 limitation:* the `network_allow` list from `SandboxConfig` is not dynamically applied to the security group rules — this is a V2 feature. |
| 5 | Resource limits | CPU and memory are defined statically in the Fargate task definition via Terraform variables (`task_cpu`, `task_memory`). *V1 limitation:* `SandboxConfig.cpu_limit` and `memory_limit_mb` values are not applied per-task — this is a V2 feature. |
| 6 | Sensitive data detection | Files matching `.env`, `.pem`, or `.key` are **never uploaded** to S3. Workspaces exceeding 500 MB are rejected. AWS credentials are sourced from the environment only (IAM role or `AWS_PROFILE`) — never accepted as constructor arguments. |
| 7 | Audit trail | Every `provision`, `execute`, and `destroy` event is recorded. The `destroy` audit write is in a `finally` block — **it fires even if S3 cleanup fails**. |

---

## V1 Limitations

These are known, intentional limitations for the V1 release. They are tracked and will be addressed in V2.

- **CloudWatch log stream name is approximate.** In V1, the stream name is constructed from `instance_id`. The real ECS stream name follows the pattern `{prefix}/{container}/{task_short_id}`. Adjust this when testing against real AWS infrastructure.
- **`assignPublicIp: "ENABLED"`** — Fargate tasks receive a public IP address. This is mitigated by the zero-ingress security group, but in V2 tasks will run in private subnets with a NAT gateway.
- **`SandboxConfig.network_allow` is not enforced in cloud mode.** The security group controls egress statically. Dynamic per-task network allowlists are a V2 feature.
- **`SandboxConfig.cpu_limit` and `memory_limit_mb` do not override the task definition.** Resource sizing must be done via Terraform before deployment.
- **`stderr` is always an empty string.** ECS/CloudWatch combines stdout and stderr in a single log stream. The V1 implementation returns all output as `stdout`.
- **Image selection is audit-only.** `FargateRuntime` detects the appropriate runtime image from workspace markers (same logic as `PodmanRuntime`) and records the selection in the audit log, but the Fargate task definition controls which container image actually runs. Override the task definition via Terraform to change the image.

---

## Configuration Reference

### Constructor Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `cluster_arn` | `str` | Yes | ARN of the ECS cluster (e.g. `arn:aws:ecs:us-east-1:…:cluster/sandboxshift`) |
| `task_def_arn` | `str` | Yes | ARN of the ECS task definition including family and revision (e.g. `…:task-definition/sandboxshift-sandbox:1`) |
| `subnet_ids` | `list[str]` | Yes | One or more VPC subnet IDs for the Fargate task network interface |
| `security_group_ids` | `list[str]` | Yes | One or more security group IDs applied to the Fargate task |
| `region` | `str` | Yes | AWS region string (e.g. `"us-east-1"`) |
| `log_group` | `str` | Yes | CloudWatch Logs log group name (e.g. `"/sandboxshift/tasks"`) |
| `audit_logger` | `AuditLogger` | No | Audit logger instance. Defaults to the V1 no-op stub from `src/observability/audit.py` |

### Environment (AWS Credentials)

`FargateRuntime` never accepts AWS credentials as arguments. It uses the standard AWS credential chain, in order:

1. IAM instance/task role (recommended for EC2/ECS deployments)
2. `AWS_PROFILE` environment variable
3. `~/.aws/credentials` file
4. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` environment variables

Do **not** hardcode credentials. Rotate them using IAM roles.

---

## Terraform Setup

All required AWS resources are provisioned by the Terraform module in `terraform/fargate/`.

```bash
cd terraform/fargate
terraform init
terraform apply \
  -var="vpc_id=vpc-..." \
  -var="subnet_ids=[\"subnet-...\",\"subnet-...\"]"
```

After `terraform apply`, run:

```bash
terraform output
```

The output values map directly to `FargateRuntime` constructor parameters:

| Terraform Output | Constructor Parameter |
|------------------|-----------------------|
| `cluster_arn` | `cluster_arn` |
| `task_def_arn` | `task_def_arn` |
| `subnet_ids` | `subnet_ids` |
| `security_group_id` | `security_group_ids` |
| `log_group` | `log_group` |

---

## Running the Tests

All AWS calls are mocked via `unittest.mock` — no real AWS account or credentials are needed to run the test suite.

```bash
pytest tests/sandbox/runtime/test_fargate.py -v
# 28 tests, all AWS calls mocked via unittest.mock
```

To run with coverage:

```bash
pytest tests/sandbox/runtime/test_fargate.py -v --cov=src/sandbox/runtime/fargate --cov-report=term-missing
```

---

## Related Components

- [BurstEngine](burst-engine.md) — decides whether a task runs via `PodmanRuntime` or `FargateRuntime`
- [SensitivityScanner](sensitivity-scanner.md) — gates cloud execution; sensitive workspaces never reach `FargateRuntime`
- [PodmanRuntime](podman-runtime.md) — the local counterpart to `FargateRuntime`
