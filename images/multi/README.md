# sandboxshift/runtime-multi:latest

Combined Python 3.11 + Node 20 sandbox runtime for SandboxShift.

## Base image

`cgr.dev/chainguard/wolfi-base` + `apk add python-3.11 nodejs-20 busybox`

Wolfi is the upstream distro that all Chainguard images derive from. Using `wolfi-base` + `apk` installs both runtimes in one image without fragile cross-image `COPY --from` operations.

## When selected

`PodmanRuntime._detect_image()` finds multiple workspace markers (e.g., both `requirements.txt` and `package.json` are present). Decision #19.

This is the only `sandboxshift/`-prefixed image currently referenced directly by `podman.py`. It must be built and locally available before running SandboxShift against a multi-language workspace.

## Properties

| Property | Value |
|----------|-------|
| User | `65532:65532` (Chainguard nonroot) |
| Working directory | `/workspace` |
| Shell | `/bin/sh` (BusyBox) |
| Python | 3.11 (Wolfi APK package `python-3.11`) |
| Node | 20 (Wolfi APK package `nodejs-20`) |

## Build

```bash
make -C images build-multi
# Equivalent: podman build --platform linux/amd64 -t sandboxshift/runtime-multi:latest ./multi
```

## Notes

- `apk` remains in the final layer (only the cache is removed). Network egress is allowlist-controlled by PodmanRuntime (Decision #18), so `apk` cannot reach registries unless the operator explicitly allows them.
- V2: multi-stage strip of `apk` from the runtime layer is planned.
