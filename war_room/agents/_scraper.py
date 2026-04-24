"""
war_room.agents._scraper — URL → clean markdown.

Strategy:
    1. github.com/* → fast-path: rewrite to raw.githubusercontent for READMEs
    2. Use readability-lxml if available (best quality)
    3. Fall back to a regex-based main-content extractor (zero deps beyond stdlib + httpx)

Public surface:
    async def scrape_url(url, max_chars=8000) -> ScrapeResult
    async def scrape_batch(urls, max_chars=6000) -> list[ScrapeResult]
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse as up
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; SemeClawScrapingAgent/1.0)"


@dataclass
class ScrapeResult:
    url: str
    title: str = ""
    content: str = ""
    flag: str | None = None  # PAYWALL | JS-REQUIRED | ROBOTS-BLOCKED | ERROR
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "flag": self.flag,
            "error": self.error,
        }


def _github_readme_url(url: str) -> str | None:
    """If url looks like a github repo root, return the raw README URL."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/?#]+)/?$", url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\n\s*\n\s*\n+")
_PAYWALL_HINTS = ("subscribe to read", "subscriber-only", "sign in to continue", "paywall")


def _basic_extract(html: str) -> tuple[str, str]:
    """Title + main text using regex only. Lossy but zero-dep."""
    title_match = _TITLE_RE.search(html)
    title = _TAG_RE.sub("", title_match.group(1)).strip() if title_match else ""
    cleaned = _SCRIPT_RE.sub("", html)
    # Try to grab <main> or <article> first
    for tag in ("article", "main"):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", cleaned, re.DOTALL | re.IGNORECASE)
        if m:
            cleaned = m.group(1)
            break
    text = _TAG_RE.sub("\n", cleaned)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#x27;", "'", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = _WS_RE.sub("\n\n", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln.strip())
    return title, text


def _readability_extract(html: str) -> tuple[str, str] | None:
    try:
        from readability import Document  # type: ignore
    except Exception:
        return None
    try:
        doc = Document(html)
        title = doc.short_title()
        body_html = doc.summary(html_partial=True)
        _, text = _basic_extract(body_html)
        return title, text
    except Exception as e:
        logger.debug("readability failed, falling back to basic: %s", e)
        return None


async def scrape_url(url: str, max_chars: int = 8000) -> ScrapeResult:
    if not url or not url.startswith(("http://", "https://")):
        return ScrapeResult(url=url, flag="ERROR", error="invalid url")

    fetch_url = _github_readme_url(url) or url
    try:
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers={"User-Agent": _UA}
        ) as c:
            r = await c.get(fetch_url)
            if r.status_code in (401, 402, 403):
                return ScrapeResult(url=url, flag="PAYWALL", error=f"http {r.status_code}")
            r.raise_for_status()
            text_body = r.text
    except Exception as e:
        return ScrapeResult(url=url, flag="ERROR", error=str(e))

    # Raw markdown / plain content
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype.lower() and len(text_body) < max_chars * 4:
        # Plain text or markdown — return as-is (trimmed)
        title = url.rsplit("/", 1)[-1] or up.urlparse(url).netloc
        return ScrapeResult(url=url, title=title, content=text_body[:max_chars])

    # Detect paywall hints
    lower = text_body[:5000].lower()
    if any(h in lower for h in _PAYWALL_HINTS):
        return ScrapeResult(url=url, flag="PAYWALL", error="paywall hint detected")
    # Detect JS-only (very small body)
    if len(text_body) < 500 and "<noscript" in text_body.lower():
        return ScrapeResult(url=url, flag="JS-REQUIRED")

    extracted = _readability_extract(text_body) or _basic_extract(text_body)
    title, content = extracted
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[...truncated...]"
    return ScrapeResult(url=url, title=title, content=content)


async def scrape_batch(urls: list[str], max_chars: int = 6000) -> list[ScrapeResult]:
    """Fetch up to 5 URLs in parallel."""
    sem = asyncio.Semaphore(5)

    async def _one(u: str) -> ScrapeResult:
        async with sem:
            return await scrape_url(u, max_chars=max_chars)

    return await asyncio.gather(*[_one(u) for u in urls[:5]])
