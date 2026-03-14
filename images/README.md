# SandboxShift Runtime Images

Pre-built Chainguard-based container images for SandboxShift V1 sandboxes.

> **You don't need to write a Dockerfile.** SandboxShift auto-detects your workspace and selects the right image.

---

## Image Selection (Decision #19)

`PodmanRuntime._detect_image()` scans the workspace for marker files and picks the image automatically:

| Workspace contains | Image used |
|--------------------|----------|
| `requirements.txt` | `sandboxshift/runtime-python:3.11` |
| `package.json` | `sandboxshift/runtime-node:20` |
| Multiple markers | `sandboxshift/runtime-multi:latest` |
| None of the above | `sandboxshift/runtime-python:3.11` (default) |

---

## Why Chainguard?

All images are based on [Chainguard](https://chainguard.dev) / Wolfi images (Security Layer 1, AGENTS.md):
- Zero known CVEs
- SBOM-signed supply chain
- Rebuilt nightly
- Minimal attack surface

---

## Why `:latest-dev` for Python and Node?

`PodmanRuntime` unconditionally executes tasks as `/bin/sh -c <task>`.

Chainguard's distroless `:latest` variants have **no shell at all** — every task would fail immediately. The `:latest-dev` variants add BusyBox (providing `/bin/sh`) and `apk`.

Network egress is controlled by PodmanRuntime's allowlist (Decision #18), not by what's installed in the image. `apk` cannot reach package repositories unless the operator explicitly allows them in `sandboxshift.yaml`.

---

## Security Properties

Every image:
- Runs as **UID 65532** (`Chainguard nonroot`) — matches `_NONROOT_USER = "65532:65532"` in `podman.py` (Defence-in-depth: enforced both in image and by `--user` flag at runtime)
- Working directory: `/workspace` — the only mounted directory in V1
- No `CMD`/`ENTRYPOINT` — command is always supplied externally by `PodmanRuntime`
- No exposed ports or volumes in the Dockerfile

---

## Building Images

```bash
# Build all three images
make -C images build-all

# Build a specific image
make -C images build-python
make -C images build-node
make -C images build-multi

# Push to a registry (requires podman login first)
IMAGE_REGISTRY=123456789.dkr.ecr.us-east-1.amazonaws.com make -C images push-all
```

---

## V2 Roadmap

- Strip `apk` from the final layer using multi-stage builds (reduces attack surface)
- Add `sandboxshift/runtime-go:1.22`, `sandboxshift/runtime-java:21`, `sandboxshift/runtime-rust:latest`
- gVisor integration at the host runtime level (not image level)
