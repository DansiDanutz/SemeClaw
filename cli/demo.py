"""`semeclaw demo` — run the saved 4-agent live demo task.

Pipeline:
    1. Browser Agent  — search "open source multi-agent orchestration github 2026"
    2. Browser+LLM    — pick top 3 GitHub repos
    3. Scraping Agent — fetch each repo README in parallel
    4. Research Agent — synthesise stars/lang/license/standout
    5. Writer Agent   — render comparison brief markdown
    6. Coder Agent    — generate starter.py importing the top pick

Outputs land in demo/reports/four-agent-live-demo/<timestamp>/.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import _env
from ._ui import _C, ERR, INFO, MISS, OK, banner, fail, hint, row, section, success

_REPO_RE = re.compile(r"https?://github\.com/([^/\s)>\"]+)/([^/\s)>\"#?]+)")


async def _llm(model: str, system: str, user: str, max_tokens: int = 1200) -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": "https://github.com/DansiDanutz/SemeClaw",
                    "X-Title": "SemeClaw demo",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  {ERR} model {model}: {e}")
        return None


async def _agent_call(agent_id: str, system: str, user: str, max_tokens: int = 1200) -> str:
    """Try each model in the agent's preference list; return first success."""
    from war_room.agents import registry
    a = registry.get(agent_id)
    if not a:
        return f"[no such agent: {agent_id}]"
    for spec in a.model_preference:
        if not spec.startswith("openrouter:"):
            continue  # this minimal demo uses OpenRouter only
        model = spec.split(":", 1)[1]
        out = await _llm(model, system, user, max_tokens=max_tokens)
        if out:
            return out
    return "[all models in this agent's waterfall failed — check OPENROUTER_API_KEY and network]"


