# Getting Started with SandboxShift

This guide walks you through: install → first local run → reading audit logs → cloud burst setup.

**Target audience:** Individual DevOps / platform engineer on macOS or Linux.

---

## Prerequisites

| Requirement | Check |
|-------------|-------|
| Python 3.11+ | `python --version` |
| Podman (rootless) | `podman info \| grep rootless` → should say `true` |
| AWS account | Only needed for cloud burst mode |

### Install and configure rootless Podman

```bash
# macOS
brew install podman
podman machine init && podman machine start

# Ubuntu / Debian
sudo apt install podman
```

Verify rootless works:
```bash
podman run --rm hello-world
```

---

## Installation

```bash
pip install sandboxshift

# Start the API server
uvicorn sandboxshift.api:app --factory --host 127.0.0.1 --port 8000
```

Verify it's running:
```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"0.1.0"}
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
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "workspace": "/tmp/test-workspace",
    "task": "python hello.py"
  }' | jq .
```

Expected response:

```json
{
  "runtime_mode": "local",
  "sensitivity_reasons": [],
  "burst_confidence": "preferred",
  "duration_seconds": 1.23,
  "task_result": {
    "exit_code": 0,
    "stdout": "hello from sandbox\n",
    "stderr": "",
    "duration_seconds": 0.85
  }
}
```

Key points:
- `exit_code` 0 = task succeeded
- Non-zero `exit_code` is still HTTP 200 — it's a task result, not an API error (Decision #36)
- `runtime_mode: "local"` — BurstEngine chose local because RAM was sufficient (Decision #5)
- Mode is decided **before** the task starts — there is no mid-execution switching

---

## Reading the Audit Log

Every run appends structured JSON events to `~/.sandboxshift/audit.log`:

```bash
cat ~/.sandboxshift/audit.log | jq .
```

Or use the API:

```bash
curl http://localhost:8000/audit | jq .
```

Events you'll see for each run:

| Event | What it means |
|-------|---------------|
| `scan_started` | SensitivityScanner begins |
| `scan_complete` | Sensitivity result — `is_sensitive`, `findings` |
| `burst_decision` | BurstEngine chose `local` or `cloud`, with reason |
| `provision` | Sandbox container started |
| `execute` | Task ran — `exit_code`, `stdout`, `stderr` |
| `destroy` | Container stopped and removed |
| `run_complete` | Total duration and runtime mode |

**Note:** Malformed lines in the audit log are silently skipped at read time (Decision #37). The log is append-only and is not writable from inside the sandbox.

---

## Sensitive Data Handling

Create a workspace with a sensitive file:

```bash
mkdir -p /tmp/sensitive-workspace
echo "AWS_SECRET_ACCESS_KEY=EXAMPLEKEY123" > /tmp/sensitive-workspace/.env
echo 'print("processing")' > /tmp/sensitive-workspace/main.py
```

Run a task:

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "workspace": "/tmp/sensitive-workspace",
    "task": "python main.py"
  }' | jq '{runtime_mode, sensitivity_reasons}'
```

Expected:
```json
{
  "runtime_mode": "local",
  "sensitivity_reasons": ["found .env file", "content: secret= assignment"]
}
```

Key behaviours:
- `runtime_mode` is always `"local"` when sensitive data is detected (Decision #9, fail-closed)
- BurstEngine is bypassed entirely — mode is forced regardless of available RAM
- If the sensitivity scan itself fails (e.g., permission error reading a file), SandboxShift still forces local — scan error never silently allows cloud execution

---

## Cloud Burst Setup {#cloud-burst-setup}

Cloud burst requires a one-time Terraform apply to provision resources in **your** AWS account.

### Step 1 — Apply Terraform

```bash
cd terraform/fargate
terraform init

cat > terraform.tfvars << 'EOF'
aws_region            = "us-east-1"
workspace_bucket_name = "sandboxshift-workspace-YOUR_ACCOUNT_ID"
EOF

terraform apply
```

### Step 2 — Export env vars

```bash
export FARGATE_CLUSTER_ARN=$(terraform output -raw cluster_arn)
export FARGATE_TASK_DEFINITION_ARN=$(terraform output -raw task_def_arn)
export FARGATE_SUBNET_IDS=$(terraform output -json subnet_ids | jq -r 'join(",")')
export FARGATE_SECURITY_GROUP_IDS=$(terraform output -json security_group_ids | jq -r 'join(",")')
export FARGATE_LOG_GROUP=$(terraform output -raw log_group)
export FARGATE_REGION=$(terraform output -raw region)
```

All 6 env vars must be present. If any are missing, SandboxShift falls back to local-only mode (no error — this is intentional).

### Step 3 — Restart the API server

```bash
uvicorn sandboxshift.api:app --factory --host 127.0.0.1 --port 8000
```

### Step 4 — Run a cloud task

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "workspace": "/tmp/test-workspace",
    "task": "python hello.py",
    "mode": "cloud"
  }' | jq '{runtime_mode, duration_seconds}'
```

Expected: `"runtime_mode": "cloud"`.

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `podman: command not found` | Podman not installed | Install Podman for your OS |
| `Error: rootless mode requires...` | Podman not configured rootlessly | Run `podman machine init && podman machine start` (macOS) |
| `runtime_mode: "local"` when expecting cloud | Not all 6 FARGATE_* env vars set | Run `env \| grep FARGATE` and check all 6 are present |
| `422 Unprocessable Entity` on `/run` | Workspace path doesn't exist, or is a sensitive path (`~/.ssh`, `/etc`) | Use an explicit, non-sensitive workspace path |
| S3 bucket name collision | Bucket name must be globally unique | Suffix with your 12-digit AWS account ID |
| `exit_code: 1` but HTTP 200 | Task failed inside the sandbox | Check `task_result.stderr` in the response — non-zero exit is a task result, not an API error |
