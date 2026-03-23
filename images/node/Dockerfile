# sandboxshift/runtime-node:20
# -----------------------------------------------------------------------
# Node 20 sandbox runtime for SandboxShift.
#
# Base: node:20-slim (Docker Hub official)
#   - Always publicly available, no auth required
#   - Pinned to Node 20 LTS
#   - Debian slim: has /bin/sh, npm
#
# Security properties:
#   Layer 2: Non-root user (sandboxshift, UID 10000)
# -----------------------------------------------------------------------

FROM node:20-slim

LABEL org.opencontainers.image.title="sandboxshift/runtime-node" \
      org.opencontainers.image.version="20" \
      org.opencontainers.image.description="SandboxShift Node 20 sandbox runtime" \
      org.opencontainers.image.vendor="SandboxShift"

WORKDIR /workspace

# Install python3 + pip for boto3 S3 workspace download bootstrap.
# --break-system-packages is required on Debian bookworm (PEP 668) when
# installing pip packages system-wide inside a container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && pip3 install --no-cache-dir --break-system-packages boto3 \
 && rm -rf /var/lib/apt/lists/*

# Enable corepack so yarn and pnpm are available without a separate install.
# corepack ships with Node 20 — this is a zero-dependency unlock.
# Must run as root (before USER switch) since it writes to /usr/local/bin.
RUN corepack enable

# Create a non-root user for runtime execution.
# Home is /home/sandboxshift (NOT /workspace) so that npm/pip user-installs
# and other tools that write to ~ still work when workspace is mounted :ro.
# chown /workspace so the sandboxshift user can write files during S3 bootstrap.
RUN groupadd -g 10000 sandboxshift \
 && useradd -u 10000 -g 10000 -s /bin/sh -d /home/sandboxshift sandboxshift \
 && mkdir -p /home/sandboxshift \
 && chown 10000:10000 /home/sandboxshift \
 && chown 10000:10000 /workspace

USER 10000:10000

# Set HOME explicitly so npm, pip and other tools that write to ~ use the
# correct home directory. Without this, $HOME may default to /tmp and user
# installs land in /tmp/.local/bin which is not on PATH.
# PATH is extended so scripts installed via 'npm install' or 'pip install -r'
# (e.g. via npx, or pip --user) are executable without a prefix.
ENV HOME=/home/sandboxshift
ENV PATH=/home/sandboxshift/.local/bin:$PATH
