# sandboxshift/runtime-multi:latest
# -----------------------------------------------------------------------
# Combined Python 3.11 + Node 20 sandbox runtime for SandboxShift.
# Used when _detect_image() finds multiple workspace markers.
# (Decision #19 — multiple markers -> runtime-multi)
#
# Base: python:3.11-slim (Docker Hub official)
#   - Always publicly available, no auth required
#   - Node 20 installed via NodeSource apt repository (official)
#
# Security properties:
#   Layer 2: Non-root user (sandboxshift, UID 10000)
# -----------------------------------------------------------------------

FROM python:3.11-slim

LABEL org.opencontainers.image.title="sandboxshift/runtime-multi" \
      org.opencontainers.image.version="latest" \
      org.opencontainers.image.description="SandboxShift multi-runtime (Python 3.11 + Node 20)" \
      org.opencontainers.image.vendor="SandboxShift"

# Install Node 20 via NodeSource official apt repo, then clean up.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get remove -y curl gnupg \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Install boto3 for S3 workspace download bootstrap (used in Fargate mode).
# Install uv for fast Python dependency management.
RUN pip install --no-cache-dir boto3 uv

WORKDIR /workspace

# Create a non-root user for runtime execution.
RUN groupadd -g 10000 sandboxshift \
 && useradd -u 10000 -g 10000 -s /bin/sh -d /workspace sandboxshift

USER 10000:10000
