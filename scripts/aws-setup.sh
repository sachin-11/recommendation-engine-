#!/usr/bin/env bash
# Create the AWS resources for a single-host RecoEngine deployment.
#
#   AWS_REGION=us-east-1 KEY_NAME=recoengine ./scripts/aws-setup.sh [--with-rds] [--with-redis]
#
# Creates (idempotently where AWS allows):
#   - ECR repositories recoengine-backend and recoengine-dashboard
#   - an SSH key pair (saved to ./<KEY_NAME>.pem) unless it already exists
#   - a security group allowing 80, 443 and 22 (22 only from SSH_CIDR)
#   - an EC2 instance (Ubuntu 24.04, Docker installed by user data) with an Elastic IP
#   - optionally an RDS PostgreSQL db.t3.micro and an ElastiCache Redis cache.t3.micro,
#     reachable only from the instance's security group
# and prints every resource id and connection string at the end.
#
# Requires: aws CLI v2 configured with rights for EC2, ECR (and RDS / ElastiCache).
# Costs money: t3.small/medium + EIP (+ RDS/ElastiCache) are billed while they exist.
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT="${PROJECT:-recoengine}"
KEY_NAME="${KEY_NAME:-recoengine}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.medium}"   # t3.small is enough for an MVP
VOLUME_GB="${VOLUME_GB:-30}"
SSH_CIDR="${SSH_CIDR:-0.0.0.0/0}"             # restrict to your IP, e.g. 203.0.113.7/32
WITH_RDS=false
WITH_REDIS=false

for arg in "$@"; do
  case "$arg" in
    --with-rds) WITH_RDS=true ;;
    --with-redis) WITH_REDIS=true ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

export AWS_DEFAULT_REGION="$AWS_REGION"
aws() { command aws --region "$AWS_REGION" --output text "$@"; }
log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
tag_spec() { echo "ResourceType=$1,Tags=[{Key=Name,Value=$PROJECT},{Key=Project,Value=$PROJECT}]"; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account)"
ECR_REGISTRY="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# ---------------------------------------------------------------- ECR
log "ECR repositories"
for repo in "$PROJECT-backend" "$PROJECT-dashboard"; do
  if aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1; then
    echo "exists: $repo"
  else
    aws ecr create-repository --repository-name "$repo" \
      --image-scanning-configuration scanOnPush=true --image-tag-mutability MUTABLE >/dev/null
    # Keep the last 30 images.
    aws ecr put-lifecycle-policy --repository-name "$repo" --lifecycle-policy-text \
      '{"rules":[{"rulePriority":1,"description":"keep 30","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":30},"action":{"type":"expire"}}]}' >/dev/null
    echo "created: $repo"
  fi
done

# ---------------------------------------------------------------- key pair
log "Key pair $KEY_NAME"
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  echo "exists (use your existing $KEY_NAME.pem)"
else
  aws ec2 create-key-pair --key-name "$KEY_NAME" --key-type ed25519 \
    --query KeyMaterial > "$KEY_NAME.pem"
  chmod 600 "$KEY_NAME.pem"
  echo "saved ./$KEY_NAME.pem — keep it safe"
fi

# ---------------------------------------------------------------- network
log "Security group"
VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId')"
SG_ID="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$PROJECT-web" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId')"
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID="$(aws ec2 create-security-group --group-name "$PROJECT-web" --vpc-id "$VPC_ID" \
    --description "RecoEngine web host" --query GroupId)"
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 22 --cidr "$SSH_CIDR" >/dev/null
  echo "created: $SG_ID (80, 443 open; 22 from $SSH_CIDR)"
else
  echo "exists: $SG_ID"
fi

# ---------------------------------------------------------------- IAM role (ECR pull from the host)
log "Instance role"
ROLE="$PROJECT-ec2"
if ! command aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  command aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  command aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
  command aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  command aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  echo "created: $ROLE (waiting for IAM to propagate)"; sleep 15
else
  echo "exists: $ROLE"
fi

# ---------------------------------------------------------------- EC2
log "EC2 instance ($INSTANCE_TYPE)"
INSTANCE_ID="$(aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=$PROJECT" "Name=instance-state-name,Values=pending,running,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId')"
if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  AMI_ID="$(aws ssm get-parameter \
    --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --query Parameter.Value)"
  USER_DATA="$(cat <<'EOF'
#!/bin/bash
set -eux
apt-get update
apt-get install -y ca-certificates curl git unzip jq
curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install
apt-get install -y amazon-ecr-credential-helper || true
EOF
)"
  INSTANCE_ID="$(aws ec2 run-instances --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
    --iam-instance-profile "Name=$ROLE" \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$VOLUME_GB,VolumeType=gp3,Encrypted=true}" \
    --metadata-options HttpTokens=required \
    --user-data "$USER_DATA" \
    --tag-specifications "$(tag_spec instance)" "$(tag_spec volume)" \
    --query 'Instances[0].InstanceId')"
  echo "launched: $INSTANCE_ID"
