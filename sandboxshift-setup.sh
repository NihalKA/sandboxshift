#!/usr/bin/env bash
# sandboxshift-setup.sh — One-command AWS infrastructure bootstrap for SandboxShift.
#
# What this script does:
#   1.  Detects your AWS account ID and region automatically
#   2.  Creates an S3 bucket for Terraform remote state
#   3.  Creates a DynamoDB table for Terraform state locking
#   4.  Creates ECR repositories for all three runtime images
#   5.  Builds and pushes runtime images to ECR (python, node, multi)
#   6.  Patches terraform/fargate/backend.tf
#   7.  Runs terraform init + terraform apply (wires ECR image into task definition)
#   8.  Writes all FARGATE_* env vars to ~/.sandboxshift/fargate.env
#       (auto-loaded by the CLI — no manual source needed)
#
# Usage:
#   chmod +x sandboxshift-setup.sh
#   ./sandboxshift-setup.sh
#
# Optional env var overrides:
#   AWS_REGION           Override region (default: auto-detected from AWS config)
#   SANDBOXSHIFT_ENV     Deployment tag (default: dev)
#
# Requirements:
#   - aws CLI configured (aws configure or AWS_PROFILE)
#   - terraform >= 1.5
#   - podman  (brew install podman)
#   - skopeo  (brew install skopeo)  — used for reliable cross-platform image push
#   - jq      (brew install jq)

set -euo pipefail

# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${BLUE}[sandboxshift]${NC} $*"; }
ok()    { echo -e "${GREEN}[sandboxshift]${NC} \u2713 $*"; }
warn()  { echo -e "${YELLOW}[sandboxshift]${NC} \u26a0 $*"; }
error() { echo -e "${RED}[sandboxshift]${NC} \u2717 $*" >&2; exit 1; }

check_dependency() {
  command -v "$1" &>/dev/null || error "'$1' is required but not installed. $2"
}

ensure_ecr_repo() {
  local repo_name="$1"
  if aws ecr describe-repositories \
      --repository-names "${repo_name}" \
      --region "${REGION}" &>/dev/null; then
    ok "ECR repo already exists: ${repo_name}"
  else
    info "Creating ECR repo: ${repo_name}"
    aws ecr create-repository \
      --repository-name "${repo_name}" \
      --region "${REGION}" \
      --image-scanning-configuration scanOnPush=true \
      --output text > /dev/null
    ok "Created ECR repo: ${repo_name}"
  fi
}

build_and_push() {
  local image_dir="$1"   # e.g. images/python
  local local_tag="$2"   # e.g. sandboxshift/runtime-python:3.11
  local ecr_tag="$3"     # e.g. 1234.dkr.ecr.us-east-1.amazonaws.com/sandboxshift/runtime-python:3.11

  info "Building ${local_tag} (linux/amd64, --pull=always)..."
  # --pull=always  — force-pull the AMD64 base; never reuse cached ARM64 layers.
  # --platform     — cross-compile to AMD64 on Apple Silicon.
  podman build \
    --platform linux/amd64 \
    --pull=always \
    -t "${local_tag}" \
    "${SCRIPT_DIR}/${image_dir}"

  # Push via: podman save  →  temp tar on macOS  →  skopeo push to ECR
  #
  # WHY NOT podman push:
  #   podman push routes traffic through the QEMU Linux VM's TCP stack.
  #   That VM has TCP Segmentation Offload (TSO) enabled, which assembles
  #   packets larger than the real macOS → AWS path can carry, causing
  #   "write: broken pipe" on large blob uploads, regardless of MTU tuning.
  #
  # WHY NOT skopeo containers-storage:
  #   On macOS, podman stores images inside the QEMU VM, not in the local
  #   macOS containers storage. containers-storage: looks at the local store
  #   and finds nothing — "does not resolve to an image ID".
  #
  # THE FIX — two steps:
  #   1. podman save  — runs as a podman client call; the image is streamed
  #      out of the QEMU VM and written to a temp tar file on the macOS
  #      filesystem. This uses the Podman REST socket, not raw TCP.
  #   2. skopeo copy docker-archive:  — reads the local tar file and uploads
  #      to ECR using macOS's own TCP stack. No VM involved. Reliable.
  #
  # Auth: podman login already wrote ~/.config/containers/auth.json above.
  #       skopeo reads the same file automatically.
  local tmp_tar
  tmp_tar=$(mktemp -t "sandboxshift-image-XXXX.tar")
  # shellcheck disable=SC2064
  trap "rm -f '${tmp_tar}'" RETURN

  info "Exporting ${local_tag} from Podman VM..."
  podman save -o "${tmp_tar}" "${local_tag}"

  info "Pushing ${ecr_tag} (via skopeo, native macOS TCP)..."
  skopeo copy \
    --override-os  linux \
    --override-arch amd64 \
    "docker-archive:${tmp_tar}" \
    "docker://${ecr_tag}"

  ok "Pushed ${ecr_tag}"
}

