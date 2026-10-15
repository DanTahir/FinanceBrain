#!/usr/bin/env bash
# CloudFront distribution in front of the EC2 origin (HTTP only, secret origin header).
# Default behavior caches static assets; /api/* and /mcp* are uncached and allow all methods.
source "$(dirname "$0")/lib.sh"
trap cleanup_tmp EXIT

ORIGIN_DNS="$(state_get ORIGIN_DNS)"
nonempty "$ORIGIN_DNS" || die "ORIGIN_DNS missing; run infra/10_base.sh first"

DIST_ID="$(awsx cloudfront list-distributions --query "DistributionList.Items[?Comment=='$CF_COMMENT'].Id | [0]" --output text)"
if nonempty "$DIST_ID"; then
  CUR_ORIGIN="$(awsx cloudfront get-distribution --id "$DIST_ID" --query 'Distribution.DistributionConfig.Origins.Items[0].DomainName' --output text)"
  [ "$CUR_ORIGIN" = "$ORIGIN_DNS" ] || log "WARNING: distribution origin $CUR_ORIGIN differs from $ORIGIN_DNS (update manually)"
  log "distribution exists $DIST_ID"
else
  ORIGIN_SECRET="$(ssm_get_secret origin_secret)"
  API_BEHAVIOR() {
    cat <<EOF
{"PathPattern":"$1","TargetOriginId":"ec2-origin","ViewerProtocolPolicy":"redirect-to-https",
 "AllowedMethods":{"Quantity":7,"Items":["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"],
   "CachedMethods":{"Quantity":2,"Items":["GET","HEAD"]}},
 "Compress":false,"CachePolicyId":"$CF_CACHING_DISABLED","OriginRequestPolicyId":"$CF_ORP_ALL_VIEWER_EXCEPT_HOST",
 "SmoothStreaming":false,"FieldLevelEncryptionId":""}
EOF
  }
  CFG="$(tmpjson cfconfig <<EOF
{
 "CallerReference":"financebrain-$(date +%s)",
 "Comment":"$CF_COMMENT",
 "Enabled":true,
 "HttpVersion":"http2and3",
 "IsIPV6Enabled":true,
 "PriceClass":"PriceClass_100",
 "DefaultRootObject":"",
 "Origins":{"Quantity":1,"Items":[{
   "Id":"ec2-origin","DomainName":"$ORIGIN_DNS","OriginPath":"",
   "CustomHeaders":{"Quantity":1,"Items":[{"HeaderName":"X-Origin-Secret","HeaderValue":"$ORIGIN_SECRET"}]},
   "CustomOriginConfig":{"HTTPPort":80,"HTTPSPort":443,"OriginProtocolPolicy":"http-only",
     "OriginSslProtocols":{"Quantity":1,"Items":["TLSv1.2"]},"OriginReadTimeout":60,"OriginKeepaliveTimeout":60},
   "ConnectionAttempts":3,"ConnectionTimeout":10}]},
 "DefaultCacheBehavior":{"TargetOriginId":"ec2-origin","ViewerProtocolPolicy":"redirect-to-https",
   "AllowedMethods":{"Quantity":2,"Items":["GET","HEAD"],"CachedMethods":{"Quantity":2,"Items":["GET","HEAD"]}},
   "Compress":true,"CachePolicyId":"$CF_CACHING_OPTIMIZED","SmoothStreaming":false,"FieldLevelEncryptionId":""},
 "CacheBehaviors":{"Quantity":2,"Items":[$(API_BEHAVIOR '/api/*'),$(API_BEHAVIOR '/mcp*')]}
}
EOF
)"
  unset ORIGIN_SECRET
  DIST_ID="$(awsx cloudfront create-distribution --distribution-config "$CFG" --query Distribution.Id --output text)"
  log "distribution created $DIST_ID"
  awsx cloudfront tag-resource --resource "arn:aws:cloudfront::$ACCOUNT_ID:distribution/$DIST_ID" \
    --tags "Items=[{Key=$TAG_KEY,Value=$TAG_VAL}]" || true
fi

CF_DOMAIN="$(awsx cloudfront get-distribution --id "$DIST_ID" --query Distribution.DomainName --output text)"
state_set CF_DIST_ID "$DIST_ID"
state_set CF_DOMAIN "$CF_DOMAIN"
log "cloudfront https://$CF_DOMAIN (status: $(awsx cloudfront get-distribution --id "$DIST_ID" --query Distribution.Status --output text))"
if [ "${WAIT:-1}" = 1 ]; then
  log "waiting for distribution to deploy (can take several minutes)"
  awsx cloudfront wait distribution-deployed --id "$DIST_ID"
  log "distribution deployed"
fi
