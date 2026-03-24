# Usage Guide

Use SandboxShift to run tasks locally in a sandbox or automatically burst to your AWS account when local resources are low.

---

## Quick start

```bash
# Run a Python task locally in a sandbox
sandboxshift run /path/to/your/project "pytest tests/"

# Run a Node.js server locally
sandboxshift run /path/to/node-app "node index.js" --port 3000

# Force a cloud run in AWS Fargate
sandboxshift run /path/to/project "python main.py" --mode cloud

# Run a cloud server and stop it later
sandboxshift run /path/to/node-app "node index.js" --port 3000 --mode cloud
sandboxshift stop <instance_id>
```

---

## CLI reference

```bash
sandboxshift run <workspace> <task> [options]
```

`<workspace>` is the directory to mount. If `sandboxshift.yaml` exists there, it is loaded automatically.

Example:

```bash
sandboxshift run /path/to/my-project "node index.js"
                 └── loads /path/to/my-project/sandboxshift.yaml if present
```

Use `sandboxshift.yaml` to set per-project defaults like mode, timeout, ports, env vars, network rules, and resource limits.
See [configuration.md](configuration.md) for the full YAML reference.

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--mode MODE` | `auto` | `local`, `cloud`, or `auto`. `auto` decides based on available RAM. Sensitive workspaces still force local. |
| `--port PORT` | — | Expose a port. Accepts `3000` or `HOST:CONTAINER` like `8080:3000`. Repeat for multiple ports. |
| `--env KEY=VAL` | — | Inject an environment variable into the container. Repeat for multiple. CLI replaces YAML `env:` when set. |
| `--allow FQDN` | — | Local only. Allow outbound access to a domain. Repeat for multiple. Use `"*"` for unrestricted local egress. |
| `--setup CMD` | — | Command to run before the main task. |
| `--timeout N` | `1800` | Kill the sandbox after N seconds. |
| `--memory-mb N` | `512` | Container memory limit in MB. |
| `--cpu N` | `1.0` | Container CPU limit. |
| `--ram-threshold N` | `1024` | In `auto` mode, burst to cloud if available RAM drops below N MB. |
| `--skip-sensitivity-check` | `false` | Skip sensitive data scanning. |
| `--allow-file FILENAME` | — | Cloud only. Allow a sensitive filename like `.env` to upload to S3. Repeat for multiple. |
| `--audit-log PATH` | `~/.sandboxshift/audit.log` | Override the audit log location. |

---

## Inject environment variables

Use `--env` when your app or package manager expects environment variables inside the sandbox.

```bash
sandboxshift run . "yarn start" --mode cloud \
  --env COMPONENTS_UI_NPM_TOKEN=ghp_xxx \
  --env NODE_ENV=production
```

This works in both local Podman sandboxes and cloud Fargate sandboxes.

---

## Upload sensitive files to cloud

By default, SandboxShift blocks filenames like `.env`, `.pem`, and `.key` from being uploaded during cloud runs.

If you own and trust the file, allow it explicitly:

```bash
sandboxshift run . "node dist/main.js" --mode cloud \
  --allow-file .env \
  --allow-file .env.staging
```

The filename must match exactly.

---

## Control local vs cloud

| Intent | Command |
|--------|--------|
| Auto (default) | `sandboxshift run /workspace "task"` |
| Always local | `sandboxshift run /workspace "task" --mode local` |
| Always cloud | `sandboxshift run /workspace "task" --mode cloud` |
| Fine-grained threshold | `sandboxshift run /workspace "task" --ram-threshold 8192` |

> `--mode cloud` never overrides sensitive-data detection. Sensitive workspaces still run locally.

---

## Other commands

```bash
# List running cloud server tasks
sandboxshift list

# Stop a running cloud server task
sandboxshift stop <instance_id>

# View recent audit entries
sandboxshift audit tail
sandboxshift audit tail -n 50
sandboxshift audit tail --audit-log /tmp/my-audit.log
```

---

## Related guides

- [installation.md](installation.md) — local setup and AWS/Terraform setup
- [configuration.md](configuration.md) — `sandboxshift.yaml` and CLI precedence
- [getting-started.md](getting-started.md) — first run walkthrough