else
  echo "exists: $INSTANCE_ID"
fi
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

log "Elastic IP"
ALLOCATION_ID="$(aws ec2 describe-addresses --filters "Name=tag:Project,Values=$PROJECT" \
  --query 'Addresses[0].AllocationId')"
if [ "$ALLOCATION_ID" = "None" ] || [ -z "$ALLOCATION_ID" ]; then
  ALLOCATION_ID="$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "$(tag_spec elastic-ip)" --query AllocationId)"
fi
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$ALLOCATION_ID" >/dev/null
PUBLIC_IP="$(aws ec2 describe-addresses --allocation-ids "$ALLOCATION_ID" --query 'Addresses[0].PublicIp')"
echo "$PUBLIC_IP"

# ---------------------------------------------------------------- data stores (optional)
DB_SG_ID=""
data_security_group() {
  if [ -n "$DB_SG_ID" ]; then return; fi
  DB_SG_ID="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$PROJECT-data" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId')"
  if [ "$DB_SG_ID" = "None" ] || [ -z "$DB_SG_ID" ]; then
    DB_SG_ID="$(aws ec2 create-security-group --group-name "$PROJECT-data" --vpc-id "$VPC_ID" \
      --description "RecoEngine data stores" --query GroupId)"
    aws ec2 authorize-security-group-ingress --group-id "$DB_SG_ID" --protocol tcp --port 5432 --source-group "$SG_ID" >/dev/null
    aws ec2 authorize-security-group-ingress --group-id "$DB_SG_ID" --protocol tcp --port 6379 --source-group "$SG_ID" >/dev/null
  fi
}

DATABASE_URL="(bundled postgres container — see .env.production.example)"
if $WITH_RDS; then
  log "RDS PostgreSQL (db.t3.micro)"
  data_security_group
  DB_PASSWORD="$(openssl rand -hex 24)"
  if ! aws rds describe-db-instances --db-instance-identifier "$PROJECT-db" >/dev/null 2>&1; then
    aws rds create-db-instance --db-instance-identifier "$PROJECT-db" \
      --engine postgres --engine-version 15 --db-instance-class db.t3.micro \
      --allocated-storage 20 --storage-type gp3 --storage-encrypted \
      --master-username reco --master-user-password "$DB_PASSWORD" --db-name recommendation_engine \
      --vpc-security-group-ids "$DB_SG_ID" --no-publicly-accessible \
      --backup-retention-period 7 --tags "Key=Project,Value=$PROJECT" >/dev/null
    echo "creating (takes ~5-10 minutes)…"
    aws rds wait db-instance-available --db-instance-identifier "$PROJECT-db"
  else
    DB_PASSWORD="<existing password>"
  fi
  DB_HOST="$(aws rds describe-db-instances --db-instance-identifier "$PROJECT-db" --query 'DBInstances[0].Endpoint.Address')"
  DATABASE_URL="postgresql+asyncpg://reco:$DB_PASSWORD@$DB_HOST:5432/recommendation_engine"
fi

REDIS_URL="redis://redis:6379/0 (bundled redis container)"
if $WITH_REDIS; then
  log "ElastiCache Redis (cache.t3.micro)"
  data_security_group
  if ! aws elasticache describe-cache-clusters --cache-cluster-id "$PROJECT-redis" >/dev/null 2>&1; then
    aws elasticache create-cache-cluster --cache-cluster-id "$PROJECT-redis" \
      --engine redis --cache-node-type cache.t3.micro --num-cache-nodes 1 \
      --security-group-ids "$DB_SG_ID" --tags "Key=Project,Value=$PROJECT" >/dev/null
    echo "creating (takes ~5-10 minutes)…"
    aws elasticache wait cache-cluster-available --cache-cluster-id "$PROJECT-redis"
  fi
  REDIS_HOST="$(aws elasticache describe-cache-clusters --cache-cluster-id "$PROJECT-redis" \
    --show-cache-node-info --query 'CacheClusters[0].CacheNodes[0].Endpoint.Address')"
  REDIS_URL="redis://$REDIS_HOST:6379/0"
fi

# ---------------------------------------------------------------- summary
cat <<EOF

================================================================ RecoEngine on AWS
Region              $AWS_REGION
ECR registry        $ECR_REGISTRY
  repositories      $PROJECT-backend, $PROJECT-dashboard
EC2 instance        $INSTANCE_ID ($INSTANCE_TYPE)
Security group      $SG_ID
Elastic IP          $PUBLIC_IP   (allocation $ALLOCATION_ID)
SSH                 ssh -i $KEY_NAME.pem ubuntu@$PUBLIC_IP
DATABASE_URL        $DATABASE_URL
REDIS_URL           $REDIS_URL

Next:
  1. ssh in, clone the repo, create .env.production (ECR_REGISTRY=$ECR_REGISTRY)
  2. DEPLOY_HOST=$PUBLIC_IP ECR_REGISTRY=$ECR_REGISTRY ./scripts/deploy.sh
EOF