# ───────────────────────────────────────────────────────────────────────────────
# Preflight checks
# ───────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BLUE}\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588${NC}"
echo -e "${BLUE}  SandboxShift \u2014 AWS Infrastructure Setup${NC}"
echo -e "${BLUE}\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588${NC}"
echo ""

check_dependency aws       "Install with: brew install awscli"
check_dependency terraform "Install with: brew install terraform"
check_dependency podman    "Install with: brew install podman && podman machine init && podman machine start"
check_dependency skopeo    "Install with: brew install skopeo"
check_dependency jq        "Install with: brew install jq"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/terraform/fargate"
[[ -f "${TF_DIR}/main.tf" ]] || error "Run this script from the sandboxshift repo root."

# ───────────────────────────────────────────────────────────────────────────────
# Detect AWS identity
# ───────────────────────────────────────────────────────────────────────────────

info "Detecting AWS identity..."
IDENTITY=$(aws sts get-caller-identity 2>/dev/null) \
  || error "AWS credentials not configured. Run 'aws configure' first."

ACCOUNT_ID=$(echo "${IDENTITY}" | jq -r '.Account')
CALLER_ARN=$(echo "${IDENTITY}" | jq -r '.Arn')

if [[ -n "${AWS_REGION:-}" ]]; then
  REGION="${AWS_REGION}"
elif [[ -n "${AWS_DEFAULT_REGION:-}" ]]; then
  REGION="${AWS_DEFAULT_REGION}"
else
  REGION=$(aws configure get region 2>/dev/null || true)
  if [[ -z "${REGION}" ]]; then
    echo -n "Enter AWS region (e.g. us-east-1): "
    read -r REGION
    [[ -n "${REGION}" ]] || error "Region is required."
  fi
fi

ENV_TAG="${SANDBOXSHIFT_ENV:-dev}"

# ECR registry hostname is always deterministic — no API call needed.
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

ok "Account:      ${ACCOUNT_ID}"
ok "Region:       ${REGION}"
ok "ECR registry: ${ECR_REGISTRY}"
ok "Caller:       ${CALLER_ARN}"
echo ""

# ───────────────────────────────────────────────────────────────────────────────
# Persistent setup state
# ───────────────────────────────────────────────────────────────────────────────

SETUP_ENV="${HOME}/.sandboxshift/setup.env"
mkdir -p "${HOME}/.sandboxshift"

if [[ -f "${SETUP_ENV}" ]]; then
  # shellcheck disable=SC1090
  source "${SETUP_ENV}"
  info "Reusing existing setup: ${SETUP_ENV}"
else
  RAND_HASH=$(openssl rand -hex 3)
  STATE_BUCKET="sandboxshift-tfstate-${ACCOUNT_ID}-${RAND_HASH}"
  LOCK_TABLE="sandboxshift-tfstate-lock-${RAND_HASH}"

  cat > "${SETUP_ENV}" <<EOF
# SandboxShift setup — auto-generated by sandboxshift-setup.sh
# Do not edit manually.
RAND_HASH=${RAND_HASH}
STATE_BUCKET=${STATE_BUCKET}
LOCK_TABLE=${LOCK_TABLE}
ACCOUNT_ID=${ACCOUNT_ID}
REGION=${REGION}
EOF
  ok "Generated unique names (saved to ${SETUP_ENV})"
