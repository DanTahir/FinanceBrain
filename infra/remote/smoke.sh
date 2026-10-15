#!/bin/bash
# Runs ON the EC2 instance via SSM. Reads the OpenRouter key from SSM using the instance role and
# reports only HTTP status codes and embedding dimensions. Never prints the key.
set -uo pipefail
REGION=us-east-1
KEY=$(aws ssm get-parameter --region "$REGION" --name /financebrain/openrouter_api_key --with-decryption \
  --query Parameter.Value --output text) || { echo "ssm read FAILED"; exit 1; }
echo "ssm read ok (key length ${#KEY})"
umask 077
H=$(mktemp)
printf 'Authorization: Bearer %s\nContent-Type: application/json\nX-Title: FinanceBrain\n' "$KEY" > "$H"
unset KEY
OUT=$(mktemp)
fail=0
for m in anthropic/claude-opus-5.5 anthropic/claude-sonnet-5.5 anthropic/claude-haiku-5.5; do
  code=$(curl -s -o "$OUT" -w '%{http_code}' --max-time 90 -H @"$H" https://openrouter.ai/api/v1/chat/completions \
    -d "{\"model\":\"$m\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word ok.\"}]}")
  echo "chat $m http=$code"
  [ "$code" = 200 ] || fail=1
done
code=$(curl -s -o "$OUT" -w '%{http_code}' --max-time 60 -H @"$H" https://openrouter.ai/api/v1/embeddings \
  -d '{"model":"voyageai/voyage-4","input":["FinanceBrain smoke test"],"dimensions":1024}')
dims=$(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["data"][0]["embedding"]))' "$OUT" 2>/dev/null || echo '?')
echo "embed voyageai/voyage-4 http=$code dims=$dims"
[ "$code" = 200 ] && [ "$dims" = 1024 ] || fail=1
rm -f "$H" "$OUT"
echo "--- containers"
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
echo "--- local origin without secret header (expect 403)"
curl -s -o /dev/null -w 'http=%{http_code}\n' http://localhost/ || true
echo "smoke result: $([ $fail = 0 ] && echo PASS || echo FAIL)"
exit $fail
