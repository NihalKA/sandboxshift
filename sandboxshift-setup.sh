#!/usr/bin/env bash
# =============================================================================
# sandboxshift-setup.sh
#
# One-script setup for SandboxShift. Manages its own isolated environment:
#   - Downloads pinned Terraform 1.5.7 to ~/.sandboxshift/bin/ (always)
#   - Creates an isolated Python venv at ~/.sandboxshift/venv/
#   - Symlinks the CLI to ~/.sandboxshift/bin/sandboxshift
#
# Prerequisites the USER must install:
#   - Python 3.11+
#   - Podman (rootless)
#   - AWS CLI v2  (cloud mode only)
#
# Everything else (Terraform, jq) is managed by this script.
#
# Usage:
#   ./sandboxshift-setup.sh           # auto-detect (cloud if AWS creds present)
#   ./sandboxshift-setup.sh local     # local mode only — no AWS needed
#   ./sandboxshift-setup.sh cloud     # full cloud setup
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SANDBOXSHIFT_HOME="${SANDBOXSHIFT_HOME:-$HOME/.sandboxshift}"
BIN_DIR="$SANDBOXSHIFT_HOME/bin"
VENV_DIR="$SANDBOXSHIFT_HOME/venv"
TF_BIN="$BIN_DIR/terraform"
TF_VERSION="1.5.7"

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

step()  { echo -e "${BLUE}[sandboxshift-setup]${RESET} $*"; }
ok()    { echo -e "${GREEN}[sandboxshift-setup]${RESET} ${GREEN}✓${RESET} $*"; }
warn()  { echo -e "${YELLOW}[sandboxshift-setup]${RESET} ${YELLOW}⚠${RESET}  $*"; }
die()   { echo -e "${RED}[sandboxshift-setup]${RESET} ${RED}✗${RESET} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Script location — all relative paths from repo root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Parse mode argument
# ---------------------------------------------------------------------------
MODE="${1:-auto}"
[[ "$MODE" != "local" && "$MODE" != "cloud" && "$MODE" != "auto" ]] && \
  die "Unknown mode '${MODE}'. Usage: $0 [local|cloud|auto]"

# ---------------------------------------------------------------------------
# Step 0: Auto-detect mode
# ---------------------------------------------------------------------------
if [[ "$MODE" == "auto" ]]; then
  step "Auto-detecting setup mode ..."
  if aws sts get-caller-identity &>/dev/null 2>&1; then
    MODE="cloud"
    ok "AWS credentials found — running cloud setup"
  else
    MODE="local"
    warn "No AWS credentials found — running local-only setup"
    warn "Run './sandboxshift-setup.sh cloud' later to enable cloud burst"
  fi
fi

# ---------------------------------------------------------------------------
# Step 1: Check user-installed prerequisites
# ---------------------------------------------------------------------------
step "Checking prerequisites ..."

check_cmd() {
  local cmd="$1" hint="$2"
  command -v "$cmd" &>/dev/null || die "'${cmd}' not found. ${hint}"
}

check_cmd python3 "Install Python 3.11+: https://python.org"

# Python version check
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
[[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ) ]] && \
  die "Python 3.11+ required, found ${PY_VER}"

check_cmd podman "Install Podman: https://podman.io/getting-started/installation"

# macOS: ensure podman machine is running
if [[ "$(uname)" == "Darwin" ]]; then
  if ! podman machine list 2>/dev/null | grep -q 'Currently running'; then
    warn "Podman machine is not running — starting it now ..."
    if ! podman machine inspect &>/dev/null 2>&1; then
      step "Initialising Podman machine (first time, ~2 min) ..."
      podman machine init
    fi
    podman machine start
    ok "Podman machine started"
  fi
fi

[[ "$MODE" == "cloud" ]] && \
  check_cmd aws "Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"

ok "Prerequisites OK (Python ${PY_VER}, Podman $(podman --version | awk '{print $3}'))"

# ---------------------------------------------------------------------------
# Step 2: Create sandboxshift home directories
# ---------------------------------------------------------------------------
mkdir -p "$BIN_DIR"