fi

info "State bucket: ${STATE_BUCKET}"
info "Lock table:   ${LOCK_TABLE}"
info "Workspace bucket: auto-generated by Terraform"
echo ""

# ───────────────────────────────────────────────────────────────────────────────
# Step 1: S3 Terraform state bucket
# ───────────────────────────────────────────────────────────────────────────────

if aws s3api head-bucket --bucket "${STATE_BUCKET}" --region "${REGION}" 2>/dev/null; then
  ok "State bucket already exists: ${STATE_BUCKET}"
else
  info "Creating state bucket: ${STATE_BUCKET}"
  if [[ "${REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket \
      --bucket "${STATE_BUCKET}" \
      --region "${REGION}" \
      --output text > /dev/null
  else
    aws s3api create-bucket \
      --bucket "${STATE_BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration LocationConstraint="${REGION}" \
      --output text > /dev/null
  fi
  aws s3api put-bucket-versioning \
    --bucket "${STATE_BUCKET}" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption \
    --bucket "${STATE_BUCKET}" \
    --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-public-access-block \
    --bucket "${STATE_BUCKET}" \
    --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  ok "State bucket created and secured."
fi

# ───────────────────────────────────────────────────────────────────────────────
# Step 2: DynamoDB lock table
# ───────────────────────────────────────────────────────────────────────────────

LOCK_STATUS=$(aws dynamodb describe-table \
  --table-name "${LOCK_TABLE}" \
  --region "${REGION}" \
  --query 'Table.TableStatus' \
  --output text 2>/dev/null || echo "NOT_FOUND")

if [[ "${LOCK_STATUS}" != "NOT_FOUND" ]]; then
  ok "DynamoDB lock table already exists: ${LOCK_TABLE}"
else
  info "Creating DynamoDB lock table: ${LOCK_TABLE}"
  aws dynamodb create-table \
    --table-name "${LOCK_TABLE}" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "${REGION}" \
    --output text > /dev/null
  info "Waiting for table to become active..."
  aws dynamodb wait table-exists \
    --table-name "${LOCK_TABLE}" \
    --region "${REGION}"
  ok "DynamoDB lock table created."
fi

# ───────────────────────────────────────────────────────────────────────────────
# Step 3: ECR repositories
# ───────────────────────────────────────────────────────────────────────────────

info "Setting up ECR repositories..."
ensure_ecr_repo "sandboxshift/runtime-python"
ensure_ecr_repo "sandboxshift/runtime-node"
ensure_ecr_repo "sandboxshift/runtime-multi"
echo ""

# ───────────────────────────────────────────────────────────────────────────────
# Step 4: Podman login to ECR
# (podman login writes ~/.config/containers/auth.json which skopeo reads)
# ───────────────────────────────────────────────────────────────────────────────

info "Logging in to ECR..."
aws ecr get-login-password --region "${REGION}" \
  | podman login --username AWS --password-stdin "${ECR_REGISTRY}"
ok "ECR credentials stored (used by both podman and skopeo)."
echo ""

# ───────────────────────────────────────────────────────────────────────────────
# Step 5: Build and push runtime images
# ───────────────────────────────────────────────────────────────────────────────

info "Building and pushing runtime images (linux/amd64 — this may take a few minutes)..."
echo ""

build_and_push \
  "images/python" \
  "sandboxshift/runtime-python:3.11" \
  "${ECR_REGISTRY}/sandboxshift/runtime-python:3.11"

build_and_push \
  "images/node" \
  "sandboxshift/runtime-node:20" \
  "${ECR_REGISTRY}/sandboxshift/runtime-node:20"

build_and_push \
  "images/multi" \
  "sandboxshift/runtime-multi:latest" \
  "${ECR_REGISTRY}/sandboxshift/runtime-multi:latest"

echo ""

# ───────────────────────────────────────────────────────────────────────────────
# Step 6: Patch backend.tf
# ───────────────────────────────────────────────────────────────────────────────

info "Patching terraform/fargate/backend.tf..."
cat > "${TF_DIR}/backend.tf" <<EOF
# Auto-generated by sandboxshift-setup.sh — do not edit manually.
# Re-run ./sandboxshift-setup.sh to regenerate.
terraform {
  backend "s3" {
    bucket         = "${STATE_BUCKET}"
    key            = "sandboxshift/fargate/terraform.tfstate"
    region         = "${REGION}"
    encrypt        = true
    dynamodb_table = "${LOCK_TABLE}"
  }
}
EOF
ok "backend.tf patched."

# ───────────────────────────────────────────────────────────────────────────────
# Step 7: terraform init + apply
# ───────────────────────────────────────────────────────────────────────────────

info "Running terraform init..."
cd "${TF_DIR}"
terraform init -reconfigure \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="key=sandboxshift/fargate/terraform.tfstate" \
  -backend-config="region=${REGION}" \
  -backend-config="dynamodb_table=${LOCK_TABLE}" \
  -backend-config="encrypt=true"
ok "terraform init complete."
echo ""

info "Running terraform apply..."
terraform apply -auto-approve \
  -var="aws_region=${REGION}" \
  -var="ecr_registry=${ECR_REGISTRY}" \
  -var="environment=${ENV_TAG}"
ok "terraform apply complete."
echo ""

# ───────────────────────────────────────────────────────────────────────────────
# Step 8: Read outputs and write fargate.env
# ───────────────────────────────────────────────────────────────────────────────

info "Reading terraform outputs..."
TF_OUTPUTS=$(terraform output -json)

CLUSTER_ARN=$(echo "${TF_OUTPUTS}"      | jq -r '.cluster_arn.value')
TASK_DEF_ARN=$(echo "${TF_OUTPUTS}"     | jq -r '.task_def_arn.value')
LOG_GROUP=$(echo "${TF_OUTPUTS}"        | jq -r '.log_group.value')
SUBNET_IDS_CSV=$(echo "${TF_OUTPUTS}"   | jq -r '.subnet_ids.value | join(",")')
SEC_GROUP_IDS=$(echo "${TF_OUTPUTS}"    | jq -r '.security_group_ids.value | join(",")')
WORKSPACE_BUCKET=$(echo "${TF_OUTPUTS}" | jq -r '.workspace_bucket_name.value')

FARGATE_ENV="${HOME}/.sandboxshift/fargate.env"
cat > "${FARGATE_ENV}" <<EOF
# SandboxShift Fargate env vars — auto-generated by sandboxshift-setup.sh
# Auto-loaded by the CLI — no manual source needed.
export FARGATE_CLUSTER_ARN="${CLUSTER_ARN}"
export FARGATE_TASK_DEFINITION_ARN="${TASK_DEF_ARN}"
export FARGATE_SUBNET_IDS="${SUBNET_IDS_CSV}"
export FARGATE_SECURITY_GROUP_IDS="${SEC_GROUP_IDS}"
export FARGATE_LOG_GROUP="${LOG_GROUP}"
export FARGATE_REGION="${REGION}"
export FARGATE_WORKSPACE_BUCKET="${WORKSPACE_BUCKET}"
EOF

ok "Env vars saved to ${FARGATE_ENV}"
echo ""

# ───────────────────────────────────────────────────────────────────────────────
# Done
# ───────────────────────────────────────────────────────────────────────────────

echo -e "${GREEN}"
echo "  \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588"
echo "  SandboxShift AWS setup complete!"
echo "  \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588"
echo -e "${NC}"
echo "  ECR registry:     ${ECR_REGISTRY}"
echo "  Workspace bucket: ${WORKSPACE_BUCKET}"
echo ""
echo "  The CLI auto-loads Fargate credentials \u2014 no export needed."
echo "  Just run:"
echo ""
echo -e "    ${YELLOW}sandboxshift run /tmp/my-project \"echo hello from fargate\" --ram-threshold 999${NC}"
echo ""
