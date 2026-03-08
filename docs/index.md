# SandboxShift — Documentation

> Self-hosted AI agent sandbox with automatic local/cloud bursting.  
> Your code runs locally or on **your own AWS**. Your data never touches anyone else's servers.

---

## What Is SandboxShift?

SandboxShift provides secure, isolated execution environments for AI agents. When your local machine has enough resources the sandbox runs there. When it doesn't, it automatically bursts to your own AWS Fargate — never to a third-party cloud.

For a full introduction see the [README](../README.md).

---

## Components

| Component | Layer | Description |
|-----------|-------|-------------|
| [SensitivityScanner](components/sensitivity-scanner.md) | Security Layer 6 of 7 | Detects sensitive files and secret patterns in the workspace before any cloud decision is made. Forces local execution when secrets are found. |

> More components will be documented here as they are implemented.

---

## Getting Started

See the [README](../README.md) for installation instructions, configuration reference (`sandboxshift.yaml`), and the quickstart guide.
