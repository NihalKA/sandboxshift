# sandboxshift/runtime-python:3.11

Chainguard Python 3.11 sandbox runtime for SandboxShift.

## Base image

`cgr.dev/chainguard/python:latest-dev`

## When selected

Workspace contains `requirements.txt` (and no other runtime markers).

## Properties

| Property | Value |
|----------|-------|
| User | `65532:65532` (Chainguard nonroot) |
| Working directory | `/workspace` |
| Shell | `/bin/sh` (BusyBox, from `:latest-dev`) |
| Python | 3.11 (Chainguard package) |

## Build

```bash
make -C images build-python
# Equivalent: podman build --platform linux/amd64 -t sandboxshift/runtime-python:3.11 ./python
```

## Notes

- `pip` and `apk` are present (from `:latest-dev`) but network egress is controlled by PodmanRuntime's allowlist (Decision #18) — they are not documented user features
- V2: multi-stage build will strip `apk` from the final layer
