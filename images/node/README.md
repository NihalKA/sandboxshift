# sandboxshift/runtime-node:20

Chainguard Node 20 sandbox runtime for SandboxShift.

## Base image

`cgr.dev/chainguard/node:latest-dev`

## When selected

Workspace contains `package.json` only (no `requirements.txt` or other runtime markers).

## Properties

| Property | Value |
|----------|-------|
| User | `65532:65532` (Chainguard nonroot) |
| Working directory | `/workspace` |
| Shell | `/bin/sh` (BusyBox, from `:latest-dev`) |
| Node | 20 (Chainguard package) |

## Build

```bash
make -C images build-node
# Equivalent: podman build --platform linux/amd64 -t sandboxshift/runtime-node:20 ./node
```

## Notes

- `npm` and `apk` are present (from `:latest-dev`) but network egress is controlled by PodmanRuntime's allowlist — they are not documented user features
- V2: multi-stage build will strip `apk` from the final layer
