#!/usr/bin/env bash
# =============================================================================
# sandboxshift-setup.sh
#
# One-script setup for SandboxShift.
#
#   ./sandboxshift-setup.sh           # auto-detect (cloud if AWS creds present)
#   ./sandboxshift-setup.sh local     # local mode only — no AWS needed
#   ./sandboxshift-setup.sh cloud     # full cloud setup (ECR + Terraform + fargate.env)
#
# What this script does:
#
#   Both tracks:
#     1. Check prerequisites
#     2. pip install -e .
#     3. Build all 3 runtime images into Podman local store
#
#   Cloud track additionally:
#     4. Verify AWS credentials
#     5. Resolve AWS account ID and region
#     6. Create ECR repository (sandboxshift/runtime-multi) if missing
#     7. Login Podman to ECR
#     8. Tag and push runtime-multi to ECR
#     9. Write terraform/fargate/terraform.tfvars
#    10. terraform init && terraform apply
#    11. Collect all terraform outputs → write ~/.sandboxshift/fargate.env
#    12. Print a ready-to-use test command
# =============================================================================

set -euo pipefail

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
# Script location — all paths are relative to repo root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Parse mode argument
# ---------------------------------------------------------------------------
MODE="${1:-auto}"
if [[ "$MODE" != "local" && "$MODE" != "cloud" && "$MODE" != "auto" ]]; then
  die "Unknown mode '${MODE}'. Usage: $0 [local|cloud|auto]"
fi

# ---------------------------------------------------------------------------
# Step 0: auto-detect mode
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
# Step 1: Check prerequisites
# ---------------------------------------------------------------------------
step "Checking prerequisites ..."

check_cmd() {
  local cmd="$1" install_hint="$2"
  if ! command -v "$cmd" &>/dev/null; then
    die "'${cmd}' not found. ${install_hint}"
  fi
}

check_cmd python3  "Install Python 3.11+: https://python.org"
check_cmd pip      "Install pip: https://pip.pypa.io"
check_cmd podman   "Install Podman: https://podman.io/getting-started/installation"

# Python version check
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ) ]]; then
  die "Python 3.11+ required, found ${PY_VER}"
fi

# Podman rootless check
if ! podman info 2>/dev/null | grep -q 'rootless: true' && \
   ! podman info 2>/dev/null | grep -q 'rootlessCompute: true'; then
  # On macOS with podman machine, rootless is implicit
  if [[ "$(uname)" == "Darwin" ]]; then
    # Check if podman machine is running
    if ! podman machine inspect &>/dev/null 2>&1 || \
       ! podman machine list 2>/dev/null | grep -q 'Currently running'; then
      warn "Podman machine is not running — starting it now ..."
      if ! podman machine inspect &>/dev/null 2>&1; then
        step "Initialising Podman machine (first time, ~2min) ..."
        podman machine init
      fi
      podman machine start
      ok "Podman machine started"
    fi
  else
    warn "Podman rootless check inconclusive — continuing. Run: podman info | grep rootless"
  fi
fi

if [[ "$MODE" == "cloud" ]]; then
  check_cmd aws       "Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
  check_cmd terraform "Install Terraform: https://developer.hashicorp.com/terraform/install"
  check_cmd jq        "Install jq: https://jqlang.github.io/jq/download/"
fi

ok "Prerequisites OK (Python ${PY_VER}, Podman $(podman --version | awk '{print $3}'))"

# ---------------------------------------------------------------------------
# Step 2: Install sandboxshift Python package
# ---------------------------------------------------------------------------
step "Installing sandboxshift (pip install -e .) ..."
pip install -e . --quiet
ok "sandboxshift installed — $(sandboxshift --help | head -1)"

# ---------------------------------------------------------------------------
# Step 3: Build runtime images into Podman local store
# ---------------------------------------------------------------------------
step "Building runtime images into Podman local store ..."
echo

BUILD_FAILED=0

