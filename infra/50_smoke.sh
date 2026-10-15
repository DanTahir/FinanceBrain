#!/usr/bin/env bash
# Instance smoke test via SSM Run Command, then end-to-end checks through CloudFront.
source "$(dirname "$0")/lib.sh"
trap cleanup_tmp EXIT

INSTANCE_ID="$(state_get INSTANCE_ID)"
nonempty "$INSTANCE_ID" || die "INSTANCE_ID missing; run infra/10_base.sh"

log "waiting for SSM agent on $INSTANCE_ID"
for i in $(seq 1 40); do
  ping="$(awsx ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text)"
  [ "$ping" = "Online" ] && break
  sleep 10
done
[ "$ping" = "Online" ] || die "SSM agent not online"

B64="$(base64 -w0 infra/remote/smoke.sh)"
PARAMS="$(tmpjson smokeparams <<EOF
{"commands":["cloud-init status --wait >/dev/null 2>&1 || true","echo $B64 | base64 -d > /tmp/fb-smoke.sh","bash /tmp/fb-smoke.sh"],"executionTimeout":["900"]}
EOF
)"
CMD_ID="$(awsx ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --comment 'financebrain smoke' --parameters "$PARAMS" --query Command.CommandId --output text)"
log "ssm command $CMD_ID"
for i in $(seq 1 90); do
  st="$(awsx ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)"
  case "$st" in Success|Failed|Cancelled|TimedOut) break ;; esac
  sleep 10
done
awsx ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text
ERR="$(awsx ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text)"
[ -n "$ERR" ] && printf 'stderr:\n%s\n' "$ERR" | tail -20
log "instance smoke status: $st"

CF_DOMAIN="$(state_get CF_DOMAIN)"
if nonempty "$CF_DOMAIN"; then
  log "CloudFront https://$CF_DOMAIN/healthz -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$CF_DOMAIN/healthz")"
  log "CloudFront body: $(curl -s --max-time 20 "https://$CF_DOMAIN/" | head -c 200)"
fi
PUBLIC_IP="$(state_get PUBLIC_IP)"
if nonempty "$PUBLIC_IP"; then
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://$PUBLIC_IP/" || true)"
  log "direct origin http://<eip>/ -> ${code:-000} (expect 000 = blocked by security group)"
fi
[ "$st" = "Success" ]
