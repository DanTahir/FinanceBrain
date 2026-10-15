#!/usr/bin/env bash
# Runs ON the EC2 instance as root via SSM Run Command (see infra/30_deploy.sh).
# Usage: deploy.sh <tag> <ecr_registry> <region> <backup_bucket>
# Expects /opt/financebrain/docker-compose.yml to have been written just before. Never prints secrets.
set -euo pipefail
TAG="$1"; REGISTRY="$2"; REGION="$3"; BUCKET="$4"
D=/opt/financebrain
cd "$D"
[ -f docker-compose.yml ] || { echo "compose file missing" >&2; exit 1; }

mkdir -p "$D/falkordb-data" "$D/logs"
chown 1000:1000 "$D/logs"

# Redis/FalkorDB fork for AOF rewrite and BGSAVE; without overcommit a fork can fail (startup warning).
echo 'vm.overcommit_memory = 1' > /etc/sysctl.d/99-financebrain.conf
sysctl -q -w vm.overcommit_memory=1

# --- secrets from SSM Parameter Store -> .env (mode 600, never echoed) -------------------------------
get() { aws ssm get-parameter --region "$REGION" --name "/financebrain/$1" --with-decryption \
          --query Parameter.Value --output text; }
umask 077
{
  echo "TAG=$TAG"
  echo "ECR_REGISTRY=$REGISTRY"
  echo "OPENROUTER_API_KEY=$(get openrouter_api_key)"
  echo "FB_FALKORDB_PASSWORD=$(get falkordb_password)"
  echo "FB_ORIGIN_SECRET=$(get origin_secret)"
  echo "FB_DEMO_MCP_TOKEN=$(get demo_mcp_token)"
  echo "FB_PII_HMAC_KEY=$(get pii_hmac_key)"
  echo "FB_SESSION_SECRET=$(get session_secret)"
} > "$D/.env.tmp"
if grep -qE '=(None)?$' "$D/.env.tmp"; then echo "a required SSM parameter is empty" >&2; rm -f "$D/.env.tmp"; exit 1; fi
mv "$D/.env.tmp" "$D/.env"
umask 022
echo "env file written ($(wc -l < "$D/.env") entries)"

# --- images ------------------------------------------------------------------------------------------
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
docker compose pull -q
echo "pulled images for tag $TAG"

# The skeleton placeholder holds port 80; the real stack replaces it.
if docker ps -a --format '{{.Names}}' | grep -qx fb-hello; then
  docker rm -f fb-hello >/dev/null && echo "removed placeholder fb-hello"
fi

docker compose up -d --remove-orphans

status=none
for i in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' fb-api 2>/dev/null || echo none)"
  [ "$status" = healthy ] && break
  sleep 5
done
echo "api health: $status"
docker compose ps --format 'table {{.Name}}\t{{.Status}}'

OS="$(grep '^FB_ORIGIN_SECRET=' "$D/.env" | cut -d= -f2-)"
echo "local /healthz -> $(curl -s -o /dev/null -w '%{http_code}' -H "X-Origin-Secret: $OS" http://127.0.0.1/healthz)"
echo "local /api/healthz -> $(curl -s -H "X-Origin-Secret: $OS" http://127.0.0.1/api/healthz | head -c 300)"
echo "local / without secret -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/)"
unset OS

# --- nightly FalkorDB backup to S3 (systemd timer; AL2023 has no cron by default) -------------------
cat > /usr/local/bin/fb-backup.sh <<EOF
#!/usr/bin/env bash
set -euo pipefail
P="\$(grep '^FB_FALKORDB_PASSWORD=' $D/.env | cut -d= -f2-)"
cli() { docker exec -e REDISCLI_AUTH="\$P" fb-falkordb redis-cli "\$@"; }
before="\$(cli LASTSAVE)"
cli BGSAVE >/dev/null
for i in \$(seq 1 150); do [ "\$(cli LASTSAVE)" != "\$before" ] && break; sleep 2; done
aws s3 cp $D/falkordb-data/dump.rdb "s3://$BUCKET/falkordb/\$(date -u +%Y-%m-%dT%H%MZ).rdb" --only-show-errors --region $REGION
echo "backup uploaded"
EOF
chmod 700 /usr/local/bin/fb-backup.sh
cat > /etc/systemd/system/fb-backup.service <<'EOF'
[Unit]
Description=FinanceBrain FalkorDB backup to S3
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/fb-backup.sh
EOF
cat > /etc/systemd/system/fb-backup.timer <<'EOF'
[Unit]
Description=Nightly FinanceBrain FalkorDB backup
[Timer]
OnCalendar=*-*-* 10:00:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now fb-backup.timer >/dev/null 2>&1
echo "backup timer: $(systemctl is-active fb-backup.timer)"

docker image prune -f >/dev/null 2>&1 || true
[ "$status" = healthy ]
