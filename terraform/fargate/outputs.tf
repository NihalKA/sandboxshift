# All outputs below are consumed verbatim by FargateRuntime.__init__

output "cluster_arn" {
  description = "ECS cluster ARN — pass as cluster_arn to FargateRuntime."
  value       = aws_ecs_cluster.sandboxshift.arn
}

output "task_def_arn" {
  description = "ECS task definition ARN — pass as task_def_arn to FargateRuntime."
  value       = aws_ecs_task_definition.sandbox.arn
}

output "region" {
  description = "AWS region — pass as region to FargateRuntime."
  value       = var.aws_region
}

output "log_group" {
  description = "CloudWatch log group name — pass as log_group to FargateRuntime."
  value       = aws_cloudwatch_log_group.sandboxshift.name
}

output "subnet_ids" {
  description = "Subnet IDs — pass as subnet_ids to FargateRuntime."
  value       = var.subnet_ids
}

output "security_group_ids" {
  description = "Security group IDs — pass as security_group_ids to FargateRuntime."
  value       = [aws_security_group.sandbox_task.id]
}
