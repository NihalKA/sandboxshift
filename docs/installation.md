# Installation

Install SandboxShift locally, then optionally enable cloud burst in your own AWS account.

For a first run walkthrough after install, see [getting-started.md](getting-started.md).

---

## Prerequisites

You install these. Everything else is handled by the setup script.

| Requirement | For | Install |
|-------------|-----|--------|
| Python 3.11+ | always | [python.org](https://python.org) |
| Podman (rootless) | always | [podman.io](https://podman.io/getting-started/installation) |
| AWS CLI v2 | cloud burst only | [AWS docs](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) |

**Not required:** Terraform, jq, or a global Python install for SandboxShift itself. The setup script manages those for you.

---

## Podman setup

### Linux

Rootless Podman usually works after install.

### macOS / Windows

Podman needs a lightweight VM before it can run containers:

```bash
podman machine init
podman machine start
podman info
```

To keep rootless mode:

```bash
podman machine set --rootful=false
```

For more details, see the [Podman Machine docs](https://docs.podman.io/en/latest/markdown/podman-machine.1.html).

---

## AWS credentials (cloud burst only)

Before running `./sandboxshift-setup.sh cloud`, authenticate the AWS CLI.

### Option A — default profile

```bash
aws configure
# AWS Access Key ID: AKIA...
# AWS Secret Access Key: ....
# Default region name: us-east-1
# Default output format: json
```

### Option B — named profile

```bash
export AWS_PROFILE=my-sandboxshift-profile
```

You can also use IAM Identity Center (SSO), environment variables, or an instance role. SandboxShift uses `boto3.Session()` and picks up AWS credentials from the normal AWS SDK chain.

---

## Install SandboxShift

```bash
git clone https://github.com/NihalKA/sandboxshift
cd sandboxshift
chmod +x sandboxshift-setup.sh

# Recommended — auto-detects: cloud if AWS credentials are present, local otherwise
./sandboxshift-setup.sh

# Or explicitly:
./sandboxshift-setup.sh local
./sandboxshift-setup.sh cloud
```

### What the setup script does

The setup script:

1. Downloads pinned **Terraform 1.5.7** into `~/.sandboxshift/bin/`
2. Creates an isolated Python venv at `~/.sandboxshift/venv/`
3. Installs SandboxShift into that venv
4. Symlinks the CLI to `~/.sandboxshift/bin/sandboxshift`
5. Builds local runtime images into Podman
6. In cloud mode, pushes the runtime image and provisions AWS resources

### Add the CLI to your PATH

```bash
echo 'export PATH="$HOME/.sandboxshift/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

After this, `sandboxshift` works in every terminal.

---

## Cloud burst setup (Terraform + AWS)

Run:

```bash
./sandboxshift-setup.sh cloud
```

This provisions cloud burst in **your AWS account**.

### What gets set up

- ECR repository for the SandboxShift runtime image
- ECS cluster for sandbox tasks
- S3 bucket for workspace staging
- IAM roles for task execution
- security groups for batch and server tasks
- CloudWatch log group for task logs
- `~/.sandboxshift/fargate.env` with the connection details the CLI needs

### What the script does in cloud mode

1. Verifies AWS credentials
2. Builds and pushes `runtime-multi` to ECR
3. Runs `terraform apply` in `terraform/fargate/`
4. Writes `~/.sandboxshift/fargate.env`
5. Makes future cloud runs work without manual `export` or `source`

### Re-running setup

Re-running `./sandboxshift-setup.sh cloud` is safe:

- cached Terraform is reused
- `terraform apply` is a no-op if nothing changed
- `fargate.env` is refreshed with the latest values

---

## Related guides

- [getting-started.md](getting-started.md) — first local run and first cloud run
- [usage.md](usage.md) — CLI examples and advanced usage
- [configuration.md](configuration.md) — `sandboxshift.yaml` reference
