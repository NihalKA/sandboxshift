---
name: 🐛 Bug Report
about: Something is broken or not working as expected
title: "🐛 [BUG]: "
labels: bug, needs-triage
assignees: ''
---

## Describe The Bug
<!-- A clear description of what the bug is -->

## To Reproduce
```bash
# Exact commands you ran
sandboxshift run --task "..." --workspace ./...
```

## Expected Behaviour
<!-- What you expected to happen -->

## Actual Behaviour
<!-- What actually happened — include full error output -->

```
paste error output here
```

## Environment

| Item | Value |
|------|-------|
| SandboxShift version | |
| OS | |
| Python version | |
| Podman version | |
| Mode (local/cloud/auto) | |

## Audit Log
<!-- If available, paste the relevant section of .sandboxshift/audit.log -->

```json

```

## Which Security Layer Is Affected? (if applicable)
- [ ] Layer 1: Chainguard base image
- [ ] Layer 2: Podman rootless
- [ ] Layer 3: gVisor
- [ ] Layer 4: Network policy
- [ ] Layer 5: Resource limits
- [ ] Layer 6: Sensitive data detection
- [ ] Layer 7: Audit trail
- [ ] Not security related

> ⚠️ If this is a **security vulnerability**, please do NOT use this template.
> See [SECURITY.md](../../SECURITY.md) for responsible disclosure.
