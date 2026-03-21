terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── Current AWS identity (account ID used in bucket name) ──────────────────────

data "aws_caller_identity" "current" {}

# ── Random suffix for globally-unique bucket name ──────────────────────────
#
# Stored in Terraform state — same suffix survives destroy/re-apply.
# 3 bytes = 6-char hex string. Combined with account ID this is effectively
# unique without requiring user input.

resource "random_id" "workspace_suffix" {
  byte_length = 3
}

# ── VPC (optional default VPC convenience) ────────────────────────────────

data "aws_vpc" "default" {
  count   = var.use_default_vpc ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = var.use_default_vpc ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

# ── ECS Cluster ─────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "sandboxshift" {
  name = var.cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.common_tags
}

resource "aws_ecs_cluster_capacity_providers" "sandboxshift" {
  cluster_name       = aws_ecs_cluster.sandboxshift.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ── CloudWatch Log Group ──────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "sandboxshift" {
  name              = "/sandboxshift/tasks"
  retention_in_days = 7

  tags = local.common_tags
}

# ── S3 Workspace Staging Bucket ───────────────────────────────────────────────
#
# Name is auto-generated: sandboxshift-ws-{account_id}-{6-char-hex}
# No user input required — bucket name is derived from AWS account and a
# random suffix stored in Terraform state.
# Decision #25: AES256 SSE (not KMS) — no key management overhead for V1.
#
# This is the PERSISTENT bucket used across all runs. Each run uploads its
# workspace under a unique prefix (workspace/{instance_id}/) and cleans up
# after itself. The 1-day S3 lifecycle rule is a safety net.

resource "aws_s3_bucket" "workspace" {
  bucket = local.workspace_bucket_name

  tags = local.common_tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "workspace" {
  bucket = aws_s3_bucket.workspace.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "workspace" {
  bucket = aws_s3_bucket.workspace.id
  versioning_configuration {
    status = "Suspended"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "workspace" {
  bucket = aws_s3_bucket.workspace.id

  rule {
    id     = "expire-workspace-objects"
    status = "Enabled"
    expiration {
      days = 1
    }
    filter {}
  }
}

resource "aws_s3_bucket_public_access_block" "workspace" {
  bucket = aws_s3_bucket.workspace.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── IAM Role for ECS Task Execution ──────────────────────────────────────────

resource "aws_iam_role" "task_execution" {
  name = "${var.cluster_name}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ── IAM Role for ECS Task (S3 workspace access) ───────────────────────────

resource "aws_iam_role" "task_role" {
  name = "${var.cluster_name}-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "task_s3" {
  name = "s3-workspace-access"
  role = aws_iam_role.task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
      ]
      Resource = [
        aws_s3_bucket.workspace.arn,
        "${aws_s3_bucket.workspace.arn}/*",
      ]
    }]
  })
}

# ── Security Group for Fargate Tasks (batch mode) ─────────────────────────────

resource "aws_security_group" "sandbox_task" {
  name        = "${var.cluster_name}-sandbox-task"
  description = "Outbound allow-list for SandboxShift Fargate tasks"
  vpc_id      = var.use_default_vpc ? data.aws_vpc.default[0].id : var.vpc_id

  # Egress: allow only to configured CIDRs (network_allow enforcement)
  dynamic "egress" {
    for_each = var.allowed_egress_cidr_blocks
    content {
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = [egress.value]
    }
  }

  # No ingress rules — batch tasks are outbound-only
  tags = local.common_tags
}

# ── Security Group for Server-Mode Tasks (ports exposed) ─────────────────────
#
# Only attached when the user configures ports: in sandboxshift.yaml or via
# the --port CLI flag. Allows ALL TCP inbound so the task's public IP is
# reachable on any configured port. FargateRuntime appends this SG to the
# task's security groups in server mode; batch tasks never use it.
#
# The broad ingress is intentional and documented — server mode is an explicit
# opt-in (requires ports: config). Security Layer 4 (egress allowlist) is
# still enforced via sandbox_task SG which is always present.

resource "aws_security_group" "sandbox_server_task" {
  name        = "${var.cluster_name}-sandbox-server"
  description = "ALL TCP inbound for SandboxShift server-mode Fargate tasks"
  vpc_id      = var.use_default_vpc ? data.aws_vpc.default[0].id : var.vpc_id

  ingress {
    description = "All TCP inbound — server mode only (explicit opt-in)"
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Egress handled by sandbox_task SG (always present alongside this one)
  tags = local.common_tags
}

# ── ECS Task Definition ──────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "sandbox" {
  family                   = var.task_family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task_role.arn

  container_definitions = jsonencode([{
    name      = "sandbox"
    image     = "${var.ecr_registry}/sandboxshift/runtime-python:3.11"
    essential = true

    # entryPoint is set here in the task definition (not overrideable at
    # run_task time via containerOverrides). This overrides any default
    # ENTRYPOINT in the image so that the command injected by FargateRuntime
    # ("-c", "<bootstrap> && <task>") is passed to /bin/sh.
    entryPoint = ["/bin/sh"]

    # Default command placeholder — always overridden by FargateRuntime at
    # run_task time via containerOverrides.command = ["-c", "<full_command>"]
    command = ["-c", "echo sandboxshift ready"]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.sandboxshift.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "sandboxshift"
      }
    }

    # environment injected at run_task time via container overrides
    environment = []
  }])

  tags = local.common_tags
}

# ── Locals ───────────────────────────────────────────────────────────────────────────

locals {
  workspace_bucket_name = "sandboxshift-ws-${data.aws_caller_identity.current.account_id}-${random_id.workspace_suffix.hex}"

  common_tags = {
    Project     = "SandboxShift"
    ManagedBy   = "Terraform"
    Environment = var.environment
  }
}
