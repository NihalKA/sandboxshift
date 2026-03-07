## Summary
<!-- What does this PR do? One paragraph. -->

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Test improvement
- [ ] Refactor (no behaviour change)
- [ ] Security fix

## Related Issue
<!-- Closes #XXX -->

---

## Checklist

### Code Quality
- [ ] Type hints on all new functions
- [ ] Docstrings on all new functions and classes
- [ ] No function exceeds 50 lines
- [ ] No hardcoded credentials, regions, or account IDs
- [ ] No silent failures — all errors are explicit

### Tests
- [ ] Tests written for all new code
- [ ] Both happy path and failure cases covered
- [ ] No real AWS calls in tests (mocks used)
- [ ] Coverage is >= 80% (`pytest --cov=src`)
- [ ] All existing tests still pass

### Security (complete if this PR touches src/, images/, or terraform/)
- [ ] Layer 1: Chainguard base image unchanged
- [ ] Layer 2: Podman still rootless, no --privileged
- [ ] Layer 3: gVisor not bypassed
- [ ] Layer 4: No new wildcard network rules
- [ ] Layer 5: Resource limits still enforced
- [ ] Layer 6: Sensitive data detection not bypassed
- [ ] Layer 7: Audit trail intact

### Documentation
- [ ] README updated if user-facing behaviour changed
- [ ] Relevant docs/ files updated
- [ ] ADR written if this is an architectural decision

---

## How To Test This PR
<!-- Step by step instructions for the reviewer to test your changes -->

```bash
# commands to test
```

## Screenshots / Output (if applicable)
