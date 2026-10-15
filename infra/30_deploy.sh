#!/usr/bin/env bash
# Build the api and web images, push them to ECR, and roll them out on the instance via SSM Run Command.
# Env: TAG (default: git short sha, plus -dirty-<time> if the tree has changes), SKIP_BUILD=1 to reuse pushed images.
source "$(dirname "$0")/lib.sh"
trap cleanup_tmp EXIT

bash scripts/check_secrets.sh >/dev/null || die "secret check failed"

if [ -z "${TAG:-}" ]; then
  TAG="$(git rev-parse --short HEAD)"
  if [ -n "$(git status --porcelain -- backend datagen data evals frontend infra pyproject.toml uv.lock)" ]; then
    TAG="$TAG-dirty-$(date +%H%M%S)"
  fi
fi
API_IMG="$ECR_REGISTRY/financebrain/api:$TAG"
WEB_IMG="$ECR_REGISTRY/financebrain/web:$TAG"
mkdir -p logs

if [ "${SKIP_BUILD:-0}" != 1 ]; then
  log "ecr login $ECR_REGISTRY"
  awsx ecr get-login-password | docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null
  log "building $API_IMG (log: logs/build_api.log)"
  docker build --platform linux/amd64 -f infra/docker/api.Dockerfile -t "$API_IMG" . > logs/build_api.log 2>&1 \
    || { tail -40 logs/build_api.log; die "api build failed"; }
  log "building $WEB_IMG (log: logs/build_web.log)"
  docker build --platform linux/amd64 -f infra/docker/web.Dockerfile -t "$WEB_IMG" . > logs/build_web.log 2>&1 \
    || { tail -40 logs/build_web.log; die "web build failed"; }
  log "pushing images"
  docker push -q "$API_IMG" >/dev/null
  docker push -q "$WEB_IMG" >/dev/null
  log "pushed $TAG"
fi

B64C="$(base64 -w0 infra/docker-compose.prod.yml)"
SSM_EXTRA_CMDS="\"mkdir -p /opt/financebrain\",\"echo $B64C | base64 -d > /opt/financebrain/docker-compose.yml\"," \
  ssm_run_script "financebrain deploy $TAG" 1200 infra/remote/deploy.sh "$TAG" "$ECR_REGISTRY" "$AWS_REGION" "$BUCKET" \
  || die "remote deploy failed"
state_set DEPLOYED_TAG "$TAG"

DIST_ID="$(state_get CF_DIST_ID)"
CF_DOMAIN="$(state_get CF_DOMAIN)"
if nonempty "$DIST_ID"; then
  INV="$(awsx cloudfront create-invalidation --distribution-id "$DIST_ID" --paths '/*' --query Invalidation.Id --output text)"
  log "cloudfront invalidation $INV"
fi
if nonempty "$CF_DOMAIN"; then
  log "https://$CF_DOMAIN/healthz -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$CF_DOMAIN/healthz")"
  log "https://$CF_DOMAIN/api/healthz -> $(curl -s --max-time 20 "https://$CF_DOMAIN/api/healthz" | head -c 300)"
fi
log "deployed $TAG"
