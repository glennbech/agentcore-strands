#!/usr/bin/env bash
# Build the agent image for linux/arm64 (AgentCore's required arch) and push
# it to ECR. Uses buildx with QEMU emulation because Codespaces are x86_64.
set -euo pipefail

: "${SUFFIX:?SUFFIX must be set — export your per-student suffix first (e.g. export SUFFIX=gb42)}"
REGION="${AWS_REGION:-us-east-1}"
REPO_NAME="${REPO_NAME:-dessertifier-agent-${SUFFIX}}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"

echo "==> Logging docker into ECR (${REGISTRY})"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> Ensuring buildx builder exists"
docker buildx create --use --name agentcore-builder >/dev/null 2>&1 \
  || docker buildx use agentcore-builder

echo "==> Building and pushing linux/arm64 image to ${IMAGE_URI}"
docker buildx build \
  --platform linux/arm64 \
  --tag "$IMAGE_URI" \
  --push \
  "$(dirname "$0")/../agent"

echo
echo "==> Pushed: ${IMAGE_URI}"
echo "==> Now: cd terraform && terraform init && terraform apply"
