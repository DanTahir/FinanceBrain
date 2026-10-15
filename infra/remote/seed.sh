#!/usr/bin/env bash
# Runs ON the EC2 instance as root via SSM Run Command (see infra/40_seed.sh).
# Seeds production FalkorDB from the committed corpus inside the API container:
#   1. redact both tenants with a fresh KMS-wrapped vault (tokens are sequential in first-seen order, so the
#      redacted corpus is byte-identical to the committed one and the extraction/embedding cache stays valid),
#   2. seed brain__<tenant> graphs offline from the cache (no LLM or embedding calls) and snapshot pristine__<tenant>.
# Usage: seed.sh            (idempotent; --drop rebuilds the brain graphs, memory graphs are untouched)
set -euo pipefail
X() { docker exec -e PYTHONIOENCODING=utf-8 fb-api "$@"; }
X true || { echo "fb-api not running; deploy first" >&2; exit 1; }

fp() { X sh -c 'cat data/redacted/*/*.jsonl | md5sum | cut -c1-32'; }
before="$(fp)"
for t in halcyon brightwater; do
  echo "== redaction $t"
  X python -m backend.redaction.run --tenant "$t" --reset-vault 2>&1 | tail -n 12
done
after="$(fp)"
if [ "$before" = "$after" ]; then
  echo "redacted corpus identical to committed: yes ($after)"
else
  echo "redacted corpus identical to committed: NO (before $before, after $after)" >&2
  exit 1
fi

echo "== seed (offline)"
X python -m backend.ingest.seed --tenant halcyon --tenant brightwater --drop --offline 2>&1 | tail -n 40

echo "== graphs"
P="$(grep '^FB_FALKORDB_PASSWORD=' /opt/financebrain/.env | cut -d= -f2-)"
docker exec -e REDISCLI_AUTH="$P" fb-falkordb redis-cli GRAPH.LIST
for g in brain__halcyon brain__brightwater; do
  echo "$g nodes: $(docker exec -e REDISCLI_AUTH="$P" fb-falkordb redis-cli GRAPH.RO_QUERY "$g" 'MATCH (n) RETURN count(n)' | sed -n 2p)"
done
unset P
