"""
war_room.agents._browser_search — Zero-config web search.

Backend ladder (highest available wins):
    1. Brave Search API   — if BRAVE_SEARCH_API_KEY is set (2k/mo free)
    2. SearXNG instance   — if SEARXNG_URL is set
    3. DuckDuckGo HTML    — no key, no quota, best-effort scrape

Public surface:
    async def search(query, count=5, freshness=None) -> SearchResponse
    SearchResponse: {backend, query, results: [{title, url, snippet, domain}]}
"""
from __future__ import annotations

import logging
import os
import re
import urllib.parse as up
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet, "domain": self.domain}


@dataclass
class SearchResponse:
    backend: str
    query: str
    results: list[SearchResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "error": self.error,
        }


_UA = "Mozilla/5.0 (compatible; SemeClawBrowserAgent/1.0; +https://semeclaw.fly.dev)"


def _domain_of(url: str) -> str:
    try:
        return up.urlparse(url).netloc.lower()
    except Exception:
        return ""


async def _brave(query: str, count: int, freshness: str | None) -> SearchResponse | None:
    key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not key:
        return None
    params = {"q": query, "count": min(count, 20)}
    if freshness:
        params["freshness"] = freshness  # pd | pw | pm | py
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers={"Accept": "application/json", "X-Subscription-Token": key},
            )
            r.raise_for_status()
            data = r.json()
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
                domain=_domain_of(item.get("url", "")),
            )
            for item in (data.get("web", {}).get("results") or [])[:count]
        ]
        return SearchResponse(backend="brave", query=query, results=results)
    except Exception as e:
        logger.warning("brave search failed: %s", e)
        return SearchResponse(backend="brave", query=query, error=str(e))


async def _searxng(query: str, count: int) -> SearchResponse | None:
    base = os.environ.get("SEARXNG_URL")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{base.rstrip('/')}/search",
                params={"q": query, "format": "json", "language": "en"},
                headers={"User-Agent": _UA},
            )
            r.raise_for_status()
            data = r.json()
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                domain=_domain_of(item.get("url", "")),
            )
            for item in (data.get("results") or [])[:count]
        ]
        return SearchResponse(backend="searxng", query=query, results=results)
    except Exception as e:
        logger.warning("searxng search failed: %s", e)
        return SearchResponse(backend="searxng", query=query, error=str(e))


# DuckDuckGo HTML — minimal regex parser, no third-party dep.
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _TAG_RE.sub("", s).replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"').strip()


async def _duckduckgo(query: str, count: int) -> SearchResponse:
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True, headers={"User-Agent": _UA}
        ) as c:
            r = await c.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": "us-en"},
            )
            r.raise_for_status()
            html = r.text
        results: list[SearchResult] = []
        for m in _DDG_RESULT_RE.finditer(html):
            url_raw, title_raw, snippet_raw = m.group(1), m.group(2), m.group(3)
            # DDG wraps urls in /l/?uddg=<encoded>
            if url_raw.startswith("//duckduckgo.com/l/") or url_raw.startswith("/l/"):
                qs = up.parse_qs(up.urlparse(url_raw).query)
                url = up.unquote(qs.get("uddg", [url_raw])[0])
            else:
                url = url_raw
            results.append(
                SearchResult(
                    title=_strip_html(title_raw),
                    url=url,
                    snippet=_strip_html(snippet_raw),
                    domain=_domain_of(url),
                )
            )
            if len(results) >= count:
                break
        return SearchResponse(backend="duckduckgo", query=query, results=results)
    except Exception as e:
        logger.warning("duckduckgo search failed: %s", e)
        return SearchResponse(backend="duckduckgo", query=query, error=str(e))


async def search(query: str, count: int = 5, freshness: str | None = None) -> SearchResponse:
    """Try Brave → SearXNG → DuckDuckGo. Returns the first backend with results."""
    for fn in (lambda: _brave(query, count, freshness), lambda: _searxng(query, count)):
        resp = await fn()
        if resp and resp.results:
            return resp
    return await _duckduckgo(query, count)


def status() -> dict:
    """For `semeclaw status` — which backends are configured."""
    return {
        "brave":      bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
        "searxng":    bool(os.environ.get("SEARXNG_URL")),
        "duckduckgo": True,
    }
