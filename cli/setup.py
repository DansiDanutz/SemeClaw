"""`semeclaw setup` — interactive onboarding wizard.

Steps:
    1. Welcome + show what's already configured (calls status checklist)
    2. Offer to add OpenRouter key (skippable — DDG search + Ollama fallback work without)
    3. Smoke-test the key against /v1/models
    4. Offer to add Brave Search key (optional)
    5. Offer to enable adapters (Paperclip / Moltica / Claude Code / GPT)
    6. Save to ~/.semeclaw/env
    7. Run a 1-shot smoke prompt against the chosen model
    8. Offer to launch the live demo
"""
from __future__ import annotations

import asyncio
import os
import sys

from . import _env
from ._ui import banner, section, row, hint, ask, confirm, success, fail, OK, MISS, _C


async def _smoke_openrouter(key: str) -> tuple[bool, str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            data = r.json()
        free = [m["id"] for m in data.get("data", []) if ":free" in m.get("id", "")]
        return True, f"{len(free)} free models available"
    except Exception as e:
        return False, str(e)


async def _smoke_completion(key: str, model: str = "google/gemma-3-27b-it:free") -> tuple[bool, str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": "https://github.com/DansiDanutz/SemeClaw",
                    "X-Title": "SemeClaw setup smoke test",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly: ready"}],
                    "max_tokens": 10,
                },
            )
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"].strip()
        return True, f"{model} → {txt[:40]}"
    except Exception as e:
        return False, str(e)


def run() -> int:
    _env.export_into_process()
    banner("SemeClaw setup",
           "One-time onboarding. Everything is optional — defaults work zero-config.")

    updates: dict[str, str] = {}

    # ----- OpenRouter key -----
    section("Step 1 / 4 — OpenRouter (free LLM access)")
    print("  SemeClaw uses 5 free OpenRouter models out of the box (Llama 3.3 70B,")
    print("  Qwen3 80B, Gemma 3 27B, GPT-OSS 120B, Hermes 3 405B). You'll need a free")
    print("  OpenRouter API key — takes 30 seconds:")
    print(f"  {_C.C}https://openrouter.ai/keys{_C.OFF}\n")
    current = os.environ.get("OPENROUTER_API_KEY", "")
    if current:
        row(OK, "Already configured", f"key set ({len(current)} chars)")
        if not confirm("Replace it?", default=False):
            new_key = current
        else:
            new_key = ask("OpenRouter API key", default="").strip()
    else:
        new_key = ask("OpenRouter API key (paste, or press Enter to skip)", default="").strip()

    if new_key:
        print(f"\n  {_C.DIM}Smoke-testing the key...{_C.OFF}")
        ok, msg = asyncio.run(_smoke_openrouter(new_key))
        if ok:
            row(OK, "OpenRouter API", msg)
            updates["OPENROUTER_API_KEY"] = new_key
            print(f"  {_C.DIM}Running 1 free completion to verify end-to-end...{_C.OFF}")
            ok2, msg2 = asyncio.run(_smoke_completion(new_key))
            row(OK if ok2 else MISS, "Live completion", msg2)
        else:
            fail(f"Key did not validate: {msg}")
            row(MISS, "OpenRouter API", "skipped — fix the key and re-run setup")
    else:
        row(MISS, "OpenRouter", "skipped — Ollama / DuckDuckGo still work")
        hint("You can re-run `semeclaw setup` any time to add the key later")

    # ----- Brave Search (optional) -----
    section("Step 2 / 4 — Brave Search (optional, faster web search)")
    print("  Browser Agent uses DuckDuckGo by default (no key, unlimited).")
    print(f"  Brave Search gives faster, cleaner results — free 2,000/mo at")
    print(f"  {_C.C}https://brave.com/search/api/{_C.OFF}\n")
    if confirm("Add Brave Search API key?", default=False):
        bk = ask("Brave Search API key", default="").strip()
        if bk:
            updates["BRAVE_SEARCH_API_KEY"] = bk
            row(OK, "Brave Search", "key saved")
    else:
        row(MISS, "Brave Search", "skipped — DuckDuckGo will be used")

    # ----- Adapters -----
    section("Step 3 / 4 — External adapters (optional)")
    print("  Connect your existing agent system to the meeting room.")
    print("  All optional — the 5 core agents work fully without any of these.\n")
    try:
        from war_room.agents import registry
        for a in registry.adapters():
            req = (a.adapter or {}).get("required_env", []) or []
            already = all(os.environ.get(k) for k in req)
            if already:
                row(OK, a.name, "already connected via env")
                continue
            row(MISS, a.name, f"requires: {', '.join(req) or '(none)'}")
            if req and confirm(f"  Configure {a.name} now?", default=False):
                for k in req:
                    v = ask(f"    {k}", default="").strip()
                    if v:
                        updates[k] = v
    except Exception as e:
        row(MISS, "Adapter discovery", str(e))

    # ----- Save -----
    section("Step 4 / 4 — Save")
    if updates:
        path = _env.save(updates)
        row(OK, "Wrote", str(path))
        print(f"  {_C.DIM}Keys are stored owner-only (chmod 600).{_C.OFF}")
    else:
        row(MISS, "No new env to save", "")

    # ----- Status -----
    section("Final status")
    from . import status as _status_mod
    _status_mod.run()

    # ----- Demo offer -----
    if confirm("Run the 4-agent live demo now?", default=True):
        from . import demo as _demo_mod
        return _demo_mod.run()

    success("Setup complete. Anytime: `semeclaw status` · `semeclaw demo` · `semeclaw agents`")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
