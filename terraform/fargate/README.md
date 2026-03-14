# SandboxShift — Fargate Terraform

Provisions all AWS resources needed for SandboxShift's cloud burst mode in **your own AWS account**.

> **Your infrastructure, your data.** SandboxShift never uses third-party cloud.

---

## What This Creates

| Resource | Purpose |
|----------|--------|
| ECS cluster | Fargate task execution environment |
| ECS task definition | Container spec (image, CPU, memory, logging) |
| IAM execution role | Allows Fargate to pull images and write CloudWatch logs |
| IAM task role | Allows the sandbox container to read workspace from S3 (read-only, scoped to one bucket) |
| S3 bucket | Workspace staging — files uploaded before task, auto-expired after 1 day |
| CloudWatch log group | Task stdout/stderr, 7-day retention |
| Security group | Egress port 443 to 0.0.0.0/0; no ingress |

---

## Prerequisites

| Requirement | Version |
|-------------|--------|
| Terraform | ≥ 1.7 |
| AWS CLI | Any recent version |
| AWS credentials | Configured via `AWS_PROFILE`, `~/.aws/credentials`, or IAM role |

**Required IAM permissions for the deploying user:**

```
ecs:*
iam:CreateRole, iam:AttachRolePolicy, iam:PutRolePolicy, iam:PassRole
s3:CreateBucket, s3:PutBucketPolicy, s3:PutLifecycleConfiguration, s3:PutEncryptionConfiguration, s3:PutPublicAccessBlock
logs:CreateLogGroup, logs:PutRetentionPolicy
ec2:CreateSecurityGroup, ec2:AuthorizeSecurityGroupEgress, ec2:DescribeVpcs, ec2:DescribeSubnets
```

---

## Quick Start

### Step 1 — Initialise Terraform

```bash
cd terraform/fargate
terraform init
```

### Step 2 — Create `terraform.tfvars`

Minimal configuration using your default VPC (recommended for most developers):

```hcl
aws_region            = "us-east-1"
workspace_bucket_name = "sandboxshift-workspace-123456789012"  # replace with your AWS account ID
```

If you prefer an explicit VPC:

```hcl
aws_region            = "us-east-1"
workspace_bucket_name = "sandboxshift-workspace-123456789012"
use_default_vpc       = false
vpc_id                = "vpc-0abc123def456789"
subnet_ids            = ["subnet-0abc123def456789", "subnet-0def456ghi789012"]
```

### Step 3 — Review and apply

```bash
terraform plan
terraform apply
```

Expected: ~12 resources created in 30–60 seconds.

### Step 4 — Export env vars for the API server

```bash
export FARGATE_CLUSTER_ARN=$(terraform output -raw cluster_arn)
export FARGATE_TASK_DEFINITION_ARN=$(terraform output -raw task_def_arn)
export FARGATE_SUBNET_IDS=$(terraform output -json subnet_ids | jq -r 'join(",")')
export FARGATE_SECURITY_GROUP_IDS=$(terraform output -json security_group_ids | jq -r 'join(",")')
export FARGATE_LOG_GROUP=$(terraform output -raw log_group)
export FARGATE_REGION=$(terraform output -raw region)
```

All 6 env vars must be set for cloud burst to activate. If any are missing, SandboxShift silently falls back to local-only mode (this is by design — Decision #15, fail-closed).

### Step 5 — Verify

Start the API server, then:

```bash
curl http://localhost:8000/health
```

---

## Cost Estimate

| Resource | Typical cost |
|----------|-------------|
| Fargate tasks | ~$0.01–0.05 per task (pay-per-second) |
| CloudWatch logs | ~$0.50/GB ingested, 7-day retention |
| S3 workspace staging | Negligible (< 500 MB per session, auto-expired) |
| **Total for casual developer use** | **< $5/month** |

---

## Security Properties (AGENTS.md)

| Decision | What Terraform enforces |
|----------|------------------------|
| #22 — No hardcoded credentials | Provider block never accepts `access_key`/`secret_key` |
| #25 — AES256 SSE (not KMS) | `SSEAlgorithm = "AES256"` on workspace bucket |
| Security Layer 6 | S3 bucket blocks all public access (4 flags) |
| Security Layer 7 | CloudWatch log group with 7-day retention |

---

## Destroying Resources

```bash
terraform destroy
```

**Note:** Workspace objects uploaded during tasks auto-expire after 1 day (S3 lifecycle rule). Even if `terraform destroy` is delayed, no workspace data persists beyond 24 hours.

---

## Variables Reference

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `aws_region` | — | Yes | AWS region |
| `workspace_bucket_name` | — | Yes | Globally unique S3 bucket name |
| `cluster_name` | `sandboxshift` | No | ECS cluster name |
| `task_family` | `sandboxshift-sandbox` | No | ECS task definition family |
| `task_cpu` | `1024` | No | CPU units (1024 = 1 vCPU) |
| `task_memory` | `4096` | No | Memory in MiB |
| `ecr_registry` | `""` | No | ECR registry (leave empty for Docker Hub) |
| `environment` | `dev` | No | Tag value |
| `use_default_vpc` | `true` | No | Auto-detect default VPC |
| `vpc_id` | `""` | No | VPC ID (only if `use_default_vpc = false`) |
| `subnet_ids` | `[]` | No | Subnet IDs (only if `use_default_vpc = false`) |
| `allowed_egress_cidr_blocks` | `["0.0.0.0/0"]` | No | Egress CIDRs for port 443 |
