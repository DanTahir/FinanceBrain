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
# AllViewerAndCloudFrontHeaders-2022-06: also forwards CloudFront-Viewer-Address, which the API's per-IP
# rate limiter uses (X-Forwarded-For alone can be prefixed by the client).
CF_ORP_ALL_VIEWER_AND_CF=33f36d7e-f396-46d9-90e0-52428a34d9dc

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

# Run a local script on the instance as root via SSM Run Command, wait, and print its output.
# Usage: [SSM_EXTRA_CMDS='"cmd1","cmd2",'] ssm_run_script <comment> <timeout_s> <script> [args...]
# SSM_EXTRA_CMDS are JSON string items (comma-terminated) run before the script. Returns 0 on Success.
ssm_run_script() {
  local comment="$1" timeout="$2" script="$3"; shift 3
  local iid; iid="$(state_get INSTANCE_ID)"
  nonempty "$iid" || die "INSTANCE_ID missing; run infra/10_base.sh"
  local b64; b64="$(base64 -w0 "$script")"
  local params; params="$(tmpjson "ssm-${RANDOM}-${RANDOM}" <<EOF
{"commands":[${SSM_EXTRA_CMDS:-}"echo $b64 | base64 -d > /tmp/fb-run.sh","bash /tmp/fb-run.sh $*"],"executionTimeout":["$timeout"]}
EOF
)"
  local cmd_id; cmd_id="$(awsx ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript \
    --comment "$comment" --parameters "$params" --query Command.CommandId --output text)"
  log "ssm command $cmd_id ($comment)"
  local st=Pending i
  for i in $(seq 1 $(( timeout / 10 + 6 ))); do
    sleep 10
    st="$(awsx ssm get-command-invocation --command-id "$cmd_id" --instance-id "$iid" --query Status --output text 2>/dev/null || echo Pending)"
    case "$st" in Success|Failed|Cancelled|TimedOut) break ;; esac
  done
  awsx ssm get-command-invocation --command-id "$cmd_id" --instance-id "$iid" --query StandardOutputContent --output text
  local err; err="$(awsx ssm get-command-invocation --command-id "$cmd_id" --instance-id "$iid" --query StandardErrorContent --output text)"
  [ -n "$err" ] && printf 'stderr:\n%s\n' "$err" | tail -25 >&2
  log "ssm status: $st"
  [ "$st" = Success ]
}
