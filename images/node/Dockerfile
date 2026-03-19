# sandboxshift/runtime-node:20
# -----------------------------------------------------------------------
# Chainguard Node 20 sandbox runtime for SandboxShift.
#
# Same security rationale as runtime-python — see images/python/Dockerfile.
# :latest-dev is required for /bin/sh (BusyBox).
# Selected when workspace contains package.json only (no requirements.txt).
# -----------------------------------------------------------------------

FROM cgr.dev/chainguard/node:latest-dev

LABEL org.opencontainers.image.title="sandboxshift/runtime-node" \
      org.opencontainers.image.version="20" \
      org.opencontainers.image.description="SandboxShift Node 20 sandbox runtime" \
      org.opencontainers.image.vendor="SandboxShift"

WORKDIR /workspace

# Install uv via Wolfi APK (pip not available in the node image).
# Available to UID 65532 at runtime for dependency installation.
RUN apk add --no-cache uv \
    && rm -rf /var/cache/apk/*

# UID 65532 — Chainguard nonroot; matches _NONROOT_USER in podman.py.
USER 65532:65532
