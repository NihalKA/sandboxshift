terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── VPC (optional default VPC convenience) ─────────────────────────────────────────────────

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

# ── ECS Cluster ────────────────────────────────────────────────────────────────────────────

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

# ── CloudWatch Log Group ────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "sandboxshift" {
  name              = "/sandboxshift/tasks"
  retention_in_days = 7

  tags = local.common_tags
}

# ── S3 Workspace Staging Bucket ─────────────────────────────────────────────────────────────
#
# Stores workspace files during Fargate task execution.
# FargateRuntime uploads workspace contents here, then downloads inside the container.
# All objects auto-expire after 1 day (safety net if destroy() fails).
# Decision #25: AES256 SSE (not KMS) — no key management overhead for V1.

resource "aws_s3_bucket" "workspace" {
  bucket = var.workspace_bucket_name

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

# ── IAM Role for ECS Task Execution ──────────────────────────────────────────────────────────────

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

# ── IAM Role for ECS Task (S3 workspace access) ─────────────────────────────────────────────────

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
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.workspace.arn,
        "${aws_s3_bucket.workspace.arn}/*",
      ]
    }]
  })
}

# ── Security Group for Fargate Tasks ─────────────────────────────────────────────────────────────

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

  # No ingress rules — Fargate tasks are outbound-only
  tags = local.common_tags
}

# ── ECS Task Definition ───────────────────────────────────────────────────────────────────────

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

# ── Locals ─────────────────────────────────────────────────────────────────────────────────

locals {
  common_tags = {
    Project     = "SandboxShift"
    ManagedBy   = "Terraform"
    Environment = var.environment
  }
}
