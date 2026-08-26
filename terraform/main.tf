data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ECR repo is externally-managed (see step 2 in the README) — we just read it.
data "aws_ecr_repository" "agent" {
  name = var.ecr_repository_name
}

data "aws_ecr_image" "agent" {
  repository_name = var.ecr_repository_name
  image_tag       = var.image_tag
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # Pin by digest so re-pushing the same tag actually triggers a runtime update
  # on `terraform apply`. If we referenced `:latest` directly, Terraform would
  # never see a diff.
  image_uri = "${data.aws_ecr_repository.agent.repository_url}@${data.aws_ecr_image.agent.image_digest}"
}

# Trust policy: only the AgentCore service can assume this role, and only for
# resources in this account. Textbook confused-deputy guard.
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.name}-runtime"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid    = "ECRPull"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [data.aws_ecr_repository.agent.arn]
  }

  statement {
    sid       = "ECRAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "logs:DescribeLogGroups",
    ]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/*"]
  }

  statement {
    sid    = "BedrockInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "Workload"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
      "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "runtime" {
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_bedrockagentcore_agent_runtime" "agent" {
  agent_runtime_name = var.name
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = local.image_uri
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  # Container reads MODEL_ID from the environment (agent/app.py). Setting it
  # here lets you swap the model without rebuilding/re-pushing the image.
  environment_variables = {
    MODEL_ID = var.model_id
  }

  depends_on = [
    aws_iam_role_policy.runtime,
  ]
}
