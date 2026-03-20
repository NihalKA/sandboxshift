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
  description = "ECR registry hostname (e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com). Leave empty to use Docker Hub image names."
  type        = string
  default     = ""
}

variable "environment" {
  description = "Deployment environment tag (e.g. dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "use_default_vpc" {
  description = "If true, use the AWS account default VPC and its subnets automatically. Set false to supply vpc_id and subnet_ids explicitly."
  type        = bool
  default     = true
}

variable "vpc_id" {
  description = "VPC ID where Fargate tasks will run. Only required when use_default_vpc = false."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "List of subnet IDs for Fargate task network interfaces. Only required when use_default_vpc = false."
  type        = list(string)
  default     = []
}

variable "allowed_egress_cidr_blocks" {
  description = "CIDR blocks the sandbox task may reach outbound (port 443, enforced by security group)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
