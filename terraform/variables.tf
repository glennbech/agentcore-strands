variable "region" {
  description = "AWS region. AgentCore is only available in select regions (e.g. us-east-1, us-west-2)."
  type        = string
  default     = "us-east-1"
}

variable "suffix" {
  description = "Unique per-student suffix so multiple workshop attendees can deploy into the same AWS account without name collisions. Letters/digits/underscores only (no hyphens), 1–16 chars. Use your initials plus something distinctive (e.g. 'gb42')."
  type        = string
  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,15}$", var.suffix))
    error_message = "suffix must start with a letter and contain only letters, digits, or underscores (max 16 chars total)."
  }
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
