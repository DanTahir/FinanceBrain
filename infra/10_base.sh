#!/usr/bin/env bash
# Base resources: KMS, S3, SSM parameters, IAM role/profile, ECR, security group, EC2 + Elastic IP.
# Idempotent. Never echoes secret values.
source "$(dirname "$0")/lib.sh"
trap cleanup_tmp EXIT

# ---------- KMS (PII vault envelope key) ----------
KEY_ID="$(awsx kms list-aliases --query "Aliases[?AliasName=='$KMS_ALIAS'].TargetKeyId | [0]" --output text)"
if ! nonempty "$KEY_ID"; then
  KEY_ID="$(awsx kms create-key --description 'FinanceBrain PII token vault' \
    --tags "TagKey=$TAG_KEY,TagValue=$TAG_VAL" --query KeyMetadata.KeyId --output text)"
  awsx kms create-alias --alias-name "$KMS_ALIAS" --target-key-id "$KEY_ID"
  awsx kms enable-key-rotation --key-id "$KEY_ID"
  log "kms key created $KEY_ID"
else
  log "kms key exists $KEY_ID"
fi
KEY_ARN="arn:aws:kms:$AWS_REGION:$ACCOUNT_ID:key/$KEY_ID"
state_set KMS_KEY_ID "$KEY_ID"

# ---------- S3 backups ----------
if ! awsx s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  awsx s3api create-bucket --bucket "$BUCKET" >/dev/null
  log "bucket created $BUCKET"
else
  log "bucket exists $BUCKET"
fi
awsx s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
awsx s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  "$(tmpjson s3enc <<'EOF'
{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}
EOF
)"
awsx s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
  "$(tmpjson s3life <<'EOF'
{"Rules":[{"ID":"expire-backups","Status":"Enabled","Filter":{"Prefix":"backups/"},"Expiration":{"Days":14}}]}
EOF
)"
awsx s3api put-bucket-tagging --bucket "$BUCKET" --tagging "TagSet=[{Key=$TAG_KEY,Value=$TAG_VAL}]"
state_set BUCKET "$BUCKET"

# ---------- SSM parameters ----------
OR_KEY="$(tr -d '\r\n[:space:]' < Openrouterkey.txt)"
[[ "$OR_KEY" == sk-or-* ]] || die "Openrouterkey.txt does not look like an OpenRouter key"
if awsx ssm get-parameter --name "$SSM_PREFIX/openrouter_api_key" --query Parameter.Name --output text >/dev/null 2>&1; then
  awsx ssm put-parameter --name "$SSM_PREFIX/openrouter_api_key" --type SecureString --overwrite --value "$OR_KEY" >/dev/null
  log "ssm $SSM_PREFIX/openrouter_api_key updated"
else
  awsx ssm put-parameter --name "$SSM_PREFIX/openrouter_api_key" --type SecureString --value "$OR_KEY" \
    --tags "Key=$TAG_KEY,Value=$TAG_VAL" >/dev/null
  log "ssm $SSM_PREFIX/openrouter_api_key created"
fi
unset OR_KEY
for p in falkordb_password origin_secret demo_mcp_token pii_hmac_key session_secret; do
  ssm_ensure_random "$p"
done

# ---------- IAM role + instance profile ----------
if ! awsx iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  awsx iam create-role --role-name "$ROLE_NAME" --tags "Key=$TAG_KEY,Value=$TAG_VAL" \
    --assume-role-policy-document "$(tmpjson trust <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
)" >/dev/null
  log "iam role created $ROLE_NAME"
else
  log "iam role exists $ROLE_NAME"
fi
for p in AmazonSSMManagedInstanceCore AmazonEC2ContainerRegistryReadOnly; do
  awsx iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::aws:policy/$p"
done
awsx iam put-role-policy --role-name "$ROLE_NAME" --policy-name financebrain-app \
  --policy-document "$(tmpjson apppolicy <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"Params","Effect":"Allow","Action":["ssm:GetParameter","ssm:GetParameters","ssm:GetParametersByPath"],
  "Resource":"arn:aws:ssm:$AWS_REGION:$ACCOUNT_ID:parameter$SSM_PREFIX/*"},
 {"Sid":"Vault","Effect":"Allow","Action":["kms:Encrypt","kms:Decrypt","kms:GenerateDataKey","kms:DescribeKey"],
  "Resource":"$KEY_ARN"},
 {"Sid":"Backups","Effect":"Allow","Action":["s3:PutObject","s3:GetObject","s3:ListBucket"],
  "Resource":["arn:aws:s3:::$BUCKET","arn:aws:s3:::$BUCKET/*"]},
 {"Sid":"Logs","Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams"],
  "Resource":"arn:aws:logs:$AWS_REGION:$ACCOUNT_ID:log-group:/financebrain*"}
]}
EOF
)"
log "iam inline policy financebrain-app set (no Bedrock/Marketplace permissions)"

NEW_PROFILE=0
if ! awsx iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  awsx iam create-instance-profile --instance-profile-name "$PROFILE_NAME" --tags "Key=$TAG_KEY,Value=$TAG_VAL" >/dev/null
  NEW_PROFILE=1
fi
ROLES_IN_PROFILE="$(awsx iam get-instance-profile --instance-profile-name "$PROFILE_NAME" --query 'InstanceProfile.Roles[].RoleName' --output text)"
if [[ "$ROLES_IN_PROFILE" != *"$ROLE_NAME"* ]]; then
  awsx iam add-role-to-instance-profile --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME"
  NEW_PROFILE=1
fi
[ "$NEW_PROFILE" = 1 ] && { log "waiting for instance profile propagation"; sleep 15; }

