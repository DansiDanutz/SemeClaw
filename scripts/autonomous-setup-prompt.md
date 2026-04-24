# Autonomous setup prompt

Just cloned SemeClaw and want an AI agent to set it up for you?
**Open the repo in Claude Code, Codex, Cursor, or Aider, then paste the prompt below.**

The agent will read [`AGENTS.md`](../AGENTS.md), run `semeclaw doctor --json`, fix what it can, and ask you only about choices that need a human (e.g. which API keys to add).

---

## Copy-paste prompt

```
You are setting up SemeClaw, a freshly-cloned open-source War Room.

Read AGENTS.md at the repo root. It is the spec. Follow it.

Constraints:
- Never push to git, deploy to Fly, or deploy to Vercel without my explicit OK.
- Never commit secrets. .env stays local; user keys go in ~/.semeclaw/env.
- The system MUST work without any API keys (free fallbacks exist for everything).
- Use `semeclaw doctor --json` for every diagnostic — parse the JSON, do not eyeball.

Goal: get me from `git clone` to a passing `semeclaw doctor` in the
fewest steps, asking me only when a real choice is needed.

When you finish, give me a single 4-line report:
  Status:  green | partial | blocked
  Worked:  what you set up
  Skipped: what you intentionally did not (and why)
  Next:    one command for me to run
```

---

## Why a separate prompt?

`AGENTS.md` is the **spec** (stable, opinionated, machine-readable).
This file is the **invocation** (a tiny user-facing wrapper that any
coding agent can act on without further context).

If you change the setup flow, change `AGENTS.md`. This prompt rarely needs to change.
