#!/usr/bin/env bash
# Build the API and dashboard images for the EKS nodes (linux/amd64) and push them to the
# ECR repositories Terraform created, tagged with the current git commit.
#
#   ./build-and-push.sh            # from infra/eks, after `terraform apply`
set -euo pipefail

cd "$(dirname "$0")"
REGION=$(terraform output -raw region)
API_REPO=$(terraform output -json ecr_repositories | python -c 'import json,sys; print(json.load(sys.stdin)["api"])')
DASHBOARD_REPO=$(terraform output -json ecr_repositories | python -c 'import json,sys; print(json.load(sys.stdin)["dashboard"])')
REGISTRY=${API_REPO%%/*}
TAG=$(git rev-parse --short HEAD)
ROOT=$(git rev-parse --show-toplevel)

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# The API image also runs the worker. The dashboard calls the API on its own origin
# (the ingress sends /api to the API), so no API URL is baked into it.
docker build --platform linux/amd64 -t "$API_REPO:$TAG" "$ROOT"
docker build --platform linux/amd64 -t "$DASHBOARD_REPO:$TAG" "$ROOT/dashboard"
docker push "$API_REPO:$TAG"
docker push "$DASHBOARD_REPO:$TAG"

echo "Pushed tag $TAG"
