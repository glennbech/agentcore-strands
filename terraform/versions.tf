terraform {
  required_version = ">= 1.6.0"

  required_providers {
    # AgentCore is very new but the handwritten AWS provider caught up:
    # `aws_bedrockagentcore_agent_runtime` (and ~20 sibling resources) landed
    # in the aws provider recently. It has native retry for IAM propagation,
    # which the auto-generated `awscc` provider lacks — that's why we're on
    # `aws` here instead of `awscc`. If you ever need an even-newer AWS
    # service that hasn't made it into `aws` yet, `awscc` is the fallback.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}
