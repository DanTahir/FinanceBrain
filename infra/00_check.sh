#!/usr/bin/env bash
# Preflight: AWS identity/region, local tools, and OpenRouter key/credit status. Prints no secrets.
source "$(dirname "$0")/lib.sh"

log "account=$ACCOUNT_ID region=$AWS_REGION"
awsx sts get-caller-identity --query Arn --output text | sed -E 's/[0-9]{12}/<acct>/' >&2

for t in docker git openssl; do
  command -v "$t" >/dev/null || die "missing tool: $t"
done
docker info --format '{{.ServerVersion}}' >/dev/null 2>&1 || die "docker daemon not reachable"
log "docker ok"

[ -f Openrouterkey.txt ] || die "Openrouterkey.txt not found in repo root"
git check-ignore -q Openrouterkey.txt || die "Openrouterkey.txt is NOT gitignored"
log "Openrouterkey.txt present and gitignored"

log "OpenRouter key status (limit/usage only):"
python -m uv run python scripts/check_openrouter.py key

PL_ID="$(awsx ec2 describe-managed-prefix-lists --filters "Name=prefix-list-name,Values=$CF_PREFIX_LIST_NAME" --query 'PrefixLists[0].PrefixListId' --output text)"
nonempty "$PL_ID" || die "CloudFront origin-facing prefix list not found"
log "cloudfront prefix list $PL_ID"
log "preflight OK"
