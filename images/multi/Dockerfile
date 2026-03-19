# sandboxshift/runtime-multi:latest
# -----------------------------------------------------------------------
# Combined Python 3.11 + Node 20 sandbox runtime for SandboxShift.
# Used when _detect_image() finds multiple workspace markers.
# (Decision #19 — multiple markers → runtime-multi)
#
# Base: cgr.dev/chainguard/wolfi-base
#   Wolfi is the upstream distro for all Chainguard images.
#   Using wolfi-base + apk installs both runtimes without fragile
#   cross-image COPY --from operations.
#
#   python-3.11 and nodejs-20 are the pinned APK package names in Wolfi.
#   busybox is required so /bin/sh is available for PodmanRuntime's
#   `podman run ... /bin/sh -c <task>` invocation.
#
# Security properties:
#   Layer 1: Wolfi/Chainguard base — zero CVEs, SBOM-signed
#   Layer 2: Non-root UID 65532 — matches _NONROOT_USER in podman.py
#
# V2 note: multi-stage strip of apk from the final layer is planned.
#   Network egress is allowlist-controlled by PodmanRuntime (Decision #18),
#   so apk cannot reach the internet unless the operator explicitly allows it.
# -----------------------------------------------------------------------

FROM cgr.dev/chainguard/wolfi-base

LABEL org.opencontainers.image.title="sandboxshift/runtime-multi" \
      org.opencontainers.image.version="latest" \
      org.opencontainers.image.description="SandboxShift multi-runtime (Python 3.11 + Node 20)" \
      org.opencontainers.image.vendor="SandboxShift"

# Install both runtimes + busybox shell (provides /bin/sh for PodmanRuntime) + uv.
RUN apk add --no-cache \
        python-3.11 \
        nodejs-20 \
        busybox \
        uv \
    && rm -rf /var/cache/apk/*

WORKDIR /workspace

# Chainguard nonroot UID — matches _NONROOT_USER = "65532:65532" in podman.py.
USER 65532:65532
