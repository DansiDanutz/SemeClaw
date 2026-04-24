---
id: adapter_claude_code
name: Claude Code Adapter
adapter_kind: external_cli
role: Bridge to the user's local Claude Code CLI
core: false
adapter:
  protocol: subprocess
  binary: claude
  detect_cmd: ["claude", "--version"]
  invoke_cmd: ["claude", "-p", "{prompt}", "--output-format", "json"]
  required_env: []                            # uses Claude Code's own auth
---

# Claude Code Adapter

## What it does
Lets a SemeClaw meeting room delegate complex coding tasks to the user's locally installed Claude Code CLI. The Coder Agent can hand off to it for anything that needs deep codebase exploration or multi-file edits.

## Setup
```bash
# Install Claude Code (one-time)
curl -fsSL https://claude.ai/install.sh | sh
claude /login
```
SemeClaw uses your existing Claude Code authentication — no extra API key needed.

## Detection
The adapter shells out to `claude --version` at startup. If found, it registers a `claude_code:do` agent that the orchestrator can route to.

## Invocation
```
claude -p "<prompt>" --output-format json --add-dir <project_path>
```
Output is parsed and streamed into the meeting timeline as the Coder Agent's response.

## When to use
- Multi-file refactors the local Coder Agent can't keep in context
- Codebase Q&A on the user's actual repo
- Long-running implementation tasks with file edits

## Failure mode
If `claude` is not on PATH or auth has expired, the adapter logs a warning and the meeting falls back to the local Coder Agent only.
