variable "region" {
  description = "AWS region. AgentCore is only available in select regions (e.g. us-east-1, us-west-2)."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Base name used for the IAM role and AgentCore runtime. AgentCore runtime names must match [a-zA-Z][a-zA-Z0-9_]{0,47}, so no hyphens."
  type        = string
  default     = "emojifier_agent"
}

variable "ecr_repository_name" {
  description = "Existing ECR repo that holds the agent image. Create it with `aws ecr create-repository` before `terraform apply`."
  type        = string
  default     = "emojifier-agent"
}

variable "image_tag" {
  description = "ECR image tag to deploy. Terraform resolves this to a digest so re-pushes trigger updates."
  type        = string
  default     = "latest"
}

variable "model_id" {
  description = "Bedrock model / inference-profile ID. Passed to the container as the MODEL_ID env var. Must be enabled for your account and region."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}
