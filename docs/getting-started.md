# Getting Started with SandboxShift

This guide walks you through: install → first local run → reading audit logs → cloud burst setup.

**Target audience:** Any developer on macOS or Linux.

---

## Prerequisites

You install these. Everything else is managed by the setup script.

| Requirement | For | Install |
|-------------|-----|---------|
| Python 3.11+ | always | [python.org](https://python.org) |
| Podman (rootless) | always | [podman.io](https://podman.io/getting-started/installation) |
| AWS CLI v2 | cloud burst only | `brew install awscli` / [AWS docs](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) |

**Not required to install:** Terraform, jq — the setup script downloads and manages these for you.

### Install Podman

```bash
# macOS
brew install podman
podman machine init && podman machine start

# Ubuntu / Debian
sudo apt install podman
```

Verify:
```bash
podman run --rm hello-world
```

---

## Installation

```bash
git clone https://github.com/NihalKA/sandboxshift
cd sandboxshift
chmod +x sandboxshift-setup.sh
```

Then run the setup script for your use case.

### Local mode only (no AWS needed)

```bash
./sandboxshift-setup.sh local
```

This:
1. Downloads **Terraform 1.5.7** to `~/.sandboxshift/bin/` (pinned, never touches your system Terraform)
2. Creates an **isolated Python venv** at `~/.sandboxshift/venv/` (your global env stays clean)
3. Installs sandboxshift into that venv
4. Symlinks the CLI to `~/.sandboxshift/bin/sandboxshift`
5. Builds all 3 runtime images into Podman (`runtime-python`, `runtime-node`, `runtime-multi`)

### Cloud burst mode (local + AWS Fargate)

```bash
./sandboxshift-setup.sh cloud
```

Everything above, plus:
6. Creates an ECR repository in your AWS account and pushes `runtime-multi`
7. Runs `terraform apply` to provision ECS cluster, S3 bucket, IAM roles, and security groups
8. Writes `~/.sandboxshift/fargate.env` with all connection variables
9. The CLI **auto-loads `fargate.env`** on every run — no manual `source` or `export` needed

> **Auto-detect:** `./sandboxshift-setup.sh` (no argument) runs cloud if `aws sts get-caller-identity` succeeds, otherwise local.

### Add CLI to your PATH

```bash
echo 'export PATH="$HOME/.sandboxshift/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
# or for bash:
echo 'export PATH="$HOME/.sandboxshift/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

Verify:
```bash
sandboxshift --help
```

---

## First Run — Local Mode

Create a test workspace:

```bash
mkdir -p /tmp/test-workspace
echo 'print("hello from sandbox")' > /tmp/test-workspace/hello.py
```

Run a task:

```bash
sandboxshift run /tmp/test-workspace "python hello.py"
```

Expected output:

```
[sandboxshift] Sandbox provisioned: podman-...
[sandboxshift] ✓ Task complete
Runtime: local
Duration: 1.23s
Exit code: 0
hello from sandbox
```

Key points:
- Exit code 0 = task succeeded
- `Runtime: local` — BurstEngine chose local because RAM was sufficient
- Mode is decided **before** the task starts — no mid-execution switching

---

## First Run — Node.js

```bash
mkdir -p /tmp/node-test
cat > /tmp/node-test/index.js <<'EOF'
const http = require('http');
const port = process.env.PORT || 3000;
http.createServer((req, res) => {
  res.end('hello from sandbox\n');
}).listen(port, () => console.log(`Listening on port ${port}`));
EOF
echo '{"name":"test"}' > /tmp/node-test/package.json

# Run as a local server (PORT injected automatically by sandboxshift)
sandboxshift run /tmp/node-test "node index.js" --port 3000
```

SandboxShift auto-injects `PORT=3000` into the container — your app reads `process.env.PORT` without hardcoding it.

---

## Reading the Audit Log

Every run appends structured JSON events to `~/.sandboxshift/audit.log`:

```bash
sandboxshift audit tail
sandboxshift audit tail --lines 50

# Raw JSON
cat ~/.sandboxshift/audit.log | python3 -m json.tool
```

Events you'll see for each run:

| Event | What it means |
|-------|---------------|
| `scan_started` | SensitivityScanner begins |
| `scan_complete` | Result — `is_sensitive`, `findings` |
| `burst_decision` | BurstEngine chose `local` or `cloud`, with reason |
| `provision` | Sandbox started, workspace staged |
| `execute` | Task ran — `exit_code` |
| `destroy` | Container stopped and cleaned up |
| `run_complete` | Total duration and runtime mode |

---

## Sensitive Data Handling

```bash
mkdir -p /tmp/sensitive-workspace
echo "AWS_SECRET_ACCESS_KEY=EXAMPLEKEY123" > /tmp/sensitive-workspace/.env
echo 'print("processing")' > /tmp/sensitive-workspace/main.py

sandboxshift run /tmp/sensitive-workspace "python main.py"
```

Expected:
```
Runtime: local
```
Even if RAM is tight — sensitive workspaces are always forced local. The sensitivity reasons appear in the audit log.

---

## Cloud Burst Setup

### Prerequisites

- AWS CLI configured: `aws configure`
- That's it — Terraform is downloaded automatically by the setup script

### Run the setup script

```bash
./sandboxshift-setup.sh cloud
```

The script will:
1. Download Terraform 1.5.7 to `~/.sandboxshift/bin/` (skipped if already cached at correct version)
2. Verify AWS credentials and detect your account ID and region (prompts if not set)
3. Create ECR repository `sandboxshift/runtime-multi` (if not exists)
4. Login Podman to ECR and push the image (~1-2 min on first push)
5. Run `terraform apply` in `terraform/fargate/` (~1-2 min)
6. Write `~/.sandboxshift/fargate.env` — auto-loaded by the CLI

### What gets provisioned in your AWS account

| Resource | What it is |
|----------|------------|
| ECS Cluster | `sandboxshift` |
| ECS Task Definition | `sandboxshift-sandbox` (uses your ECR image) |
| S3 Bucket | `sandboxshift-ws-{account}-{suffix}` — workspace staging |
| IAM Roles | Task execution role + task role (S3 access only) |
| Security Group (batch) | Outbound-only |
| Security Group (server) | ALL TCP inbound — only attached for `--port` tasks |
| CloudWatch Log Group | `/sandboxshift/tasks` — 7-day retention |

### Environment variables (auto-written to `~/.sandboxshift/fargate.env`)

| Variable | Required | Source |
|----------|----------|--------|
| `FARGATE_CLUSTER_ARN` | ✓ | `terraform output cluster_arn` |
| `FARGATE_TASK_DEFINITION_ARN` | ✓ | `terraform output task_def_arn` |
| `FARGATE_SUBNET_IDS` | ✓ | `terraform output subnet_ids` |
| `FARGATE_SECURITY_GROUP_IDS` | ✓ | `terraform output security_group_ids` |
| `FARGATE_LOG_GROUP` | ✓ | `terraform output log_group` |
| `FARGATE_REGION` | ✓ | `terraform output region` |
| `FARGATE_WORKSPACE_BUCKET` | ✓ | `terraform output workspace_bucket_name` |
| `FARGATE_SERVER_SECURITY_GROUP_ID` | optional | `terraform output server_security_group_id` |

The CLI reads this file automatically on every invocation. You never need to `source` or `export` anything.

### Test cloud burst

```bash
# Force cloud by setting RAM threshold impossibly high
sandboxshift run /tmp/test-workspace "python hello.py" --ram-threshold 999999
```

Expected output:
```
[sandboxshift] Uploading N file(s) to S3 ...
[sandboxshift] ✓ Workspace uploaded
[sandboxshift] Submitting ECS Fargate task ...
[sandboxshift] ✓ Task submitted: <task-id>
[sandboxshift] PROVISIONING ...
[sandboxshift] RUNNING ...
[sandboxshift] ✓ Logs retrieved
Runtime: cloud
```

### Run a cloud server

```bash
# Starts a Node server on Fargate, prints URL, tails logs live
sandboxshift run /tmp/node-test "node index.js" --port 3000 --ram-threshold 999999

# Output:
#   [sandboxshift] Server is RUNNING
#     http://54.x.x.x:3000
#     To stop: sandboxshift stop ss-abc123...
#   [sandboxshift] Streaming logs (Ctrl+C to stop tailing — server stays running):

# Stop when done
sandboxshift stop ss-abc123...
```

---

## Updating after infrastructure changes

Re-running `./sandboxshift-setup.sh cloud` is idempotent:
- Terraform is skipped if already cached at the correct version
- `terraform apply` is a no-op if nothing changed
- `fargate.env` is overwritten with the latest outputs

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `podman: command not found` | Podman not installed | See Install Podman above |
| `Error: rootless mode requires...` | Podman not rootless | `podman machine init && podman machine start` (macOS) |
| `node: not found` inside container | Old task def uses python-only image | Re-run `./sandboxshift-setup.sh cloud` |
| Cloud tasks always go local | `fargate.env` missing or incomplete | Re-run `./sandboxshift-setup.sh cloud` |
| `sandboxshift: command not found` | `~/.sandboxshift/bin` not in PATH | Add to shell profile — see PATH step above |
| `No module named uvicorn` in ECS | `requirements.txt` missing uvicorn | Add `uvicorn` + `fastapi` to your `requirements.txt` |
| `exit_code: 1` but task ran | Task failed inside sandbox | Check stderr — non-zero exit is a task result, not an API error |
| Terraform state conflict on re-apply | Old state references deleted resources | `cd terraform/fargate && ~/.sandboxshift/bin/terraform refresh` then re-run setup |
