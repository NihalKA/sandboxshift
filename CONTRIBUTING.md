# Contributing to SandboxShift

Thank you for your interest in contributing! SandboxShift is an open source
project and we welcome contributions of all kinds.

---

## Before You Start

- Read [README.md](README.md) to understand the project
- Read [AGENTS.md](AGENTS.md) to understand the architecture and all decisions made
- Check [existing issues](../../issues) to avoid duplicate work
- For large changes, open an issue first to discuss before writing code

---

## Ways To Contribute

- **Bug fixes** — fix something broken
- **New language runtimes** — add Ruby, PHP, .NET support
- **Sensitive data patterns** — improve detection coverage
- **Documentation** — improve clarity, fix typos, add examples
- **Tests** — improve coverage
- **Platform support** — Windows, macOS improvements

---

## Development Setup

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/sandboxshift.git
cd sandboxshift

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --cov=src --cov-report=term-missing

# Run linting
ruff check src/
mypy src/
```

---

## Pull Request Process

1. **Fork** the repo and create a branch from `main`
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Write code** following the coding principles in [AGENTS.md](AGENTS.md)

3. **Write tests** — coverage must not drop below 80%

4. **Run the full check locally before pushing:**
   ```bash
   pytest tests/ -v --cov=src --cov-report=term-missing
   ruff check src/
   mypy src/
   ```

5. **Commit with clear messages:**
   ```
   feat: add Ruby runtime support
   fix: sensitivity scanner misses multiline private keys
   docs: improve getting started guide
   test: add edge cases for BurstEngine RAM threshold
   ```

6. **Open a PR** — fill in the PR template completely

7. **Address review feedback** — maintainers will review within a few days

---

## Coding Standards

These are non-negotiable — the CI will fail if they're not met:

- Type hints on every function
- Docstrings on every function and class
- Functions under 50 lines
- No hardcoded credentials, regions, or account IDs
- No silent failures — all errors must be explicit
- Tests for every new function (happy path + failure case)

---

## Security Contributions

**Never** open a public issue for a security vulnerability.
See [SECURITY.md](SECURITY.md) for responsible disclosure.

If your contribution touches the 7 security layers, extra scrutiny applies.
Any PR that weakens a security layer will be rejected regardless of other merit.

---

## Commit Message Format

```
type: short description (under 72 chars)

Optional longer explanation if needed.
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `security`

---

## Questions?

Open a [Discussion](../../discussions) — not an issue — for questions about
how something works or whether a contribution would be welcome.