# ---------------------------------------------------------------------------
# Step 3: Download pinned Terraform (always use our own, never system terraform)
# ---------------------------------------------------------------------------
_download_terraform() {
  # Check if our pinned version is already present
  if [[ -x "$TF_BIN" ]]; then
    local existing_ver
    existing_ver=$("$TF_BIN" version -json 2>/dev/null | \
      python3 -c "import json,sys; print(json.load(sys.stdin).get('terraform_version','unknown'))" \
      2>/dev/null || echo "unknown")
    if [[ "$existing_ver" == "$TF_VERSION" ]]; then
      ok "Terraform ${TF_VERSION} already present (${TF_BIN})"
      return 0
    fi
    step "Replacing cached Terraform ${existing_ver} with pinned ${TF_VERSION} ..."
  else
    step "Downloading Terraform ${TF_VERSION} (managed by sandboxshift) ..."
  fi

  # Detect platform
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  arch=$(uname -m)
  case "$arch" in
    x86_64)        arch="amd64" ;;
    arm64|aarch64) arch="arm64" ;;
    *) die "Unsupported architecture: ${arch}" ;;
  esac

  local tf_url="https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_${os}_${arch}.zip"
  local tf_zip="${BIN_DIR}/terraform_${TF_VERSION}.zip"

  step "  Fetching ${tf_url} ..."
  python3 - "$tf_url" "$tf_zip" <<'PYEOF'
import urllib.request, ssl, sys, os

# macOS Python from python.org does not use the system certificate store by
# default, causing SSLCertVerificationError. Fix: load /etc/ssl/cert.pem
# (the macOS system root CA bundle, present since macOS 10.13 High Sierra)
# into the SSL context explicitly. SSL verification is NOT disabled — all
# server certificates are still fully verified against the system root CAs.
ctx = ssl.create_default_context()
if sys.platform == 'darwin' and os.path.exists('/etc/ssl/cert.pem'):
    ctx = ssl.create_default_context(cafile='/etc/ssl/cert.pem')

url, dest = sys.argv[1], sys.argv[2]
def progress(count, block, total):
    pct = min(int(count * block * 100 / total), 100) if total > 0 else 0
    print(f"  ... {pct}%", end="\r", flush=True)

opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
urllib.request.install_opener(opener)
urllib.request.urlretrieve(url, dest, reporthook=progress)
print()
PYEOF

  step "  Extracting ..."
  python3 - "$tf_zip" "$BIN_DIR" <<'PYEOF'
import zipfile, sys
with zipfile.ZipFile(sys.argv[1]) as z:
    z.extract("terraform", sys.argv[2])
PYEOF

  chmod +x "$TF_BIN"
  rm -f "$tf_zip"
  ok "Terraform ${TF_VERSION} installed at ${TF_BIN}"
}

_download_terraform

# ---------------------------------------------------------------------------
# Step 4: Create isolated Python venv + install sandboxshift
# ---------------------------------------------------------------------------
step "Setting up isolated Python environment at ${VENV_DIR} ..."

# Create or re-use venv
if [[ ! -f "${VENV_DIR}/bin/python3" ]]; then
  python3 -m venv "$VENV_DIR"
  ok "Venv created at ${VENV_DIR}"
else
  ok "Venv already exists at ${VENV_DIR}"
fi

step "Installing sandboxshift into venv ..."
"$VENV_DIR/bin/pip" install -e . --quiet
ok "sandboxshift installed in isolated venv"

# Symlink CLI into BIN_DIR so ~/.sandboxshift/bin/sandboxshift works
ln -sf "$VENV_DIR/bin/sandboxshift" "$BIN_DIR/sandboxshift"
ok "CLI symlinked: ${BIN_DIR}/sandboxshift"

# PATH hint — print if not already on PATH
if ! echo ":${PATH}:" | grep -q ":${BIN_DIR}:"; then
  echo
  warn "Add ~/.sandboxshift/bin to your PATH to use the CLI from anywhere:"
  echo -e "  ${BOLD}echo 'export PATH=\"\$HOME/.sandboxshift/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc${RESET}"
  echo -e "  ${BOLD}# or for bash: >> ~/.bashrc${RESET}"
