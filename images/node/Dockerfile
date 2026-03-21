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

# Create a non-root user for runtime execution.
# chown /workspace so the sandboxshift user can write files during S3 bootstrap.
RUN groupadd -g 10000 sandboxshift \
 && useradd -u 10000 -g 10000 -s /bin/sh -d /workspace sandboxshift \
 && chown 10000:10000 /workspace

USER 10000:10000
