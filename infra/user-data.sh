#!/bin/bash
# EC2 first-boot: Docker + Compose, then a placeholder Caddy origin that only answers
# requests carrying the CloudFront origin secret header. Replaced by the real stack on first deploy.
# No `set -x`: commands below handle secrets.
set -euo pipefail
exec > >(tee -a /var/log/financebrain-userdata.log) 2>&1
echo "user-data start $(date -Is)"

dnf install -y -q docker jq
systemctl enable --now docker
usermod -aG docker ec2-user

mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version

mkdir -p /opt/financebrain
IMDS_TOKEN=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
REGION=$(curl -fsS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/placement/region)

for i in $(seq 1 30); do
  if ORIGIN_SECRET=$(aws ssm get-parameter --region "$REGION" --name /financebrain/origin_secret \
      --with-decryption --query Parameter.Value --output text 2>/dev/null); then
    break
  fi
  sleep 5
done

umask 077
printf 'ORIGIN_SECRET=%s\n' "$ORIGIN_SECRET" > /opt/financebrain/hello.env
umask 022
cat > /opt/financebrain/Caddyfile.hello <<'EOF'
{
	admin off
}
:80 {
	@trusted header X-Origin-Secret {$ORIGIN_SECRET}
	handle @trusted {
		respond /healthz "ok" 200
		respond "FinanceBrain origin is up. App deploy pending." 200
	}
	respond "forbidden" 403
}
EOF

docker rm -f fb-hello >/dev/null 2>&1 || true
docker run -d --name fb-hello --restart unless-stopped -p 80:80 \
  --env-file /opt/financebrain/hello.env \
  -v /opt/financebrain/Caddyfile.hello:/etc/caddy/Caddyfile:ro caddy:2

echo "user-data done $(date -Is)"