fi

# Use our venv sandboxshift for the rest of the script
SANDBOXSHIFT_CMD="$VENV_DIR/bin/sandboxshift"

# ---------------------------------------------------------------------------
# Step 5: Build runtime images into Podman local store
# ---------------------------------------------------------------------------
step "Building runtime images into Podman local store ..."
echo

BUILD_FAILED=0

build_image() {
  local tag="$1" context="$2"
  step "  Building ${tag} ..."
  if podman build -t "$tag" "$context" --quiet 2>&1 | sed 's/^/    /'; then
    ok "  ${tag}"
  else
    warn "  Failed to build ${tag} (non-fatal — continuing)"
    BUILD_FAILED=1
  fi
}

build_image "sandboxshift/runtime-python:3.11" "images/python"
build_image "sandboxshift/runtime-node:20"      "images/node"
build_image "sandboxshift/runtime-multi:latest" "images/multi"
echo

if [[ "$BUILD_FAILED" -eq 1 ]]; then
  warn "One or more image builds failed. Local mode may be limited."
else
  ok "All runtime images built"
fi

_print_local_success() {
  echo
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
  echo -e "${GREEN}${BOLD}  SandboxShift is ready for local use!${RESET}"
  echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
  echo
  echo -e "  Try it:"
  echo -e "    mkdir -p /tmp/ss-test"
  echo -e "    echo 'print(\"hello from sandbox\")' > /tmp/ss-test/hello.py"
  echo -e "    ${BOLD}sandboxshift run /tmp/ss-test \"python hello.py\"${RESET}"
  echo
  echo -e "  To enable cloud burst later:"
  echo -e "    ${BOLD}./sandboxshift-setup.sh cloud${RESET}"
  echo
}

# Short-circuit for local mode
if [[ "$MODE" == "local" ]]; then
  _print_local_success
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 6: Verify AWS credentials
# ---------------------------------------------------------------------------
step "Verifying AWS credentials ..."
IDENTITY_JSON=$(aws sts get-caller-identity 2>&1) || \
  die "AWS credentials not configured.\n  Run: aws configure  (or set AWS_PROFILE)"

ACCOUNT_ID=$(echo "$IDENTITY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['Account'])")
ok "AWS account: ${ACCOUNT_ID}"

# ---------------------------------------------------------------------------
# Step 7: Resolve region
# ---------------------------------------------------------------------------
AWS_REGION_RESOLVED="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
[[ -z "$AWS_REGION_RESOLVED" ]] && \
  AWS_REGION_RESOLVED=$(aws configure get region 2>/dev/null || true)
if [[ -z "$AWS_REGION_RESOLVED" ]]; then
  echo
  echo -n -e "${BLUE}[sandboxshift-setup]${RESET} AWS region (e.g. us-east-1): "
  read -r AWS_REGION_RESOLVED
  [[ -z "$AWS_REGION_RESOLVED" ]] && die "Region is required."
fi
ok "Region: ${AWS_REGION_RESOLVED}"

# ---------------------------------------------------------------------------
# Step 8: Create ECR repository if missing
# ---------------------------------------------------------------------------
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION_RESOLVED}.amazonaws.com"
ECR_REPO="sandboxshift/runtime-multi"
ECR_IMAGE_URI="${ECR_REGISTRY}/${ECR_REPO}:latest"

step "Ensuring ECR repository '${ECR_REPO}' exists ..."
if aws ecr describe-repositories \
     --repository-names "$ECR_REPO" \
     --region "$AWS_REGION_RESOLVED" &>/dev/null; then
  ok "ECR repository already exists"
else
  step "Creating ECR repository ..."
  aws ecr create-repository \
    --repository-name "$ECR_REPO" \
    --region "$AWS_REGION_RESOLVED" \
    --image-scanning-configuration scanOnPush=true \
    --tags Key=Project,Value=SandboxShift >/dev/null
  ok "ECR repository created: ${ECR_REPO}"
