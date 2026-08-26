output "ecr_repository_url" {
  description = "ECR repo Terraform reads (created manually in Step 2 with `aws ecr create-repository`, not managed by Terraform)."
  value       = data.aws_ecr_repository.agent.repository_url
}

output "image_digest" {
  description = "Digest of the currently-deployed image."
  value       = data.aws_ecr_image.agent.image_digest
}

output "agent_runtime_arn" {
  description = "ARN used to invoke the AgentCore runtime."
  value       = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn
}

output "agent_runtime_id" {
  description = "Short ID of the runtime; appears in the CloudWatch log group name."
  value       = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_id
}

output "runtime_role_arn" {
  description = "IAM role the AgentCore runtime assumes."
  value       = aws_iam_role.runtime.arn
}

output "memory_id" {
  description = "AgentCore Memory ID that remember/recall write to and read from."
  value       = aws_bedrockagentcore_memory.sessions.id
}
