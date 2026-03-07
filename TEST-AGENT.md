# SandboxShift — Agent Test Guide

Run these tests after completing SETUP.md.
Tests go from simple → full chain. Stop if any test fails and fix before continuing.

---

## Test 1 — Verify Agents Are Visible

**What to check:** Custom agents appear in the Copilot Chat dropdown.

1. Open Copilot Chat (`Ctrl+Shift+I`)
2. Click the agent/model dropdown at the bottom
3. Confirm you see all 6 custom agents:

```
✅ Orchestrator
✅ Planner
✅ Coder
✅ Reviewer
✅ Docs
✅ Security
```

**If missing:** See Troubleshooting in SETUP.md.

---

## Test 2 — Verify context7 MCP

**What to check:** Planner/Coder can fetch live docs.

1. Open Copilot Chat in **Agent mode** (not Ask mode)
2. Select **Planner** from dropdown
3. Type:

```
Use context7 to look up the current version of FastAPI and tell me what the latest release is.
```

**Expected:** Returns the actual current FastAPI version from live docs, not a guess.

**If it fails:** Re-check Step 4 of SETUP.md.

---

## Test 3 — Verify GitHub MCP

**What to check:** Orchestrator can create a GitHub issue.

1. Select **Orchestrator** from dropdown
2. Type:

```
Create a test GitHub issue in the sandboxshift repo titled
"✅ TEST: Agent GitHub access verified" with body
"This issue confirms the Orchestrator can create GitHub issues.
Safe to close." and label it "test".
```

**Expected:**
- Issue appears in your GitHub repo
- You receive an email notification from GitHub

**Then:** Close the issue manually on GitHub.

**If it fails:** Re-check Step 5 of SETUP.md — token permissions.

---

## Test 4 — Verify Full Agent Chain (Dry Run)

**What to check:** Orchestrator → Planner → Coder chain works end to end.

1. Select **Orchestrator** from dropdown
2. Paste this exact prompt:

```
Read AGENTS.md to understand the project context.

Then do a DRY RUN ONLY of building the BurstEngine component:
- Call Planner to create an implementation plan
- Show me the execution plan (phases and tasks)
- Do NOT call Coder yet — stop after showing the plan
- Tell me what files would be created

This is a test to verify the agent chain is working correctly.
```

**Expected output:**
```
[Orchestrator reads AGENTS.md]
[Orchestrator calls Planner]
[Planner researches codebase, returns plan]

## Execution Plan

### Phase 1: Design
- Write ADR-002 for BurstEngine → Planner

### Phase 2: Implementation
- Task 2.1: src/sandbox/burst/engine.py → Coder
- Task 2.2: tests/sandbox/test_engine.py → Coder

### Phase 3: Review
- Review burst/engine.py → Reviewer

### Phase 4: Security
- Scan for layer violations → Security

### Phase 5: Docs
- Document BurstEngine → Docs

Files that would be created:
- architecture/decisions/ADR-002-burst-engine.md
- src/sandbox/burst/engine.py
- tests/sandbox/test_engine.py
- docs/ (section update)

Ready to proceed? Reply "go" to start implementation.
```

**If the chain worked:** You're ready. Proceed to Test 5.

**If Orchestrator didn't call Planner:** The `agent` tool isn't working.
Check that VS Code agent mode is fully enabled.

---

## Test 5 — Verify Blocked Issue Flow

**What to check:** When Orchestrator hits something missing, it creates an issue instead of guessing.

1. Select **Orchestrator** from dropdown
2. Type:

```
Read AGENTS.md. I want to add a feature where SandboxShift
sends a Slack notification when a sandbox finishes.
Plan this out but do not implement anything.
```

**Expected:** Orchestrator should recognise this is NOT in V1 scope (AGENTS.md defines V1 clearly) and either:
- Tell you directly in chat: "This is a V2 feature, not in V1 scope. Do you want to add it to the roadmap or proceed with V1 work?"
- OR create a GitHub issue asking for clarification

**What you're testing:** Does the Orchestrator read AGENTS.md properly and respect V1 scope boundaries?

---

## All Tests Passed?

```
✅ Test 1 — Agents visible in dropdown
✅ Test 2 — context7 fetches live docs
✅ Test 3 — GitHub issue created + email received
✅ Test 4 — Full chain dry run worked
✅ Test 5 — Orchestrator respects V1 scope
```

**You're ready to build SandboxShift for real.**

Start with:

```
Select Orchestrator → type:

"Read AGENTS.md and ADR-001.
Build the first V1 component: SensitivityScanner.
This is the sensitive data detection layer (Layer 6).
Follow the implementation order defined in ADR-001."
```

Then lock your screen. Come back when you get an email — or when it's done.

---

## Quick Reference — What Each Agent Does

| Agent | Model | Role | You talk to it? |
|-------|-------|------|-----------------|
| Orchestrator | Claude Sonnet | Breaks tasks, delegates, creates blocked issues | ✅ YES — always |
| Planner | GPT-4o | Creates implementation plans | ❌ Never directly |
| Coder | Claude Sonnet | Writes code + tests | ❌ Never directly |
| Reviewer | Claude Sonnet | Security + quality gate | ❌ Never directly |
| Docs | Gemini Flash | Writes all documentation | ❌ Never directly |
| Security | Claude Sonnet | CVE scans, 7-layer verification | ❌ Never directly |