fi

# ---------------------------------------------------------------------------
# Step 9: Podman login to ECR
# ---------------------------------------------------------------------------
step "Logging Podman in to ECR ..."
aws ecr get-login-password --region "$AWS_REGION_RESOLVED" | \
  podman login --username AWS --password-stdin "$ECR_REGISTRY"
ok "Podman logged in to ${ECR_REGISTRY}"

# ---------------------------------------------------------------------------
# Step 10: Tag and push runtime-multi to ECR
# ---------------------------------------------------------------------------
step "Tagging sandboxshift/runtime-multi:latest → ${ECR_IMAGE_URI} ..."
podman tag "sandboxshift/runtime-multi:latest" "$ECR_IMAGE_URI"

step "Pushing to ECR (~1-2 min on first push) ..."
podman push "$ECR_IMAGE_URI"
ok "Image pushed: ${ECR_IMAGE_URI}"

# ---------------------------------------------------------------------------
# Step 11: Write terraform.tfvars
# ---------------------------------------------------------------------------
TF_DIR="$SCRIPT_DIR/terraform/fargate"
TF_VARS_FILE="$TF_DIR/terraform.tfvars"

step "Writing ${TF_VARS_FILE} ..."
cat > "$TF_VARS_FILE" <<EOF
aws_region   = "${AWS_REGION_RESOLVED}"
ecr_registry = "${ECR_REGISTRY}"
EOF
ok "terraform.tfvars written"

# ---------------------------------------------------------------------------
# Step 12: Create S3 state bucket + DynamoDB lock table, write backend.tf
#
# Names are deterministic — derived from account ID so re-running the script
# always points to the same bucket and table on any machine.
#
#   Bucket: sandboxshift-tfstate-<account_id>-<6char_hash>
#   Table:  sandboxshift-tfstate-lock-<6char_hash>
#   Hash:   md5(account_id)[:6]  — 16M combinations, effectively unique per account
#
# The S3 bucket is separate from the workspace bucket (created by main.tf).
# Creating it here (before terraform init) breaks the circular dependency:
#   workspace bucket → created by Terraform (main.tf)
#   state bucket     → created by this script (before terraform init)
# ---------------------------------------------------------------------------
TF_STATE_HASH=$(python3 -c "import hashlib; print(hashlib.md5('${ACCOUNT_ID}'.encode()).hexdigest()[:6])")
TF_STATE_BUCKET="sandboxshift-tfstate-${ACCOUNT_ID}-${TF_STATE_HASH}"
TF_STATE_TABLE="sandboxshift-tfstate-lock-${TF_STATE_HASH}"

step "Ensuring Terraform state S3 bucket '${TF_STATE_BUCKET}' ..."
if aws s3api head-bucket --bucket "$TF_STATE_BUCKET" --region "$AWS_REGION_RESOLVED" 2>/dev/null; then
  ok "State bucket already exists"
else
  # us-east-1 is the default region — it rejects CreateBucketConfiguration
  if [[ "$AWS_REGION_RESOLVED" == "us-east-1" ]]; then
    aws s3api create-bucket \
      --bucket "$TF_STATE_BUCKET" \
      --region "$AWS_REGION_RESOLVED" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "$TF_STATE_BUCKET" \
      --region "$AWS_REGION_RESOLVED" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION_RESOLVED" >/dev/null
  fi
  aws s3api put-bucket-versioning \
    --bucket "$TF_STATE_BUCKET" \
    --versioning-configuration Status=Enabled >/dev/null
  aws s3api put-bucket-encryption \
    --bucket "$TF_STATE_BUCKET" \
    --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
  aws s3api put-public-access-block \
    --bucket "$TF_STATE_BUCKET" \
    --public-access-block-configuration \
      'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true' >/dev/null
  ok "State bucket created: ${TF_STATE_BUCKET}"
fi

step "Ensuring Terraform state DynamoDB lock table '${TF_STATE_TABLE}' ..."
if aws dynamodb describe-table \
     --table-name "$TF_STATE_TABLE" \
     --region "$AWS_REGION_RESOLVED" &>/dev/null; then
  ok "State lock table already exists"
