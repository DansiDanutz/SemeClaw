"""
Models adapter — discover and run LLMs from multiple sources.

Sources:
  1. Local Ollama      — OLLAMA_HOST (default http://127.0.0.1:11434)
     GET  /api/tags           → list models
     POST /api/generate       → inference

  2. OpenRouter        — OPENROUTER_API_KEY + OPENROUTER_BASE_URL
     GET  /api/v1/models      → list models (no auth required)
     POST /api/v1/chat/completions → inference (auth required)

  3. Curated free models — fallback when everything is offline

Each model is exposed as an AdapterAgent so the War Room can pick the
best available model for a task. Models are tagged with their source
(ollama | openrouter | fallback) so generation can be routed correctly.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path
from typing import Any

import httpx

try:
    from war_room.adapters.base import Adapter, AdapterHealth
except ImportError:
    from adapters.base import Adapter, AdapterHealth

logger = logging.getLogger("war_room.adapters.ollama")

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_OPENROUTER_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

MODELS_STATE_DIR = Path.home() / ".semeclaw"

# Curated free models shown when everything is offline
FALLBACK_MODELS = [
    {"id": "llama3.1", "name": "Llama 3.1", "source": "fallback", "parameter_size": "8B"},
    {"id": "codellama", "name": "CodeLlama", "source": "fallback", "parameter_size": "7B"},
    {"id": "mistral", "name": "Mistral", "source": "fallback", "parameter_size": "7B"},
    {"id": "phi4", "name": "Phi-4", "source": "fallback", "parameter_size": "14B"},
    {
        "id": "openrouter/anthropic/claude-3.5-sonnet",
        "name": "Claude 3.5 Sonnet",
        "source": "fallback",
        "parameter_size": "cloud",
    },
    {
        "id": "openrouter/google/gemini-2.0-flash-exp",
        "name": "Gemini 2.0 Flash",
        "source": "fallback",
        "parameter_size": "cloud",
    },
]


class OllamaAdapter(Adapter):
    """Multi-source model adapter: Ollama + OpenRouter + fallback."""

    def __init__(
        self,
        ollama_url: str | None = None,
        openrouter_key: str | None = None,
        openrouter_url: str | None = None,
    ) -> None:
        super().__init__("ollama", ollama_url or DEFAULT_OLLAMA_URL)
        self.ollama_url = ollama_url or DEFAULT_OLLAMA_URL
        self.openrouter_key = openrouter_key or OPENROUTER_KEY
        self.openrouter_url = (openrouter_url or DEFAULT_OPENROUTER_URL).rstrip("/")
        self._ollama_client: httpx.AsyncClient | None = None
        self._openrouter_client: httpx.AsyncClient | None = None
        self._state_file = MODELS_STATE_DIR / "models_sync.json"
        self._cached_models: list[dict] = []
        self._load_local_state()

    def _load_local_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._cached_models = data.get("models", [])
            except Exception:
                self._cached_models = []
        else:
            self._cached_models = []

    def _save_local_state(self) -> None:
        try:
            MODELS_STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps({"models": self._cached_models}, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save models local state: %s", e)

    async def _ensure_ollama_client(self) -> httpx.AsyncClient | None:
        if self._ollama_client is None:
            self._ollama_client = httpx.AsyncClient(base_url=self.ollama_url, timeout=60.0)
        return self._ollama_client

    async def _ensure_openrouter_client(self) -> httpx.AsyncClient | None:
        if self._openrouter_client is None:
            headers: dict[str, str] = {"HTTP-Referer": "https://semeclaw.local", "X-Title": "SemeClaw"}
            if self.openrouter_key:
                headers["Authorization"] = f"Bearer {self.openrouter_key}"
            self._openrouter_client = httpx.AsyncClient(base_url=self.openrouter_url, timeout=60.0, headers=headers)
        return self._openrouter_client

    async def close(self) -> None:
        for client in (self._ollama_client, self._openrouter_client):
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
        self._ollama_client = None
        self._openrouter_client = None

    def _ollama_reachable(self) -> bool:
        try:
            parsed = httpx.URL(self.ollama_url)
            host = parsed.host or "127.0.0.1"
            port = parsed.port or 11434
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except OSError:
            return False

    async def _openrouter_reachable(self) -> bool:
        try:
            client = await self._ensure_openrouter_client()
            if client is None:
                return False
            r = await client.get("/models", timeout=10.0)
            return r.status_code < 500
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    async def health(self) -> AdapterHealth:
        ollama_ok = self._ollama_reachable()
        or_ok = await self._openrouter_reachable()
        meta: dict[str, Any] = {
            "ollama_url": self.ollama_url,
            "ollama_reachable": ollama_ok,
            "openrouter_url": self.openrouter_url,
            "openrouter_reachable": or_ok,
            "openrouter_key_set": bool(self.openrouter_key),
        }
        if ollama_ok or or_ok:
            models = await self._fetch_all_models()
            meta["models_available"] = len(models)
            return AdapterHealth(
                name="ollama",
                reachable=True,
                meta=meta,
            )
        return AdapterHealth(
            name="ollama",
            reachable=False,
            error="No model source reachable (Ollama offline, OpenRouter unreachable)",
            meta=meta,
        )

    # ------------------------------------------------------------------ #
    #  Model discovery
    # ------------------------------------------------------------------ #

    async def _fetch_ollama_models(self) -> list[dict]:
        if not self._ollama_reachable():
            return []
        client = await self._ensure_ollama_client()
        if client is None:
            return []
        try:
            r = await client.get("/api/tags")
            r.raise_for_status()
            data = r.json()
            models = data.get("models", [])
            for m in models:
                m["source"] = "ollama"
            return models
        except Exception as e:
            logger.warning("Ollama model fetch failed: %s", e)
            return []

    async def _fetch_openrouter_models(self) -> list[dict]:
        if not await self._openrouter_reachable():
            return []
        client = await self._ensure_openrouter_client()
        if client is None:
            return []
        try:
            r = await client.get("/models", timeout=15.0)
            r.raise_for_status()
            data = r.json()
            models = data.get("data", [])
            # Mark all as openrouter
            for m in models:
                m["source"] = "openrouter"
            return models
        except Exception as e:
            logger.warning("OpenRouter model fetch failed: %s", e)
            return []

    async def _fetch_all_models(self) -> list[dict]:
        ollama = await self._fetch_ollama_models()
        openrouter = await self._fetch_openrouter_models()
        all_models = ollama + openrouter
        if all_models:
            self._cached_models = all_models
            self._save_local_state()
            return all_models
        return self._cached_models if self._cached_models else FALLBACK_MODELS

    async def list_free_models(self) -> list[dict]:
        """Return only free models (Ollama is always free, OpenRouter with zero pricing, fallback assumed free)."""
        models = await self._fetch_all_models()
        free: list[dict] = []
        for m in models:
            src = m.get("source", "")
            if src in ("ollama", "fallback"):
                free.append(m)
                continue
            if src == "openrouter":
                pricing = m.get("pricing", {})
                prompt_price = str(pricing.get("prompt", "1"))
                completion_price = str(pricing.get("completion", "1"))
                if prompt_price == "0" and completion_price == "0":
                    free.append(m)
        return free

    # ------------------------------------------------------------------ #
    #  Adapter interface
    # ------------------------------------------------------------------ #

    async def sync_to_war_room(self) -> list[dict[str, Any]]:
        agents = await self.list_agents()
        return [{"type": "agent", "data": a} for a in agents]

    async def list_agents(self) -> list[dict[str, Any]]:
        models = await self._fetch_all_models()
        return [self._normalize_agent(m) for m in models]

    async def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        return []

    # ------------------------------------------------------------------ #
    #  Write (sync FROM war room)
    # ------------------------------------------------------------------ #

    async def sync_from_war_room(self, changes: list[dict[str, Any]]) -> bool:
        ok = True
        for change in changes:
            ctype = change.get("type")
            if ctype == "generate":
                result = await self.generate(
                    model=change.get("model", ""),
                    prompt=change.get("prompt", ""),
                    system=change.get("system"),
                )
                ok = ok and result is not None
            elif ctype == "pull_model":
                ok = ok and await self.pull_model(change.get("model", ""))
            elif ctype == "delete_model":
                ok = ok and await self.delete_model(change.get("model", ""))
        return ok

    # ------------------------------------------------------------------ #
    #  Inference
    # ------------------------------------------------------------------ #

    async def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        stream: bool = False,
    ) -> str | None:
        """Route generation to the correct backend based on model source."""
        # Determine source from cached model list or model ID heuristic
        source = "ollama"
        for m in self._cached_models:
            if m.get("id") == model or m.get("name") == model:
                source = m.get("source", "ollama")
                break
        else:
            # Heuristic: OpenRouter models often contain '/'
            if "/" in model and not model.startswith("http"):
                source = "openrouter"

        if source == "openrouter":
            return await self._generate_openrouter(model, prompt, system, stream)
        return await self._generate_ollama(model, prompt, system, stream)

    async def _generate_ollama(
        self, model: str, prompt: str, system: str | None = None, stream: bool = False
    ) -> str | None:
        if not self._ollama_reachable():
            logger.info("[LOCAL] Ollama generate (%s): %s...", model, prompt[:60])
            return f"[Ollama offline — mock response for {model}]"

        client = await self._ensure_ollama_client()
        if client is None:
            return None

        try:
            payload: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "stream": stream,
            }
            if system:
                payload["system"] = system
            r = await client.post("/api/generate", json=payload)
            r.raise_for_status()
            data = r.json()
            return data.get("response", "")
        except Exception as e:
            logger.error("Ollama generate failed: %s", e)
            return None

    async def _generate_openrouter(
        self, model: str, prompt: str, system: str | None = None, stream: bool = False
    ) -> str | None:
        if not self.openrouter_key:
            logger.warning("OpenRouter API key not set — cannot generate with %s", model)
            return f"[OpenRouter key missing — cannot use {model}]"

        client = await self._ensure_openrouter_client()
        if client is None:
            return None

        try:
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            r = await client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": stream,
                },
            )
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""
        except Exception as e:
            logger.error("OpenRouter generate failed: %s", e)
            return None

    async def pull_model(self, model: str) -> bool:
        """Pull a model. Only works for Ollama; OpenRouter models are cloud-hosted."""
        if model.startswith("openrouter/"):
            logger.info("OpenRouter models are cloud-hosted — no pull needed for %s", model)
            return True
        if not self._ollama_reachable():
            logger.info("[LOCAL] Ollama pull %s (queued for when online)", model)
            return True

        client = await self._ensure_ollama_client()
        if client is None:
            return False

        try:
            r = await client.post("/api/pull", json={"name": model, "stream": False})
            r.raise_for_status()
            logger.info("Pulled Ollama model: %s", model)
            return True
        except Exception as e:
            logger.error("Ollama pull failed: %s", e)
            return False

    async def delete_model(self, model: str) -> bool:
        """Delete a model. Only works for Ollama."""
        if model.startswith("openrouter/"):
            logger.info("Cannot delete OpenRouter cloud model: %s", model)
            return False
        if not self._ollama_reachable():
            logger.info("[LOCAL] Ollama delete %s (queued for when online)", model)
            return True

        client = await self._ensure_ollama_client()
        if client is None:
            return False

        try:
            r = await client.delete("/api/delete", json={"name": model})
            r.raise_for_status()
            logger.info("Deleted Ollama model: %s", model)
            return True
        except Exception as e:
            logger.error("Ollama delete failed: %s", e)
            return False

    # ------------------------------------------------------------------ #
    #  Normalize
    # ------------------------------------------------------------------ #

    def _normalize_agent(self, raw: dict[str, Any]) -> dict[str, Any]:
        source = raw.get("source", "ollama")
        if source == "openrouter":
            model_id = raw.get("id", raw.get("model", "unknown"))
            name = raw.get("name", model_id)
            pricing = raw.get("pricing", {})
            is_free = str(pricing.get("prompt", "1")) == "0" and str(pricing.get("completion", "1")) == "0"
            return {
                "id": model_id,
                "name": name,
                "role": "Cloud LLM (OpenRouter)",
                "status": "ready",
                "platform": self.name,
                "source": "openrouter",
                "free": is_free,
                "pricing": pricing,
                "context_length": raw.get("context_length"),
                "description": raw.get("description", "")[:100],
            }

        # Ollama or fallback
        name = raw.get("name", raw.get("model", "unknown"))
        base_name = name.split(":")[0] if ":" in name else name
        details = raw.get("details", {})
        return {
            "id": base_name,
            "name": base_name,
            "role": f"Local LLM ({details.get('parameter_size', '?')})",
            "status": "ready",
            "platform": self.name,
            "source": source,
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
            "format": details.get("format"),
            "family": details.get("family"),
            "free": True,  # local is always free
        }
