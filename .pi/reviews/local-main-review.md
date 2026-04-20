# Local Review Bundle: main

No open PR was found for this branch. Use this bundle with `pi-company` and the `pr-review` skill.

## Repo Summary
- Repo: /Users/davidai/SemeClaw
- Branch: main

## Git Status
```text
M .github/workflows/ci.yml
 M Dockerfile
 M README.md
 M SEMECLAW_AGENT_PLAN.md
 M default_workspace/config.example.yaml
 M docs/ARCHITECTURE.md
 M skills/dexter.md
 M src/semeclaw/cli/onboard.py
 M src/semeclaw/core/agent.py
 M src/semeclaw/core/commands/handlers.py
 M src/semeclaw/core/commands/registry.py
 M src/semeclaw/provider/llm/base.py
 M src/semeclaw/tools/builtin_tools.py
 M src/semeclaw/utils/config.py
 M tests/test_api.py
D  war_room/audio/_test.mp3
D  war_room/audio/meetings/saved/75f6809d_quick-test_-what-is-nervix_-2026-04-17.mp3
D  war_room/audio/scripts/75f6809d_ro.json
D  war_room/builds/danslab-company-website-2026-04-18/index.html
 M war_room/dashboard/server.py
D  war_room/logs/auto_scheduler.jsonl
D  war_room/logs/dashboard-err.log
D  war_room/logs/dashboard.log
D  war_room/logs/run-2026-04-15.jsonl
D  war_room/logs/run-2026-04-17.jsonl
D  war_room/logs/run-2026-04-18.jsonl
 M war_room/memory.py
D  war_room/memory/memory.json
 M war_room/paperclip_bridge.py
D  war_room/research/[moltbot]-nervix:-npm-publish-nervix-cli-2026-04-18.md
D  war_room/research/pc-pc-9999.md
D  war_room/shared_state.json
 M war_room/tests/conftest.py
?? .pi/
?? .venv2/
?? AUDIT.md
?? conftest.py
?? src/semeclaw/integrations/
?? src/semeclaw/tools/github_tools.py
?? src/semeclaw/tools/pi_company_tools.py
?? telegram_bridge.py
?? tests/test_github_pr.py
?? tests/test_hardening.py
?? tests/test_llm_fallback.py
?? tests/test_onboard_fallbacks.py
?? tests/test_pi_company.py
```

## Diff Stat
```text
.github/workflows/ci.yml               |  23 ++++
 Dockerfile                             |   4 +-
 README.md                              |   8 +-
 SEMECLAW_AGENT_PLAN.md                 |   2 +-
 default_workspace/config.example.yaml  |  13 ++-
 docs/ARCHITECTURE.md                   |   2 +-
 skills/dexter.md                       |   2 +-
 src/semeclaw/cli/onboard.py            |  49 +++++++--
 src/semeclaw/core/agent.py             |   8 ++
 src/semeclaw/core/commands/handlers.py |  80 ++++++++++++++
 src/semeclaw/core/commands/registry.py |   8 ++
 src/semeclaw/provider/llm/base.py      | 195 +++++++++++++++++++++++----------
 src/semeclaw/tools/builtin_tools.py    |  26 ++++-
 src/semeclaw/utils/config.py           |  19 ++++
 tests/test_api.py                      |  54 ++++++---
 war_room/dashboard/server.py           |  96 ++++++++--------
 war_room/memory.py                     |   4 +-
 war_room/paperclip_bridge.py           |   2 +-
 war_room/tests/conftest.py             |  11 +-
 19 files changed, 459 insertions(+), 147 deletions(-)
```

## Review Instructions
- Read `.pi/company/context.md` first if present.
- Prioritize correctness, regressions, missing tests, rollout risk, and ownership gaps.
- Findings first. Keep summaries short.