else
  aws dynamodb create-table \
    --table-name "$TF_STATE_TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$AWS_REGION_RESOLVED" >/dev/null
  step "  Waiting for DynamoDB table to become active ..."
  aws dynamodb wait table-exists \
    --table-name "$TF_STATE_TABLE" \
    --region "$AWS_REGION_RESOLVED"
  ok "State lock table created: ${TF_STATE_TABLE}"
fi

# Generate backend.tf with real bucket/table/region.
# backend.tf is gitignored — it does not exist in the repo. setup.sh generates
# it on every run. terraform.tfstate (if any) is migrated to S3.
step "Writing terraform/fargate/backend.tf ..."
cat > "$TF_DIR/backend.tf" <<EOF
# Auto-generated by sandboxshift-setup.sh — do not edit manually.
# Re-run ./sandboxshift-setup.sh to regenerate.
#
# S3 bucket and DynamoDB table were created by sandboxshift-setup.sh
# before terraform init ran. They are named deterministically from your
# AWS account ID so re-running the script always uses the same backend.
terraform {
  backend "s3" {
    bucket         = "${TF_STATE_BUCKET}"
    key            = "sandboxshift/fargate/terraform.tfstate"
    region         = "${AWS_REGION_RESOLVED}"
    encrypt        = true
    dynamodb_table = "${TF_STATE_TABLE}"
  }
}
EOF
ok "backend.tf written (s3://${TF_STATE_BUCKET})"

# ---------------------------------------------------------------------------
# Step 13: terraform init + apply  (using OUR pinned terraform binary)
#
# If a local terraform.tfstate exists with content (e.g. from the previous
# local-backend period), migrate it to S3 automatically. Otherwise just
# reconfigure — state is already in S3 or there is no prior state.
# ---------------------------------------------------------------------------
step "Running terraform init ..."
if [[ -s "${TF_DIR}/terraform.tfstate" ]]; then
  step "  Local state detected — migrating to S3 backend ..."
  (cd "$TF_DIR" && echo "yes" | "$TF_BIN" init -upgrade -migrate-state -input=false -no-color 2>&1 | \
    grep -v '^$' | sed 's/^/  /')
else
  (cd "$TF_DIR" && "$TF_BIN" init -upgrade -reconfigure -input=false -no-color 2>&1 | \
    grep -v '^$' | sed 's/^/  /')
fi
ok "terraform init complete"

step "Running terraform apply (provisions AWS resources ~1-2 min) ..."
(cd "$TF_DIR" && "$TF_BIN" apply -auto-approve -input=false -no-color 2>&1 | \
  grep -v '^$' | sed 's/^/  /')
ok "terraform apply complete"

# ---------------------------------------------------------------------------
# Step 14: Read terraform outputs → ~/.sandboxshift/fargate.env
#          All JSON parsing done with Python — no jq needed.
#
# Decision #64: task_def_arn removed. FargateRuntime now registers a fresh
# ECS task definition per run using execution_role_arn and task_role_arn.
# There is no static task definition ARN any more — CPU/memory are baked in at run time.
# ---------------------------------------------------------------------------
step "Reading terraform outputs ..."
TF_OUTPUTS=$((cd "$TF_DIR" && "$TF_BIN" output -json) 2>/dev/null)

extract() {
  python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get(sys.argv[2],{}).get('value',''))" \
    "$TF_OUTPUTS" "$1"
}
extract_csv() {
  python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(','.join(d.get(sys.argv[2],{}).get('value',[])))" \
    "$TF_OUTPUTS" "$1"
}

CLUSTER_ARN=$(extract cluster_arn)
EXECUTION_ROLE_ARN=$(extract execution_role_arn)
TASK_ROLE_ARN=$(extract task_role_arn)
SUBNET_IDS=$(extract_csv subnet_ids)
SECURITY_GROUP_IDS=$(extract_csv security_group_ids)
LOG_GROUP=$(extract log_group)
REGION=$(extract region)
WORKSPACE_BUCKET=$(extract workspace_bucket_name)
SERVER_SG_ID=$(extract server_security_group_id)
ECR_IMAGE_OUTPUT=$(extract ecr_image)

