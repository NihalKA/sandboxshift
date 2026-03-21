# All outputs below are consumed verbatim by FargateRuntime.__init__
# and documented in terraform/fargate/README.md

output "cluster_arn" {
  description = "ECS cluster ARN — pass as cluster_arn to FargateRuntime (env: FARGATE_CLUSTER_ARN)."
  value       = aws_ecs_cluster.sandboxshift.arn
}

output "task_def_arn" {
  description = "ECS task definition ARN — pass as task_def_arn to FargateRuntime (env: FARGATE_TASK_DEFINITION_ARN)."
  value       = aws_ecs_task_definition.sandbox.arn
}

output "region" {
  description = "AWS region — pass as region to FargateRuntime (env: FARGATE_REGION)."
  value       = var.aws_region
}

output "log_group" {
  description = "CloudWatch log group name — pass as log_group to FargateRuntime (env: FARGATE_LOG_GROUP)."
  value       = aws_cloudwatch_log_group.sandboxshift.name
}

output "subnet_ids" {
  description = "Subnet IDs — pass as subnet_ids to FargateRuntime (env: FARGATE_SUBNET_IDS, comma-separated)."
  value       = var.use_default_vpc ? data.aws_subnets.default[0].ids : var.subnet_ids
}

output "security_group_ids" {
  description = "Security group IDs — pass as security_group_ids to FargateRuntime (env: FARGATE_SECURITY_GROUP_IDS)."
  value       = [aws_security_group.sandbox_task.id]
}

output "server_security_group_id" {
  description = "Server-mode security group ID (ALL TCP inbound) — attached by FargateRuntime only when ports are configured (env: FARGATE_SERVER_SECURITY_GROUP_ID)."
  value       = aws_security_group.sandbox_server_task.id
}

output "workspace_bucket_name" {
  description = "S3 workspace staging bucket name — set as FARGATE_WORKSPACE_BUCKET env var on the API server."
  value       = aws_s3_bucket.workspace.id
}
