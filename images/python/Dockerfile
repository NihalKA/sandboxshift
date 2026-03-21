# sandboxshift/runtime-python:3.11
# -----------------------------------------------------------------------
# Python 3.11 sandbox runtime for SandboxShift.
#
# Base: python:3.11-slim (Docker Hub official)
#   - Always publicly available, no auth required
#   - Pinned to Python 3.11 (not floating latest)
#   - Debian slim: ~50MB, has /bin/sh, pip, apt
#
# Security properties:
#   Layer 2: Non-root user (sandboxshift, UID 10000)
# -----------------------------------------------------------------------

FROM python:3.11-slim

LABEL org.opencontainers.image.title="sandboxshift/runtime-python" \
      org.opencontainers.image.version="3.11" \
      org.opencontainers.image.description="SandboxShift Python 3.11 sandbox runtime" \
      org.opencontainers.image.vendor="SandboxShift"

# /workspace is the host workspace mount point used by PodmanRuntime
# (--volume {workspace}:/workspace --workdir /workspace)
WORKDIR /workspace

# Install boto3 for S3 workspace download bootstrap (used in Fargate mode).
# Install uv package manager for fast Python dependency installation.
RUN pip install --no-cache-dir boto3 uv

# Create a non-root user for runtime execution.
# UID 10000 is used consistently across all SandboxShift runtime images.
RUN groupadd -g 10000 sandboxshift \
 && useradd -u 10000 -g 10000 -s /bin/sh -d /workspace sandboxshift

USER 10000:10000