[[ -z "$CLUSTER_ARN" ]]          && die "Could not read cluster_arn"
[[ -z "$EXECUTION_ROLE_ARN" ]]   && die "Could not read execution_role_arn"
[[ -z "$TASK_ROLE_ARN" ]]        && die "Could not read task_role_arn"
[[ -z "$SUBNET_IDS" ]]           && die "Could not read subnet_ids"
[[ -z "$SECURITY_GROUP_IDS" ]]   && die "Could not read security_group_ids"
[[ -z "$LOG_GROUP" ]]            && die "Could not read log_group"
[[ -z "$REGION" ]]               && die "Could not read region"
[[ -z "$WORKSPACE_BUCKET" ]]     && die "Could not read workspace_bucket_name"

step "Writing ${SANDBOXSHIFT_HOME}/fargate.env ..."
cat > "$SANDBOXSHIFT_HOME/fargate.env" <<EOF
# Auto-generated by sandboxshift-setup.sh — do not edit manually.
# Re-run ./sandboxshift-setup.sh cloud to regenerate after infrastructure changes.
# Auto-loaded by the sandboxshift CLI on every invocation — no manual source needed.
#
# Decision #64: FargateRuntime registers a fresh ECS task definition per run
# using FARGATE_EXECUTION_ROLE_ARN and FARGATE_TASK_ROLE_ARN. There is no
# static task definition ARN any more — CPU/memory are baked in at run time.

export FARGATE_CLUSTER_ARN="${CLUSTER_ARN}"
export FARGATE_EXECUTION_ROLE_ARN="${EXECUTION_ROLE_ARN}"
export FARGATE_TASK_ROLE_ARN="${TASK_ROLE_ARN}"
export FARGATE_SUBNET_IDS="${SUBNET_IDS}"
export FARGATE_SECURITY_GROUP_IDS="${SECURITY_GROUP_IDS}"
export FARGATE_LOG_GROUP="${LOG_GROUP}"
export FARGATE_REGION="${REGION}"
export FARGATE_WORKSPACE_BUCKET="${WORKSPACE_BUCKET}"
export FARGATE_SERVER_SECURITY_GROUP_ID="${SERVER_SG_ID}"
export FARGATE_TASK_FAMILY="sandboxshift-sandbox"
export FARGATE_ECR_IMAGE="${ECR_IMAGE_OUTPUT}"

# Terraform state backend (for reference — managed by setup script)
export TF_STATE_BUCKET="${TF_STATE_BUCKET}"
export TF_STATE_TABLE="${TF_STATE_TABLE}"
EOF
ok "fargate.env written"
ok "(auto-loaded by sandboxshift CLI — no manual source/export needed)"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  SandboxShift cloud burst is ready!${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════${RESET}"
echo
echo -e "  ECR image:     ${BOLD}${ECR_IMAGE_URI}${RESET}"
echo -e "  S3 bucket:     ${BOLD}${WORKSPACE_BUCKET}${RESET}"
echo -e "  State bucket:  ${BOLD}${TF_STATE_BUCKET}${RESET}"
echo -e "  Region:        ${BOLD}${REGION}${RESET}"
echo
echo -e "  Test local:"
echo -e "    mkdir -p /tmp/ss-test && echo 'print(\"hello\")' > /tmp/ss-test/hello.py"
echo -e "    ${BOLD}sandboxshift run /tmp/ss-test \"python hello.py\"${RESET}"
echo
echo -e "  Test cloud burst:"
echo -e "    ${BOLD}sandboxshift run /tmp/ss-test \"python hello.py\" --ram-threshold 999999${RESET}"
echo
echo -e "  Run a Node.js server in the cloud:"
echo -e "    ${BOLD}sandboxshift run /your/node-app \"node index.js\" --port 3000 --ram-threshold 999999${RESET}"
echo -e "    ${BOLD}sandboxshift stop <instance_id>${RESET}  # when done"
echo
