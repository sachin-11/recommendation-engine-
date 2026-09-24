#!/usr/bin/env bash
# Build, push and deploy RecoEngine to the EC2 host, then verify /health.
#
#   DEPLOY_HOST=203.0.113.10 ECR_REGISTRY=123456789012.dkr.ecr.us-east-1.amazonaws.com \
#     ./scripts/deploy.sh
#
# Optional: AWS_REGION (us-east-1), DEPLOY_USER (ubuntu), SSH_KEY (./recoengine.pem),
#           DEPLOY_PATH (/home/ubuntu/recoengine), IMAGE_TAG (git short sha),
#           PUBLIC_URL (http://$DEPLOY_HOST), NEXT_PUBLIC_API_BASE_URL, NEXT_PUBLIC_DOCS_URL,
#           SKIP_BUILD=1 to redeploy already-pushed images.
# The server needs the repo checked out at DEPLOY_PATH and a filled-in .env.production.
set -euo pipefail

: "${DEPLOY_HOST:?set DEPLOY_HOST}"
: "${ECR_REGISTRY:?set ECR_REGISTRY}"
AWS_REGION="${AWS_REGION:-us-east-1}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-./recoengine.pem}"
DEPLOY_PATH="${DEPLOY_PATH:-/home/$DEPLOY_USER/recoengine}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"
PUBLIC_URL="${PUBLIC_URL:-http://$DEPLOY_HOST}"
BACKEND="$ECR_REGISTRY/recoengine-backend"
DASHBOARD="$ECR_REGISTRY/recoengine-dashboard"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ssh_opts=(-o StrictHostKeyChecking=accept-new)
[ -f "$SSH_KEY" ] && ssh_opts+=(-i "$SSH_KEY")

if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
  echo "warning: uncommitted changes are included in the images but not in the server checkout" >&2
fi

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  log "Building images ($IMAGE_TAG)"
  docker build --target runtime -t "$BACKEND:$IMAGE_TAG" -t "$BACKEND:latest" "$ROOT"
  docker build \
    --build-arg NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-}" \
    --build-arg NEXT_PUBLIC_DOCS_URL="${NEXT_PUBLIC_DOCS_URL:-}" \
    -t "$DASHBOARD:$IMAGE_TAG" -t "$DASHBOARD:latest" "$ROOT/dashboard"

  log "Pushing to ECR"
  aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "$ECR_REGISTRY"
  for image in "$BACKEND" "$DASHBOARD"; do
    docker push "$image:$IMAGE_TAG"
    docker push "$image:latest"
  done
fi

log "Deploying on $DEPLOY_USER@$DEPLOY_HOST"
# shellcheck disable=SC2087  # variables are meant to expand locally
ssh "${ssh_opts[@]}" "$DEPLOY_USER@$DEPLOY_HOST" bash -s <<EOF
set -euo pipefail
cd "$DEPLOY_PATH"
git pull --ff-only --quiet || echo "git pull skipped (not fast-forward); deploying current checkout"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"
export IMAGE_TAG="$IMAGE_TAG"
compose="docker compose -f docker-compose.prod.yml --env-file .env.production"
\$compose pull app worker dashboard
echo "--- migrations"
\$compose run --rm migrate
echo "--- restarting"
\$compose up -d --remove-orphans
for i in \$(seq 1 30); do
  if curl -fsS http://localhost/health >/dev/null; then echo "healthy on the host"; exit 0; fi
  sleep 5
done
echo "API did not become healthy"; \$compose logs --tail 100 app; exit 1
EOF

log "Health check: $PUBLIC_URL/health"
for i in $(seq 1 12); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$PUBLIC_URL/health" || true)"
  if [ "$code" = "200" ]; then
    echo "OK — deployed $IMAGE_TAG"
    exit 0
  fi
  echo "attempt $i: HTTP $code"; sleep 5
done
echo "FAILED: $PUBLIC_URL/health did not return 200" >&2
exit 1
