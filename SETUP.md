# SandboxShift — Agent Setup Guide

Complete setup from zero to running agents in VS Code.
Follow every step in order. Takes about 15 minutes.

---

## Prerequisites

- VS Code **1.96 or later** — [download](https://code.visualstudio.com/)
- GitHub account with **Copilot Pro** subscription
- Git installed
- Node.js 18+ installed (needed for MCP servers)

---

## Step 1 — Install VS Code Extensions

Open VS Code, go to Extensions (`Ctrl+Shift+X`) and install:

| Extension | Publisher | Why |
|-----------|-----------|-----|
| GitHub Copilot | GitHub | Core AI |
| GitHub Copilot Chat | GitHub | Agent mode UI |
| GitHub Pull Requests | GitHub | For cloud agent later |

Verify Copilot is working:
- Bottom status bar should show the Copilot icon (not crossed out)
- Sign in with your GitHub account if prompted

---

## Step 2 — Enable Agent Mode

1. Open VS Code Settings (`Ctrl+,`)
2. Search: `chat.agent.enabled`
3. Set to `true`

Then verify:
1. Open Copilot Chat (`Ctrl+Shift+I`)
2. At the bottom of the chat panel, click the mode dropdown
3. You should see **"Agent"** as an option

---

## Step 3 — Clone The Repo

```bash
git clone https://github.com/YOUR_USERNAME/sandboxshift.git
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

If you don't see them yet — close and reopen VS Code, then check again.

---

## Step 4 — Install context7 MCP Server

The Planner and Coder agents use context7 to look up live documentation.
Without this, they will fail when trying to verify library APIs.

```bash
# Install context7 MCP globally
npm install -g @context7/mcp-server
```

Now register it in VS Code:

1. Open VS Code Settings (`Ctrl+,`)
2. Search: `mcp`
3. Click **"Edit in settings.json"**
4. Add this inside the JSON:

```json
{
  "mcp": {
    "servers": {
      "context7": {
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp@latest"]
      }
    }
  }
}
```

5. Save the file
6. Restart VS Code

Verify context7 is working:
1. Open Copilot Chat in agent mode
2. Type: `#context7 what is the latest fastapi version?`
3. It should return live documentation — not a cached answer

---

## Step 5 — Configure GitHub MCP Server

The Orchestrator uses GitHub MCP to create issues when it gets blocked
and you're away. This is what sends you email notifications.

```bash
# Install GitHub MCP server
npm install -g @modelcontextprotocol/server-github
```

You need a GitHub Personal Access Token:

1. Go to GitHub → Settings → Developer Settings → Personal Access Tokens → Fine-grained tokens
2. Click **"Generate new token"**
3. Set:
   - **Token name:** `sandboxshift-agents`
   - **Expiration:** 90 days
   - **Repository access:** Only select `sandboxshift`
   - **Permissions:**
     - Issues: Read and Write
     - Pull requests: Read and Write
     - Contents: Read and Write
4. Click **"Generate token"**
5. Copy the token — you won't see it again

Now add it to VS Code settings.json (same file as before):

```json
{
  "mcp": {
    "servers": {
      "context7": {
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp@latest"]
      },
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_PERSONAL_ACCESS_TOKEN": "YOUR_TOKEN_HERE"
        }
      }
    }
  }
}
```

> ⚠️ **Security note:** This token only has access to the sandboxshift repo.
> Never commit settings.json — it's already in .gitignore.

Restart VS Code after saving.

---

## Step 6 — Configure GitHub Email Notifications

Make sure you get emailed when the Orchestrator creates a blocked issue:

1. Go to GitHub → Settings → Notifications
2. Under **"Participating and @mentions"** → enable Email
3. Under **"Issues"** → enable Email
4. Verify your email is confirmed in GitHub Settings → Emails

Now when the Orchestrator creates an issue labelled `needs-human-decision`,
you'll get an email. Reply to the email or comment on the issue — the agent
will pick up your reply and continue.

---

## Step 7 — Verify Everything Works

Run the test prompt from `TEST-AGENT.md` to verify the full chain works
before throwing real work at it.

```
Open Copilot Chat
→ Select "Orchestrator" from agent dropdown
→ Paste the test prompt from TEST-AGENT.md
→ Watch it run
```

---

## Troubleshooting

### Agents not showing in dropdown
- Confirm files are in `.github/agents/` (exact path)
- Confirm VS Code version is 1.96+
- Close and fully reopen VS Code
- Check the agent file frontmatter has `name:` and `model:` fields

### context7 not working
- Run `npx -y @upstash/context7-mcp@latest` manually to check for errors
- Make sure Node.js 18+ is installed: `node --version`

### GitHub MCP not creating issues
- Verify your token has Issues: Read and Write permission
- Check the token hasn't expired
- Test manually: open Copilot Chat and type `@github list my repos`

### Agent mode not visible
- Search `chat.agent.enabled` in settings — must be `true`
- Make sure GitHub Copilot Chat extension (not just Copilot) is installed

---

## What Each MCP Server Does

| Server | Used By | Does |
|--------|---------|------|
| context7 | Planner, Coder | Fetches live library docs so agents don't use stale knowledge |
| github | Orchestrator | Creates blocked issues, reads issue replies, creates PRs |