## Diff
```diff
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 768b534..263ad9e 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -26,12 +26,35 @@ jobs:
       - name: Set up Python ${{ matrix.python-version }}
         run: uv python install ${{ matrix.python-version }}
 
+      - name: Reject tracked runtime artifacts
+        run: |
+          forbidden=$(git ls-files \
+            'war_room/logs/*.log' \
+            'war_room/logs/*.jsonl' \
+            'war_room/audio/_test.mp3' \
+            'war_room/audio/meetings/*.mp3' \
+            'war_room/audio/meetings/saved/*.mp3' \
+            'war_room/audio/scripts/*.json' \
+            'war_room/memory/memory.json' \
+            'war_room/shared_state.json' \
+            'war_room/research/*.md' \
+            'war_room/builds')
+          forbidden=$(printf '%s\n' "$forbidden" | grep -v '^war_room/research/saved/' || true)
+          if [ -n "$forbidden" ]; then
+            echo 'Tracked runtime artifacts detected:'
+            printf '%s\n' "$forbidden"
+            exit 1
+          fi
+
       - name: Install dependencies
         run: uv sync --frozen || uv sync
 
       - name: Install ffmpeg
         run: sudo apt-get update && sudo apt-get install -y ffmpeg
 
+      - name: Run full pytest suite
+        run: uv run pytest -q
+
       - name: Smoke test — meeting_skill module
         run: |
           uv run python -c "
diff --git a/Dockerfile b/Dockerfile
index d79a6ba..cab7804 100644
--- a/Dockerfile
+++ b/Dockerfile
@@ -1,6 +1,6 @@
 # SemeClaw War Room Agent — production image (Phase 2)
-# Build:  docker build -t ghcr.io/dansidanutz/semeclaw:0.6.0 .
-# Run:    docker run -p 8765:8765 --env-file .env ghcr.io/dansidanutz/semeclaw:0.6.0
+# Build:  docker build -t ghcr.io/dansidanutz/semeclaw:0.7.0 .
+# Run:    docker run -p 8765:8765 --env-file .env ghcr.io/dansidanutz/semeclaw:0.7.0
 
 FROM python:3.13-slim AS base
 
diff --git a/README.md b/README.md
index 27bf6a5..1a1d760 100644
--- a/README.md
+++ b/README.md
@@ -6,7 +6,7 @@
 **Embed in any app. Own your AI operations.**
 
 [![Version](https://img.shields.io/badge/version-0.7.0-10b981.svg)](./pyproject.toml)
-[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
+[![Python](https://img.shields.io/badge/python-3.10%2B%20(min)%20%C2%B7%203.13%20(rec)-blue.svg)](https://www.python.org/)
 [![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com/)
 [![License](https://img.shields.io/badge/license-Proprietary-8b5cf6.svg)](#license)
 [![Agent](https://img.shields.io/badge/OpenClaw-Agent-f59e0b.svg)](https://github.com/czl9707/build-your-own-openclaw)
@@ -49,7 +49,7 @@ Every meeting includes a **host announcer**, a **conversational orchestrator**,
 | 🪟 **iframe + JS SDK** | `/embed` + `/embed.js` — drop into Notion, CMS, NERVIX marketplace, anywhere |
 | 🔐 **Bearer auth on writes** | Reads stay open (so embeds work) · writes protected via `SEMECLAW_API_KEY` |
 | 🌐 **CORS + CSP configurable** | Allow-list origins + iframe parents via env |
-| 🐳 **Docker-ready** | Python 3.13 + uvicorn + ffmpeg + healthcheck |
+| 🐳 **Docker-ready** | Python 3.13 recommended for deploy parity (3.10+ minimum) + uvicorn + ffmpeg + healthcheck |
 | 📎 **Paperclip bridge** | Hook into Paperclip fleet ops today · first-class agent adapter coming in Phase 4 |
 | 🛡 **Sentinel** | Fleet health monitor — probes all droplets every 60s, fires Telegram alerts on CPU/RAM/disk thresholds. Runs on :18790 |
 | ⚡ **Coordinator** | 8-backend circuit-breaker LLM proxy on :8996. Auto-fails over across Claude / OpenRouter / Ollama / local models |
@@ -64,6 +64,10 @@ Every meeting includes a **host announcer**, a **conversational orchestrator**,
 
 ### Local dev
 
+Requirements:
+- Python 3.10+ minimum
+- Python 3.13 recommended for parity with Docker/dev deploys
+
 ```bash
 git clone https://github.com/DansiDanutz/SemeClaw.git
 cd SemeClaw