async def _run_pipeline(out_dir: Path) -> dict:
    from war_room.agents import browser_search, scraper

    transcript: dict = {"steps": [], "started_at": datetime.now(timezone.utc).isoformat()}

    # Step 1 — Browser search
    print(f"\n  {INFO} Browser Agent: searching...")
    query = "open source multi agent orchestration framework github"
    sr = await browser_search.search(query, count=10)
    print(f"      {OK} backend={sr.backend}  results={len(sr.results)}")
    transcript["steps"].append({
        "agent": "browser",
        "action": "search",
        "query": query,
        "backend": sr.backend,
        "results": [r.to_dict() for r in sr.results],
    })

    # Step 2 — Pick top 3 GitHub repos from results
    print(f"  {INFO} Browser Agent: ranking GitHub repos...")
    github_urls: list[str] = []
    for r in sr.results:
        m = _REPO_RE.match(r.url)
        if m and "github.com" in r.url:
            url = f"https://github.com/{m.group(1)}/{m.group(2)}"
            if url not in github_urls:
                github_urls.append(url)
        if len(github_urls) >= 3:
            break
    if len(github_urls) < 3:
        # fallback: ask the Browser Agent to suggest from snippet text
        snippet_blob = "\n".join(f"- {r.title} — {r.url} — {r.snippet[:140]}" for r in sr.results[:8])
        suggestion = await _agent_call(
            "browser",
            "You pick the most promising GitHub repos from search snippets.",
            f"From these results, list exactly 3 GitHub repo URLs (one per line, no extra text) "
            f"that are open-source multi-agent orchestration frameworks:\n\n{snippet_blob}",
            max_tokens=300,
        )
        for line in suggestion.splitlines():
            m = _REPO_RE.search(line)
            if m:
                url = f"https://github.com/{m.group(1)}/{m.group(2)}"
                if url not in github_urls:
                    github_urls.append(url)
            if len(github_urls) >= 3:
                break
    github_urls = github_urls[:3]
    print(f"      {OK} top 3: {[u.split('github.com/')[1] for u in github_urls]}")
    transcript["steps"].append({"agent": "browser", "action": "rank", "picks": github_urls})

    if not github_urls:
        return {"error": "no GitHub repos surfaced", "transcript": transcript}

    # Step 3 — Scrape READMEs in parallel
    print(f"  {INFO} Scraping Agent: fetching {len(github_urls)} README(s) in parallel...")
    scrapes = await scraper.scrape_batch(github_urls, max_chars=4000)
    for s in scrapes:
        mark = OK if not s.flag and s.content else MISS
        size = len(s.content) if s.content else 0
        print(f"      {mark} {s.url.split('github.com/')[1]:40} {size} chars  flag={s.flag or '-'}")
    transcript["steps"].append({"agent": "scraping", "results": [s.to_dict() for s in scrapes]})

    # Step 4 — Research synthesises
    print(f"  {INFO} Research Agent: synthesising comparison...")
    readme_blob = "\n\n---\n\n".join(
        f"## {s.url}\n\n{s.content[:3000]}" for s in scrapes if s.content
    )
    synthesis = await _agent_call(
        "research",
        "You are a senior technical analyst. Be terse and factual.",
        f"For each of these 3 GitHub repos, extract: name, primary language, stated license, "
        f"GitHub stars (from any badges/text you can find), and the single most distinctive "
        f"feature. Format as a markdown table.\n\n{readme_blob}",
        max_tokens=800,
    )
    print(f"      {OK} synthesis: {len(synthesis)} chars")
    transcript["steps"].append({"agent": "research", "output": synthesis})

    # Step 5 — Writer composes brief
    print(f"  {INFO} Writer Agent: composing brief...")
    brief = await _agent_call(
        "writer",
        "You write punchy 1-page technical briefs. Markdown. No fluff.",
        f"Turn this raw analysis into a 1-page comparison brief titled "
        f"'Open-Source Multi-Agent Orchestration: 3-Way Comparison'. End with a "
        f"single-sentence pick + reason.\n\n{synthesis}",
        max_tokens=1000,
    )
    print(f"      {OK} brief: {len(brief)} chars")
    transcript["steps"].append({"agent": "writer", "output": brief})

    # Step 6 — Coder scaffolds starter.py
    print(f"  {INFO} Coder Agent: scaffolding starter.py...")
    top_pick_url = github_urls[0]
    pkg_guess = top_pick_url.split("github.com/")[1].split("/")[1].lower().replace("-", "_")
    starter = await _agent_call(
        "coder",
        "You write minimal, runnable Python starters. Reply with code only.",
        f"Write a minimal Python starter file (max 30 lines) that imports the package "
        f"'{pkg_guess}' (assumed pip-installable from {top_pick_url}), instantiates whatever "
        f"the README's quickstart shows, and prints 'ready'. Add a 1-line install comment at "
        f"the top. If the package name is uncertain, add a TODO.",
        max_tokens=400,
    )
    # Strip markdown fences if present
    if starter.startswith("```"):
        starter = re.sub(r"^```\w*\n?", "", starter)
        starter = re.sub(r"\n?```$", "", starter)
    print(f"      {OK} starter.py: {len(starter)} chars")
    transcript["steps"].append({"agent": "coder", "output": starter})

    # Save outputs
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "brief.md").write_text(brief, encoding="utf-8")
    (out_dir / "starter.py").write_text(starter, encoding="utf-8")
    (out_dir / "run.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    transcript["finished_at"] = datetime.now(timezone.utc).isoformat()
    transcript["out_dir"] = str(out_dir)
    return {"ok": True, "transcript": transcript, "out_dir": out_dir}


def run() -> int:
    _env.export_into_process()
    banner("SemeClaw — 4-Agent Live Demo",
           "Real task: map the open-source multi-agent orchestration landscape")

    # Preflight
    section("Preflight")
    if not os.environ.get("OPENROUTER_API_KEY"):
        row(MISS, "OPENROUTER_API_KEY", "not set")
        fail("This demo needs OPENROUTER_API_KEY (free, 30 sec at https://openrouter.ai/keys).")
        hint("Run `semeclaw setup` to add it.")
        return 2
    row(OK, "OPENROUTER_API_KEY", "set")

    try:
        from war_room.agents import registry
        n_core = len(registry.core_agents())
        row(OK if n_core >= 4 else ERR, "Core agents", f"{n_core} loaded")
    except Exception as e:
        fail(f"Agent registry failed to load: {e}")
        return 3

    # Run
    section("Pipeline")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(__file__).resolve().parent.parent / "demo" / "reports" / "four-agent-live-demo" / ts

    t0 = time.time()
    result = asyncio.run(_run_pipeline(out_dir))
    dt = time.time() - t0

    # Report
    if result.get("error"):
        fail(f"Demo failed: {result['error']}")
        return 4

    section("Result")
    row(OK, "Total time", f"{dt:.1f}s")
    row(OK, "Output dir", str(result["out_dir"]))
    row(OK, "Brief", str(result["out_dir"] / "brief.md"))
    row(OK, "Starter", str(result["out_dir"] / "starter.py"))
    row(OK, "Transcript", str(result["out_dir"] / "run.json"))

    print(f"\n  {_C.DIM}--- Brief preview ---{_C.OFF}")
    brief = (result["out_dir"] / "brief.md").read_text(encoding="utf-8")
    for line in brief.splitlines()[:12]:
        print(f"  {line}")
    if len(brief.splitlines()) > 12:
        print(f"  {_C.DIM}... ({len(brief.splitlines())} lines total — see {result['out_dir']/'brief.md'}){_C.OFF}")

    success(f"Demo complete in {dt:.1f}s. Re-run any time: `semeclaw demo`")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
