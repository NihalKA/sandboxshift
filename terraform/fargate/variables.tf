variable "aws_region" {
  description = "AWS region to deploy SandboxShift Fargate resources."
  type        = string
}

variable "cluster_name" {
  description = "Name of the ECS cluster."
  type        = string
  default     = "sandboxshift"
}

variable "task_family" {
  description = "ECS task definition family name."
  type        = string
  default     = "sandboxshift-sandbox"
}

variable "task_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 4096
}

variable "ecr_registry" {
  description = "ECR registry hostname (e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com)."
  type        = string
}

variable "environment" {
  description = "Deployment environment tag (e.g. dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "vpc_id" {
  description = "VPC ID where Fargate tasks will run."
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for Fargate task network interfaces."
  type        = list(string)
}

variable "allowed_egress_cidr_blocks" {
  description = "CIDR blocks the sandbox task may reach outbound (enforced by security group)."
  type        = list(string)
  default     = []
}