diff --git a/SEMECLAW_AGENT_PLAN.md b/SEMECLAW_AGENT_PLAN.md
index 861be92..9fa7f2a 100644
--- a/SEMECLAW_AGENT_PLAN.md
+++ b/SEMECLAW_AGENT_PLAN.md
@@ -83,7 +83,7 @@ Today SemeClaw runs as Dan's personal fleet brain on Mac Studio. The war-room da
 - [x] Write `INTEGRATION.md` guide for consumers
 
 ### Phase 2 — Deployability (next)
-- [ ] `Dockerfile` multi-stage build (Python 3.13 + uvicorn)
+- [ ] `Dockerfile` multi-stage build (Python 3.13 recommended for deploy parity; 3.10+ minimum)
 - [ ] `docker-compose.yml` with optional Chroma + Redis
 - [ ] GitHub Actions CI (lint + build)
 - [ ] `.env.example` documenting every env var
diff --git a/default_workspace/config.example.yaml b/default_workspace/config.example.yaml
index ae9cc0f..dc93554 100644
--- a/default_workspace/config.example.yaml
+++ b/default_workspace/config.example.yaml
@@ -8,6 +8,17 @@ llm:
   api_base: null
   temperature: 0.7
   max_tokens: 4096
+  # Optional fallback chain. SemeClaw will roll over when the primary
+  # provider hits quota, billing, access-denied, rate-limit, or transient errors.
+  # fallbacks:
+  #   - provider: openrouter
+  #     model: openrouter/qwen/qwen3.6-plus:free
+  #     api_key: your-openrouter-api-key
+  #     api_base: https://openrouter.ai/api/v1
+  #   - provider: ollama
+  #     model: ollama/qwen3:8b
+  #     api_key: ollama
+  #     api_base: http://localhost:11434
 
 default_agent: seme
 
@@ -40,4 +51,4 @@ default_agent: seme
 #     - "https://youtube.com/@competitor1"
 #     - "https://youtube.com/@competitor2"
 #   fetch_schedule: "0 8 * * *"  # Daily at 8 AM
-#   report_schedule: "0 10 * * 1"  # Weekly on Monday at 10 AM
\ No newline at end of file
+#   report_schedule: "0 10 * * 1"  # Weekly on Monday at 10 AM
diff --git a/docs/ARCHITECTURE.md b/docs/ARCHITECTURE.md
index a9ceb23..28e0489 100644
--- a/docs/ARCHITECTURE.md
+++ b/docs/ARCHITECTURE.md
@@ -213,7 +213,7 @@ Agents arranged in a 360° ring. The center **LIVE SPEAKER card** morphs in real
 
 | Layer | Tech |
 |-------|------|
-| Runtime | Python 3.13 + uvicorn |
+| Runtime | Python 3.10+ minimum, 3.13 recommended + uvicorn |
 | Web framework | FastAPI |
 | Frontend | Vanilla HTML + JS (no build step) |
 | Voice TTS | ElevenLabs Flash v2.5 → edge-tts fallback |
diff --git a/skills/dexter.md b/skills/dexter.md
index 8181b60..625c66d 100644
--- a/skills/dexter.md
+++ b/skills/dexter.md
@@ -52,7 +52,7 @@ For NERVIX: backend Python code, API endpoints, database migrations, CI/CD pipel
 - **Code review** — catching security issues, performance problems, architecture violations
 
 ## Stack I Work With
-- Language: Python 3.13 (never 3.14 — broken on macOS)
+- Language: Python 3.10+ minimum, 3.13 preferred on macOS (never 3.14 — broken on macOS)
 - Package manager: uv
 - Framework: FastAPI + uvicorn
 - Models: ollama/qwen2.5-coder:7b (local, free) → zai/glm-5 → claude-sonnet-4-6
diff --git a/src/semeclaw/cli/onboard.py b/src/semeclaw/cli/onboard.py
index 385c2b7..58ec98d 100644
--- a/src/semeclaw/cli/onboard.py
+++ b/src/semeclaw/cli/onboard.py
@@ -259,14 +259,20 @@ def write_config(
     telegram_chat_id: str = "",
     brave_key: str = "",
 ) -> Path:
+    llm_cfg = {
+        "provider": provider.id,
+        "model": model,
+        "api_key": api_key if not api_key.startswith("$") else api_key,
+        "temperature": 0.7,
+        "max_tokens": 4096,
+    }
+
+    fallbacks = _default_fallback_chain(provider.id)
+    if fallbacks:
+        llm_cfg["fallbacks"] = fallbacks
+
     cfg: dict = {
-        "llm": {
-            "provider": provider.id,
-            "model": model,
-            "api_key": api_key if not api_key.startswith("$") else api_key,
-            "temperature": 0.7,
-            "max_tokens": 4096,
-        },
+        "llm": llm_cfg,
         "default_agent": default_agent,
     }
 
@@ -288,6 +294,35 @@ def write_config(
     return out
 
 
+def _default_fallback_chain(primary_provider_id: str) -> list[dict]:
+    """Build a practical fallback chain from locally available providers."""
+    fallbacks: list[dict] = []
+
+    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
+    if primary_provider_id != "openrouter" and openrouter_key:
+        fallbacks.append(
+            {
+                "provider": "openrouter",
+                "model": "openrouter/qwen/qwen3.6-plus:free",
+                "api_key": openrouter_key,
+                "api_base": "https://openrouter.ai/api/v1",
+            }
+        )
+
+    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip() or "http://localhost:11434"
+    if primary_provider_id != "ollama" and _probe_http(ollama_url + "/api/tags"):
+        fallbacks.append(
+            {
+                "provider": "ollama",
+                "model": "ollama/qwen3:8b",
+                "api_key": "ollama",
+                "api_base": ollama_url,
+            }
+        )
+
+    return fallbacks
+
+
 # ---------------------------------------------------------------------------
 # Main onboarding flow
 # ---------------------------------------------------------------------------
diff --git a/src/semeclaw/core/agent.py b/src/semeclaw/core/agent.py
index d31e708..5c8a9d0 100644
--- a/src/semeclaw/core/agent.py
+++ b/src/semeclaw/core/agent.py
@@ -57,6 +57,14 @@ class Agent:
             skill_tool = create_skill_tool(self.config)
             registry.register(skill_tool)
 
+        from semeclaw.tools.pi_company_tools import create_pi_company_tools
+        for pi_tool in create_pi_company_tools(self.config):
+            registry.register(pi_tool)
+
+        from semeclaw.tools.github_tools import create_github_tools
+        for github_tool in create_github_tools(self.config):
+            registry.register(github_tool)
+
         return registry
 
     def new_session(self, session_id: str | None = None) -> "AgentSession":
diff --git a/src/semeclaw/core/commands/handlers.py b/src/semeclaw/core/commands/handlers.py
index ca9b55e..1410c5f 100644
--- a/src/semeclaw/core/commands/handlers.py
+++ b/src/semeclaw/core/commands/handlers.py
@@ -1,8 +1,22 @@
 """Built-in command implementations."""
 
+from pathlib import Path
 from typing import TYPE_CHECKING
 
 from semeclaw.core.commands.base import Command
+from semeclaw.integrations.pi_company import (
+    bootstrap_repo,
+    format_bootstrap_result,
+    format_status,
+    get_status,
+)
+from semeclaw.integrations.github_pr import (
+    format_local_review_bundle_result,
+    format_pr_status,
+    format_review_bundle_result,
+    get_pr_status,
+    write_review_bundle_or_local,
+)
 
 if TYPE_CHECKING:
     from semeclaw.core.agent import AgentSession
@@ -184,3 +198,69 @@ class ContextCommand(Command):
             lines.append("  Status: OK")
 
         return "\n".join(lines)
+
+
+class PiCompanyCommand(Command):
+    """Inspect or bootstrap pi-company in a target repository."""
+
+    name = "/pi-company"
+    aliases = []
+    description = "Inspect or bootstrap pi-company in a repository"
+
+    async def execute(self, args: str, session: "AgentSession") -> str:
+        parts = args.split()
+        subcommand = parts[0] if parts else "status"
+        target = Path(parts[1]).expanduser() if len(parts) > 1 else session.agent.config.workspace
+
+        if subcommand == "status":
+            status = get_status(target_path=target, workspace=session.agent.config.workspace)
+            return format_status(status)
+
+        if subcommand == "bootstrap":
+            company_name = " ".join(parts[2:]).strip() or None
+            try:
+                result = bootstrap_repo(
+                    target_path=target,
+                    workspace=session.agent.config.workspace,
+                    company_name=company_name,
+                )
+            except Exception as exc:
+                return f"Error bootstrapping pi-company: {exc}"
+            return format_bootstrap_result(result)
+
+        return (
+            "Usage:\n"
+            "  /pi-company status [target_path]\n"
+            "  /pi-company bootstrap [target_path] [company_name]"
+        )
+
+
+class GitHubPrCommand(Command):
+    """Inspect or bundle a GitHub pull request with gh."""
+
+    name = "/github-pr"
+    aliases = ["/pr"]
+    description = "Inspect PR status or generate a local review bundle"
+
+    async def execute(self, args: str, session: "AgentSession") -> str:
+        parts = args.split()
+        subcommand = parts[0] if parts else "status"
+        repo_path = Path(parts[1]).expanduser() if len(parts) > 1 else session.agent.config.workspace
+        pr_ref = parts[2] if len(parts) > 2 else None
+
+        try:
+            if subcommand == "status":
+                return format_pr_status(get_pr_status(repo_path=repo_path, pr_ref=pr_ref))
+            if subcommand == "bundle":
+                bundle = write_review_bundle_or_local(repo_path=repo_path, pr_ref=pr_ref)
+                if hasattr(bundle, "status"):
+                    return format_review_bundle_result(bundle)
+                return format_local_review_bundle_result(bundle)
+        except Exception as exc:
+            return f"GitHub PR command failed: {exc}"
+
+        return (
+            "Usage:\n"
+            "  /github-pr status [repo_path] [pr_ref]\n"
+            "  /github-pr bundle [repo_path] [pr_ref]"
+        )
diff --git a/src/semeclaw/core/commands/registry.py b/src/semeclaw/core/commands/registry.py
index 4e06d0b..e3e01ff 100644
--- a/src/semeclaw/core/commands/registry.py
+++ b/src/semeclaw/core/commands/registry.py
@@ -88,7 +88,11 @@ class CommandRegistry:
             CommandRegistry with HelpCommand, SkillsCommand, and SessionCommand
         """
         from semeclaw.core.commands.handlers import (
+            ContextCommand,
+            CompactCommand,
+            GitHubPrCommand,
             HelpCommand,
+            PiCompanyCommand,
             SkillsCommand,
             SessionCommand,
         )
@@ -97,4 +101,8 @@ class CommandRegistry:
         registry.register(HelpCommand())
         registry.register(SkillsCommand())
         registry.register(SessionCommand())
+        registry.register(CompactCommand())
+        registry.register(ContextCommand())
+        registry.register(PiCompanyCommand())
+        registry.register(GitHubPrCommand())
         return registry
diff --git a/src/semeclaw/provider/llm/base.py b/src/semeclaw/provider/llm/base.py
index 37ac8b5..d256b6d 100644
--- a/src/semeclaw/provider/llm/base.py
+++ b/src/semeclaw/provider/llm/base.py
@@ -2,13 +2,26 @@
 
 from __future__ import annotations
 
-from typing import Any, Optional, cast, TYPE_CHECKING
+from dataclasses import dataclass
+from typing import Any, cast, TYPE_CHECKING
 
 from litellm import acompletion, Choices
 from litellm.types.completion import ChatCompletionMessageParam as Message
 
 if TYPE_CHECKING:
-    from semeclaw.utils.config import LLMConfig
+    from semeclaw.utils.config import LLMConfig, LLMFallbackConfig
+
+
+@dataclass
+class LLMEndpoint:
+    """Concrete endpoint configuration used at runtime."""
+
+    provider: str
+    model: str
+    api_key: str
+    api_base: str | None
+    temperature: float
+    max_tokens: int
 
 
 class ToolCall:
@@ -25,31 +38,46 @@ class LLMProvider:
     """LLM provider using litellm for multi-provider support."""
 
     def __init__(
-        self,
-        model: str,
-        api_key: str,
-        api_base: Optional[str] = None,
-        temperature: float = 0.7,
-        max_tokens: int = 2048,
-        **kwargs: Any,
+        self, endpoints: list[LLMEndpoint], **kwargs: Any
     ):
         """Initialize LLM provider."""
-        self.model = model
-        self.api_key = api_key
-        self.api_base = api_base
-        self.temperature = temperature
-        self.max_tokens = max_tokens
+        self.endpoints = endpoints
+        primary = endpoints[0]
+        self.model = primary.model
+        self.api_key = primary.api_key
+        self.api_base = primary.api_base
+        self.temperature = primary.temperature
+        self.max_tokens = primary.max_tokens
         self._settings = kwargs
 
     @classmethod
     def from_config(cls, config: "LLMConfig") -> "LLMProvider":
         """Create provider from LLMConfig."""
-        return cls(
-            model=config.model,
-            api_key=config.api_key,
-            api_base=config.api_base,
-            temperature=config.temperature,
-            max_tokens=config.max_tokens,
+        endpoints = [
+            LLMEndpoint(
+                provider=config.provider,
+                model=config.model,
+                api_key=config.api_key,
+                api_base=config.api_base,
+                temperature=config.temperature,
+                max_tokens=config.max_tokens,
+            )
+        ]
+        for fallback in config.fallbacks:
+            endpoints.append(cls._endpoint_from_fallback(config, fallback))
+
+        return cls(endpoints=endpoints)
+
+    @staticmethod
+    def _endpoint_from_fallback(primary: "LLMConfig", fallback: "LLMFallbackConfig") -> LLMEndpoint:
+        """Resolve fallback config with inherited defaults from the primary endpoint."""
+        return LLMEndpoint(
+            provider=fallback.provider,
+            model=fallback.model,
+            api_key=fallback.api_key,
+            api_base=fallback.api_base,
+            temperature=fallback.temperature if fallback.temperature is not None else primary.temperature,
+            max_tokens=fallback.max_tokens if fallback.max_tokens is not None else primary.max_tokens,
         )
 
     async def chat(
@@ -68,40 +96,93 @@ class LLMProvider:
         Returns:
             Tuple of (response_text, tool_calls or None)
         """
-        request_kwargs: dict[str, Any] = {
-            "model": self.model,
-            "messages": messages,
-            "api_key": self.api_key,
-        }
-
-        if self.api_base:
-            request_kwargs["api_base"] = self.api_base
-
-        if tool_schemas:
-            request_kwargs["tools"] = tool_schemas
-
-        request_kwargs.update(kwargs)
-
-        response = await acompletion(**request_kwargs)
-        message = cast(Choices, response.choices[0]).message
-
-        # Parse tool calls if present
-        tool_calls = None
-        if hasattr(message, "tool_calls") and message.tool_calls:
-            tool_calls = []
-            for tc in message.tool_calls:
-                import json
-
-                args = tc.function.arguments
-                if isinstance(args, str):
-                    args = json.loads(args)
-
-                tool_calls.append(
-                    ToolCall(
-                        id=tc.id,
-                        name=tc.function.name,
-                        arguments=args,
-                    )
-                )
-
-        return message.content or "", tool_calls
+        errors: list[str] = []
+        last_exc: Exception | None = None
+
+        for index, endpoint in enumerate(self.endpoints):
+            request_kwargs: dict[str, Any] = {
+                "model": endpoint.model,
+                "messages": messages,
+                "api_key": endpoint.api_key,
+                "temperature": endpoint.temperature,
+                "max_tokens": endpoint.max_tokens,
+            }
+
+            if endpoint.api_base:
+                request_kwargs["api_base"] = endpoint.api_base
+
+            if tool_schemas:
+                request_kwargs["tools"] = tool_schemas
+
+            request_kwargs.update(kwargs)
+
+            try:
+                response = await acompletion(**request_kwargs)
+                message = cast(Choices, response.choices[0]).message
+
+                tool_calls = None
+                if hasattr(message, "tool_calls") and message.tool_calls:
+                    tool_calls = []
+                    for tc in message.tool_calls:
+                        import j

[diff truncated]
```
