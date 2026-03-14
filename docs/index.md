# SandboxShift — Documentation

> Self-hosted AI agent sandbox with automatic local/cloud bursting.  
> Your code runs locally or on **your own AWS**. Your data never touches anyone else's servers.

---

## What Is SandboxShift?

SandboxShift provides secure, isolated execution environments for AI agents. When your local machine has enough resources the sandbox runs there. When it doesn't, it automatically bursts to your own AWS Fargate — never to a third-party cloud.

For a full introduction see the [README](../README.md).

---

## Guides

| Guide | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Install → first local run → cloud burst setup — full walkthrough |
| [Configuration Reference](configuration.md) | Full `sandboxshift.yaml` and environment variable reference |

---

## Components

| Component | Layer | Description |
|-----------|-------|-------------|
| [SensitivityScanner](components/sensitivity-scanner.md) | Security Layer 6 of 7 | Detects sensitive files and secret patterns in the workspace before any cloud decision is made. Forces local execution when secrets are found. |
| [BurstEngine](components/burst-engine.md) | Scheduling Step 2 of 6 | Decides whether to run a sandbox locally or burst to the user's own AWS Fargate. Consumes `SensitivityScanner`'s result and available system RAM. Sensitive workspaces unconditionally run local. |
| [PodmanRuntime](components/podman-runtime.md) | Runtime Step 3 of 6 | Executes agent tasks in rootless Podman containers on the local machine. Auto-detects Chainguard base images, enforces CPU/RAM/network limits, and produces a full audit trail for every sandbox lifecycle event. |
| [FargateRuntime](components/fargate-runtime.md) | Cloud sandbox via AWS Fargate | Executes agent tasks in AWS Fargate tasks in the user's own account. Uploads workspace to S3, polls ECS for completion, retrieves logs from CloudWatch. |