# ---------- ECR ----------
for r in "${ECR_REPOS[@]}"; do
  if ! awsx ecr describe-repositories --repository-names "$r" >/dev/null 2>&1; then
    awsx ecr create-repository --repository-name "$r" --image-scanning-configuration scanOnPush=true \
      --tags "Key=$TAG_KEY,Value=$TAG_VAL" >/dev/null
    log "ecr repo created $r"
  else
    log "ecr repo exists $r"
  fi
  awsx ecr put-lifecycle-policy --repository-name "$r" --lifecycle-policy-text "$(tmpjson ecrlife <<'EOF'
{"rules":[{"rulePriority":1,"description":"keep last 10","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},"action":{"type":"expire"}}]}
EOF
)" >/dev/null
done

# ---------- Security group: inbound 80 from CloudFront origin-facing prefix list only ----------
VPC_ID="$(awsx ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
nonempty "$VPC_ID" || die "no default VPC"
SG_ID="$(awsx ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if ! nonempty "$SG_ID"; then
  SG_ID="$(awsx ec2 create-security-group --group-name "$SG_NAME" --vpc-id "$VPC_ID" \
    --description 'FinanceBrain origin: HTTP from CloudFront only' \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=$TAG_KEY,Value=$TAG_VAL},{Key=Name,Value=$SG_NAME}]" \
    --query GroupId --output text)"
  log "security group created $SG_ID"
fi
PL_ID="$(awsx ec2 describe-managed-prefix-lists --filters "Name=prefix-list-name,Values=$CF_PREFIX_LIST_NAME" \
  --query 'PrefixLists[0].PrefixListId' --output text)"
if ! awsx ec2 authorize-security-group-ingress --group-id "$SG_ID" \
    --ip-permissions "IpProtocol=tcp,FromPort=80,ToPort=80,PrefixListIds=[{PrefixListId=$PL_ID,Description=CloudFront-origin-facing}]" \
    >/dev/null 2>"$TMP_DIR/sg.err"; then
  grep -q 'InvalidPermission.Duplicate' "$TMP_DIR/sg.err" || { cat "$TMP_DIR/sg.err" >&2; die "SG ingress failed"; }
fi
log "security group $SG_ID allows tcp/80 from $PL_ID only"
state_set SG_ID "$SG_ID"
state_set VPC_ID "$VPC_ID"

# ---------- EC2 instance ----------
INSTANCE_ID="$(awsx ec2 describe-instances --filters "Name=tag:Name,Values=$INSTANCE_NAME" "Name=tag:$TAG_KEY,Values=$TAG_VAL" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" --query 'Reservations[0].Instances[0].InstanceId' --output text)"
if ! nonempty "$INSTANCE_ID"; then
  AMI_ID="$(awsx ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text)"
  SUBNET_ID="$(awsx ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" "Name=availability-zone,Values=$INSTANCE_AZ" \
    Name=default-for-az,Values=true --query 'Subnets[0].SubnetId' --output text)"
  log "launching $INSTANCE_TYPE ami=$AMI_ID subnet=$SUBNET_ID"
  for attempt in 1 2 3 4 5 6; do
    if INSTANCE_ID="$(awsx ec2 run-instances --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
        --iam-instance-profile "Name=$PROFILE_NAME" --security-group-ids "$SG_ID" --subnet-id "$SUBNET_ID" \
        --user-data file://infra/user-data.sh \
        --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2 \
        --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=$ROOT_GB,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME},{Key=$TAG_KEY,Value=$TAG_VAL}]" \
                             "ResourceType=volume,Tags=[{Key=Name,Value=$INSTANCE_NAME-root},{Key=$TAG_KEY,Value=$TAG_VAL}]" \
        --query 'Instances[0].InstanceId' --output text 2>"$TMP_DIR/run.err")"; then
      break
    fi
    if grep -q -i 'instance profile' "$TMP_DIR/run.err" && [ "$attempt" -lt 6 ]; then
      log "instance profile not yet visible to EC2, retrying ($attempt)"; sleep 10
    else
      cat "$TMP_DIR/run.err" >&2; die "run-instances failed"
    fi
  done
  log "instance launched $INSTANCE_ID"
else
  log "instance exists $INSTANCE_ID"
fi
awsx ec2 wait instance-running --instance-ids "$INSTANCE_ID"
state_set INSTANCE_ID "$INSTANCE_ID"

# ---------- Elastic IP (stable origin DNS for CloudFront) ----------
ALLOC_ID="$(awsx ec2 describe-addresses --filters "Name=tag:$TAG_KEY,Values=$TAG_VAL" --query 'Addresses[0].AllocationId' --output text)"
if ! nonempty "$ALLOC_ID"; then
  ALLOC_ID="$(awsx ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$INSTANCE_NAME},{Key=$TAG_KEY,Value=$TAG_VAL}]" \
    --query AllocationId --output text)"
  log "eip allocated $ALLOC_ID"
fi
ASSOC_INSTANCE="$(awsx ec2 describe-addresses --allocation-ids "$ALLOC_ID" --query 'Addresses[0].InstanceId' --output text)"
if [ "$ASSOC_INSTANCE" != "$INSTANCE_ID" ]; then
  awsx ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID" >/dev/null
  log "eip associated with $INSTANCE_ID"
  sleep 5
fi
PUBLIC_IP="$(awsx ec2 describe-addresses --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp' --output text)"
PUBLIC_DNS="$(awsx ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].PublicDnsName' --output text)"
nonempty "$PUBLIC_DNS" || PUBLIC_DNS="ec2-${PUBLIC_IP//./-}.compute-1.amazonaws.com"
state_set EIP_ALLOC_ID "$ALLOC_ID"
state_set PUBLIC_IP "$PUBLIC_IP"
state_set ORIGIN_DNS "$PUBLIC_DNS"

log "base OK: instance=$INSTANCE_ID origin=$PUBLIC_DNS"
