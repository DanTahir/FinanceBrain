#!/usr/bin/env bash
# Seed production graphs from the committed corpus and extraction/embedding cache (no LLM calls), inside the
# running API container, with the token vault wrapped by KMS. Also snapshots pristine__<tenant> for Reset demo.
source "$(dirname "$0")/lib.sh"
trap cleanup_tmp EXIT

ssm_run_script "financebrain seed" 1800 infra/remote/seed.sh || die "remote seed failed"

CF_DOMAIN="$(state_get CF_DOMAIN)"
if nonempty "$CF_DOMAIN"; then
  log "https://$CF_DOMAIN/api/healthz -> $(curl -s --max-time 20 "https://$CF_DOMAIN/api/healthz" | head -c 300)"
fi
