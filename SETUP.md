# SandboxShift — Agent Setup Guide

Complete setup from zero to running agents in VS Code.
Follow every step in order. Takes about 15 minutes.

---

## Prerequisites

- VS Code **1.96 or later** — [download](https://code.visualstudio.com/)
- GitHub account with **Copilot Pro** subscription
- Git installed

---

## Step 1 — Install VS Code Extensions

Open VS Code, go to Extensions (`Cmd+Shift+X` on Mac, `Ctrl+Shift+X` on Windows) and install:

| Extension | Publisher | Why |
|-----------|-----------|-----|
| GitHub Copilot Chat | GitHub | Agent mode UI + core AI (includes everything) |
| GitHub Pull Requests | GitHub | For cloud agent later |

> **Note:** You only need **GitHub Copilot Chat** — it now includes everything.
> There is no separate "GitHub Copilot" extension required.

Verify Copilot is working:
- Bottom status bar should show the Copilot icon (not crossed out)
- Sign in with your GitHub account if prompted

---

## Step 2 — Enable Agent Mode

1. Open VS Code Settings (`Cmd+,` on Mac, `Ctrl+,` on Windows)
2. Search: `chat.agent.enabled`
3. Set to `true`

Then verify:
1. Open Copilot Chat (`Cmd+Shift+I` on Mac, `Ctrl+Shift+I` on Windows)
2. At the bottom of the chat panel, click the mode dropdown
3. You should see **"Agent"** as an option

---

## Step 3 — Clone The Repo

```bash
git clone https://github.com/NihalKA/sandboxshift.git
cd sandboxshift
code .
```

After VS Code opens the repo, open Copilot Chat and click the agent dropdown.
You should now see your custom agents listed:

```
✦ Copilot (default)
🎯 Orchestrator
📋 Planner
💻 Coder
🔍 Reviewer
📝 Docs
🔒 Security
```

If you don't see them — close and reopen VS Code, then check again.

---

## Step 4 — Configure MCP Servers

Both MCP servers use hosted HTTP endpoints.
No npm installs, no API keys, no tokens required.
GitHub authenticates via your existing Copilot OAuth login automatically.

### Option A — MCP User Config File (Recommended)

VS Code has a dedicated MCP config file separate from settings.json.

Open it:
```
Cmd+Shift+P → type "MCP: Open User Configuration" → Enter
```

This opens a file at:
```
~/Library/Application Support/Code/User/mcp.json
```

Paste this:

```json
{
  "servers": {
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp/"
    },
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp"
    }
  },
  "inputs": []
}
```

Save the file.

### Start The Servers

After saving, VS Code shows inline buttons above each server:

```
"github":   ▷ Start | 40 tools | 2 prompts | More...
"context7": ▷ Start | 2 tools | More...
```

Click **▷ Start** on each server.

- **GitHub** will trigger an OAuth popup — sign in with your GitHub account.
  No token needed. It uses your Copilot login.
- **context7** starts immediately — no auth required.

Once started you'll see:
```
"github":   ✓ Running | Stop | Restart | 40 tools | 2 prompts
"context7": ✓ Running | Stop | Restart | 2 tools
```

Both running = you're ready. ✅

### Option B — Via settings.json (Alternative)

If Option A doesn't work, add to `settings.json` instead:
```
Cmd+Shift+P → "Open User Settings JSON"
```

```json
{
  "mcp": {
    "servers": {
      "github": {
        "type": "http",
        "url": "https://api.githubcopilot.com/mcp/"
      },
      "context7": {
        "type": "http",
        "url": "https://mcp.context7.com/mcp"
      }
    }
  }
}
```

### What Each Server Does

| Server | Used By | Does |
|--------|---------|------|
| context7 | Planner, Coder | Fetches live library docs — agents never use stale knowledge |
| github | Orchestrator | Creates blocked issues when you're away, OAuth via Copilot login |

---

## Step 5 — Configure GitHub Email Notifications

This is what notifies you on your phone when an agent is blocked
and needs your input while your screen is locked.

### Correct Location

Go to your **personal** GitHub settings — not the repo settings:

```
github.com → click your profile picture (top right)
→ Settings
→ Notifications (left sidebar)
```

Direct URL: **https://github.com/settings/notifications**

### What To Check

You need three things enabled:

**1. Watching → Notify me: Email**
```
Subscriptions section
→ Watching
→ Notify me: Email ✅
```

**2. Participating, @mentions and custom → Notify me: Email**
```
Subscriptions section
→ Participating, @mentions and custom
→ Notify me: Email ✅
```

**3. Customize email updates → make sure Issues is checked**
```
→ Click the "Reviews, Pushes, Comments" dropdown
→ Make sure Issues is checked ✅
```

This third step ensures you get emailed when someone (or an agent)
comments on an issue — not just when it's first created.

### How It Works

When the Orchestrator creates a blocked issue:
- Issue is created with label `needs-human-decision`
- You get an email to `er.nihalka@gmail.com` immediately
- Reply to the email OR open the issue and comment directly
- The agent picks up your reply and continues automatically

---

## Step 6 — Verify Everything Works

Run the tests from `TEST-AGENT.md` before starting any real work.

```
Open Copilot Chat
→ Select "Orchestrator" from agent dropdown
→ Follow TEST-AGENT.md step by step
```

---

## Troubleshooting

### Agents not showing in dropdown
- Confirm files are in `.github/agents/` — exact path matters
- Confirm VS Code version is 1.96+
- Close and fully reopen VS Code
- Check each agent file has `name:` and `model:` in the frontmatter

### context7 not working
- Try opening Copilot Chat in agent mode and typing: `#context7 latest fastapi version`
- If it fails, fall back to npx approach — add this to settings.json instead:
  ```json
  "context7": {
    "command": "npx",
    "args": ["-y", "@upstash/context7-mcp@latest"]
  }
  ```
  This requires Node.js 18+: verify with `node --version`

### GitHub MCP not creating issues
- GitHub MCP authenticates via your Copilot login automatically
- If it fails, test with: open Copilot Chat and ask `@github list my repos`
- Make sure you're signed into GitHub in VS Code

### Agent mode not visible
- Search `chat.agent.enabled` in settings — must be `true`
- Make sure GitHub Copilot Chat extension is installed and you're signed in
