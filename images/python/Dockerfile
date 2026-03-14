# sandboxshift/runtime-python:3.11
# -----------------------------------------------------------------------
# Chainguard Python 3.11 sandbox runtime for SandboxShift.
#
# Security properties (AGENTS.md Security Architecture):
#   Layer 1: Chainguard base — zero known CVEs, SBOM-signed supply chain
#   Layer 2: Non-root user — UID 65532 (Chainguard nonroot)
#
# Why :latest-dev (not :latest):
#   PodmanRuntime unconditionally runs tasks as `/bin/sh -c <task>`.
#   The distroless :latest variant has no shell and would fail every task.
#   The :latest-dev variant adds BusyBox (/bin/sh) and apk. Network egress
#   is controlled by PodmanRuntime allowlist (Decision #18), not image contents.
# -----------------------------------------------------------------------

FROM cgr.dev/chainguard/python:latest-dev

LABEL org.opencontainers.image.title="sandboxshift/runtime-python" \
      org.opencontainers.image.version="3.11" \
      org.opencontainers.image.description="SandboxShift Python 3.11 sandbox runtime" \
      org.opencontainers.image.vendor="SandboxShift"

# /workspace is the host workspace mount point used by PodmanRuntime
# (--volume {workspace}:/workspace --workdir /workspace)
WORKDIR /workspace

# Chainguard nonroot — matches _NONROOT_USER = "65532:65532" in podman.py.
# PodmanRuntime also passes --user 65532:65532 explicitly; this is defence-in-depth.
USER 65532:65532

# No CMD/ENTRYPOINT — PodmanRuntime passes the full command:
#   podman run ... <image> /bin/sh -c <task>
