#!/usr/bin/env bash
# Shared settings and helpers for FinanceBrain infra scripts. Source this file; do not execute it.
# All scripts are idempotent: re-running them converges to the same resources.
set -euo pipefail

# Git Bash on Windows: stop MSYS from rewriting args like /financebrain/x into Windows paths.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
export AWS_PAGER=""

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"

PROJECT=financebrain
TAG_KEY=Project
TAG_VAL=FinanceBrain
STATE_FILE="$REPO_ROOT/infra/.state.env"   # resource IDs only, no secrets (gitignored)
TMP_DIR="infra/.tmp"                          # relative path so aws.exe file:// works (gitignored)
mkdir -p "$TMP_DIR"

ROLE_NAME=financebrain-ec2
PROFILE_NAME=financebrain-ec2
KMS_ALIAS=alias/financebrain-vault
SSM_PREFIX=/financebrain
INSTANCE_NAME=financebrain
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.xlarge}"
INSTANCE_AZ="${INSTANCE_AZ:-us-east-1a}"
ROOT_GB=40
SG_NAME=financebrain-origin
CF_PREFIX_LIST_NAME=com.amazonaws.global.cloudfront.origin-facing
CF_COMMENT=financebrain
ECR_REPOS=(financebrain/api financebrain/web)
AMI_PARAM=/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64

# CloudFront managed policy IDs (global constants).
CF_CACHING_OPTIMIZED=658327ea-f89d-4fab-a63d-7e88639e58f6
CF_CACHING_DISABLED=4135ea2d-6df8-44a3-9df3-4b5a84be39ad
CF_ORP_ALL_VIEWER_EXCEPT_HOST=b689b0a8-53d0-40ab-baf2-68738e2966ac

# Windows aws.exe emits CRLF; strip CR so values compare cleanly.
awsx() { aws "$@" | tr -d '\r'; }
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
nonempty() { [ -n "${1:-}" ] && [ "$1" != "None" ] && [ "$1" != "null" ]; }

ACCOUNT_ID="$(awsx sts get-caller-identity --query Account --output text)" || die "AWS credentials not available (run aws sso login)"
BUCKET="financebrain-backups-${ACCOUNT_ID}"
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

state_get() {
  [ -f "$STATE_FILE" ] || return 0
  grep -E "^$1=" "$STATE_FILE" | tail -1 | cut -d= -f2- || true
}
state_set() {
  touch "$STATE_FILE"
  { grep -v -E "^$1=" "$STATE_FILE" || true; echo "$1=$2"; } > "$STATE_FILE.tmp"
  mv "$STATE_FILE.tmp" "$STATE_FILE"
}

# Write stdin to infra/.tmp/<name>.json and print a file:// URI usable by aws.exe.
tmpjson() {
  mkdir -p "$TMP_DIR"
  cat > "$TMP_DIR/$1.json"
  echo "file://$TMP_DIR/$1.json"
}
cleanup_tmp() { rm -rf "$TMP_DIR"; }

# Create a random SecureString under /financebrain/<name> only if it does not exist yet.
ssm_ensure_random() {
  local name="$SSM_PREFIX/$1"
  if awsx ssm get-parameter --name "$name" --query Parameter.Name --output text >/dev/null 2>&1; then
    log "ssm $name exists"
  else
    awsx ssm put-parameter --name "$name" --type SecureString \
      --value "$(openssl rand -hex 24)" --tags "Key=$TAG_KEY,Value=$TAG_VAL" >/dev/null
    log "ssm $name created"
  fi
}

ssm_get_secret() {
  awsx ssm get-parameter --name "$SSM_PREFIX/$1" --with-decryption --query Parameter.Value --output text
}