build_image() {
  local tag="$1" context="$2"
  step "  Building ${tag} from ${context} ..."
  if podman build -t "$tag" "$context" --quiet; then
    ok "  ${tag}"
  else
    warn "  Failed to build ${tag} — continuing (non-fatal for local use if not needed)"
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

# Short-circuit if local-only
if [[ "$MODE" == "local" ]]; then
  echo
  echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
  echo -e "${GREEN}${BOLD}  SandboxShift is ready for local use!${RESET}"
  echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
  echo
  echo -e "  Try it:"
  echo -e "    mkdir -p /tmp/ss-test"
  echo -e "    echo 'print(\"hello from sandbox\")' > /tmp/ss-test/hello.py"
  echo -e "    ${BOLD}sandboxshift run /tmp/ss-test \"python hello.py\"${RESET}"
  echo
  echo -e "  To enable cloud burst later:"
  echo -e "    ${BOLD}./sandboxshift-setup.sh cloud${RESET}"
  echo
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 4: Verify AWS credentials
# ---------------------------------------------------------------------------
step "Verifying AWS credentials ..."
if ! IDENTITY=$(aws sts get-caller-identity 2>&1); then
  die "AWS credentials not configured or invalid.\n  Run: aws configure\n  Or set: AWS_PROFILE / AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY"
fi
ACCOUNT_ID=$(echo "$IDENTITY" | jq -r '.Account')
ok "AWS account: ${ACCOUNT_ID}"

# ---------------------------------------------------------------------------
# Step 5: Resolve region
# ---------------------------------------------------------------------------
AWS_REGION_RESOLVED="${AWS_DEFAULT_REGION:-${AWS_REGION:-}}"
if [[ -z "$AWS_REGION_RESOLVED" ]]; then
  AWS_REGION_RESOLVED=$(aws configure get region 2>/dev/null || true)
fi
if [[ -z "$AWS_REGION_RESOLVED" ]]; then
  echo
  echo -n -e "${BLUE}[sandboxshift-setup]${RESET} AWS region (e.g. us-east-1): "
  read -r AWS_REGION_RESOLVED
  [[ -z "$AWS_REGION_RESOLVED" ]] && die "Region is required."
fi
ok "Region: ${AWS_REGION_RESOLVED}"

# ---------------------------------------------------------------------------
# Step 6: Create ECR repository if missing
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
# Step 7: Podman login to ECR
# ---------------------------------------------------------------------------
step "Logging Podman in to ECR ..."
aws ecr get-login-password --region "$AWS_REGION_RESOLVED" | \
  podman login --username AWS --password-stdin "$ECR_REGISTRY"
ok "Podman logged in to ${ECR_REGISTRY}"

# ---------------------------------------------------------------------------
# Step 8: Tag and push runtime-multi to ECR
# ---------------------------------------------------------------------------
step "Tagging sandboxshift/runtime-multi:latest → ${ECR_IMAGE_URI} ..."
podman tag "sandboxshift/runtime-multi:latest" "$ECR_IMAGE_URI"

step "Pushing to ECR (this may take ~1-2 min on first push) ..."
podman push "$ECR_IMAGE_URI"
ok "Image pushed: ${ECR_IMAGE_URI}"

# ---------------------------------------------------------------------------
# Step 9: Write terraform.tfvars
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
# Step 10: terraform init + apply
# ---------------------------------------------------------------------------
step "Running terraform init ..."
(cd "$TF_DIR" && terraform init -upgrade -input=false -no-color 2>&1 | \
  grep -v '^$' | sed 's/^/  /')
ok "terraform init complete"

step "Running terraform apply (this provisions AWS resources ~1-2 min) ..."
(cd "$TF_DIR" && terraform apply -auto-approve -input=false -no-color 2>&1 | \
  grep -v '^$' | sed 's/^/  /')
ok "terraform apply complete"

# ---------------------------------------------------------------------------
# Step 11: Collect terraform outputs → ~/.sandboxshift/fargate.env
# ---------------------------------------------------------------------------
ENV_DIR="$HOME/.sandboxshift"
ENV_FILE="$ENV_DIR/fargate.env"
mkdir -p "$ENV_DIR"

step "Reading terraform outputs ..."
TF_OUTPUTS=$(cd "$TF_DIR" && terraform output -json)

extract() { echo "$TF_OUTPUTS" | jq -r ".${1}.value // empty"; }
extract_json_csv() { echo "$TF_OUTPUTS" | jq -r ".${1}.value | join(\",\")"; }

CLUSTER_ARN=$(extract cluster_arn)
TASK_DEF_ARN=$(extract task_def_arn)
SUBNET_IDS=$(extract_json_csv subnet_ids)
SECURITY_GROUP_IDS=$(extract_json_csv security_group_ids)
LOG_GROUP=$(extract log_group)
REGION=$(extract region)
WORKSPACE_BUCKET=$(extract workspace_bucket_name)
SERVER_SG_ID=$(extract server_security_group_id)

[[ -z "$CLUSTER_ARN" ]]        && die "Could not read cluster_arn from terraform output"
[[ -z "$TASK_DEF_ARN" ]]       && die "Could not read task_def_arn from terraform output"
[[ -z "$SUBNET_IDS" ]]         && die "Could not read subnet_ids from terraform output"
[[ -z "$SECURITY_GROUP_IDS" ]] && die "Could not read security_group_ids from terraform output"
[[ -z "$LOG_GROUP" ]]          && die "Could not read log_group from terraform output"
[[ -z "$REGION" ]]             && die "Could not read region from terraform output"
[[ -z "$WORKSPACE_BUCKET" ]]   && die "Could not read workspace_bucket_name from terraform output"

step "Writing ${ENV_FILE} ..."
cat > "$ENV_FILE" <<EOF
# Auto-generated by sandboxshift-setup.sh — do not edit manually.
# Re-run ./sandboxshift-setup.sh cloud to regenerate after infrastructure changes.
# This file is auto-loaded by the sandboxshift CLI — no manual export needed.

export FARGATE_CLUSTER_ARN="${CLUSTER_ARN}"
export FARGATE_TASK_DEFINITION_ARN="${TASK_DEF_ARN}"
export FARGATE_SUBNET_IDS="${SUBNET_IDS}"
export FARGATE_SECURITY_GROUP_IDS="${SECURITY_GROUP_IDS}"
export FARGATE_LOG_GROUP="${LOG_GROUP}"
export FARGATE_REGION="${REGION}"
export FARGATE_WORKSPACE_BUCKET="${WORKSPACE_BUCKET}"
export FARGATE_SERVER_SECURITY_GROUP_ID="${SERVER_SG_ID}"
EOF
ok "fargate.env written to ${ENV_FILE}"
ok "(auto-loaded by sandboxshift CLI — no manual export needed)"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  SandboxShift cloud burst is ready!${RESET}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════════${RESET}"
echo
echo -e "  ECR image:      ${BOLD}${ECR_IMAGE_URI}${RESET}"
echo -e "  S3 bucket:      ${BOLD}${WORKSPACE_BUCKET}${RESET}"
echo -e "  Region:         ${BOLD}${REGION}${RESET}"
echo
echo -e "  Test it (local — uses Podman):"
echo -e "    mkdir -p /tmp/ss-test"
echo -e "    echo 'print(\"hello from sandbox\")' > /tmp/ss-test/hello.py"
echo -e "    ${BOLD}sandboxshift run /tmp/ss-test \"python hello.py\"${RESET}"
echo
echo -e "  Test it (cloud burst — forces Fargate):"
echo -e "    ${BOLD}sandboxshift run /tmp/ss-test \"python hello.py\" --ram-threshold 999999${RESET}"
echo
echo -e "  Run a Node.js server in the cloud:"
echo -e "    ${BOLD}sandboxshift run /your/node-project \"node index.js\" --port 3000 --ram-threshold 999999${RESET}"
echo -e "    ${BOLD}sandboxshift stop <instance_id>${RESET}  # when done"
echo
