# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| main branch | ✅ Yes |
| older releases | ❌ No |

---

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

SandboxShift is a security-focused project. Responsible disclosure is important
to us and we take all reports seriously.

### How To Report

1. Go to the [Security tab](../../security/advisories) of this repository
2. Click **"Report a vulnerability"**
3. Fill in the details — the more information the better:
   - Which of the 7 security layers is affected
   - Steps to reproduce
   - Potential impact
   - Suggested fix if you have one

### What To Expect

- **Acknowledgement** within 48 hours
- **Status update** within 7 days
- **Fix timeline** communicated as soon as the issue is understood
- **Credit** in the release notes if you want it

### Scope

We are particularly interested in vulnerabilities that allow:
- Sandbox escape (agent reading files outside the mounted workspace)
- Sensitive data leaking to cloud mode
- Privilege escalation inside the container
- Audit trail tampering
- Network policy bypass

### Out Of Scope

- Vulnerabilities requiring physical access to the machine
- Social engineering attacks
- Issues in third-party dependencies (report those upstream)

---

## Security Design

SandboxShift uses a 7-layer defence-in-depth security model.
See [AGENTS.md](AGENTS.md) for the full security architecture.
