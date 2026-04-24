"""
War Room — Research Tools
Browser automation, web search, web extract, and code execution sandbox
for the Research Agent.

These tools are called during agent runs to give the Research Agent
capabilities beyond plain LLM text generation.

Usage (from war_room.py):
    from research_tools import ResearchTools
    tools = ResearchTools()
    results = await tools.search("topic")
    extracted = await tools.extract(["https://example.com"])
    exec_result = await tools.run_code("print('hello')")
    browser_result = await tools.browser_navigate("https://example.com")
"""

import asyncio
import logging
import sys

logger = logging.getLogger("war_room.research_tools")

# ---------------------------------------------------------------------------
# Web Search (via curl to a public search API or via hermes web_search)
# ---------------------------------------------------------------------------


class ResearchTools:
    """Tools available to the Research Agent during pipeline runs."""

    def __init__(self, max_search_results: int = 5):
        self.max_search_results = max_search_results
        self._last_search_results: list[dict] = []

    async def search(self, query: str) -> str:
        """
        Search the web for a query. Returns top results as formatted text.
        Uses curl to DuckDuckGo HTML search as a zero-dependency approach.
        """
        logger.info("🔍 Searching: %s", query)
        try:
            # Use DuckDuckGo HTML search via curl
            import httpx

            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                r = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                )
                # Parse results from HTML
                from html.parser import HTMLParser

                results = []

                class DDGParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self._in_result = False
                        self._in_title = False
                        self._in_snippet = False
                        self._current_url = ""
                        self._current_title = ""
                        self._current_snippet = ""
                        self._results = []

                    def handle_starttag(self, tag, attrs):
                        attrs_dict = dict(attrs)
                        if (
                            tag == "a"
                            and "class" in attrs_dict
                            and "result__snippet" not in attrs_dict.get("class", "")
                        ):
                            href = attrs_dict.get("href", "")
                            if href.startswith("//duckduckgo.com/l/?uddg=") or href.startswith("/"):
                                from urllib.parse import parse_qs, unquote, urlparse

                                if "uddg" in href:
                                    qs = parse_qs(urlparse(href).query)
                                    self._current_url = unquote(qs["uddg"][0])
                                elif href.startswith("/"):
                                    self._current_url = "https://duckduckgo.com" + href

                    def handle_data(self, data):
                        pass  # DuckDuckGo HTML is complex, fallback below

                # Simpler approach: use regex to extract results
                import re

                html = r.text
                # Find result links and snippets
                title_pattern = re.compile(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL)
                snippet_pattern = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)

                titles = title_pattern.findall(html)
                snippets = snippet_pattern.findall(html)

                for i, (href, title) in enumerate(titles[: self.max_search_results]):
                    # Clean title
                    title = re.sub(r"<[^>]+>", "", title).strip()
                    snippet = ""
                    if i < len(snippets):
                        snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
                    results.append(
                        {
                            "title": title,
                            "url": href,
                            "snippet": snippet,
                        }
                    )

                if not results:
                    # Fallback: return search URL and suggest manual research
                    return f"[Search returned no parseable results for '{query}'. Try a different query or use browser_navigate.]"

                self._last_search_results = results
                lines = [f"Search results for: {query}", ""]
                for i, r in enumerate(results, 1):
                    lines.append(f"{i}. {r['title']}")
                    lines.append(f"   URL: {r['url']}")
                    if r["snippet"]:
                        lines.append(f"   {r['snippet']}")
                    lines.append("")
                return "\n".join(lines)

        except Exception as e:
            logger.error("Search failed: %s", e)
            return f"[Search failed: {e}]"

    async def extract(self, urls: list[str]) -> str:
        """
        Extract content from web pages. Returns markdown text.
        Uses markitdown for superior HTML→Markdown, falls back to trafilatura,
        then to simple regex stripping.
        """
        logger.info("📄 Extracting content from %d URLs", len(urls))
        results = []
        for url in urls:
            try:
                import httpx

                async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                    r = await client.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                    )
                    html = r.text

                    # Try markitdown for clean HTML→Markdown
                    try:
                        import os
                        import tempfile

                        from markitdown import MarkItDown

                        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
                            f.write(html)
                            tmp = f.name
                        try:
                            md = MarkItDown()
                            converted = md.convert(tmp)
                            if converted.text_content and converted.text_content.strip():
                                results.append({"url": url, "title": url, "content": converted.text_content[:5000]})
                                continue
                        finally:
                            os.unlink(tmp)
                    except ImportError:
                        pass

                    # Try trafilatura for clean extraction
                    try:
                        import trafilatura

                        extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
                        if extracted:
                            results.append({"url": url, "title": url, "content": extracted[:3000]})
                            continue
                    except ImportError:
                        pass

                    # Fallback: simple HTML-to-text
                    import re

                    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
                    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    results.append({"url": url, "title": url, "content": text[:3000]})

            except Exception as e:
                results.append({"url": url, "error": str(e)})

        lines = []
        for r in results:
            lines.append(f"\n--- URL: {r['url']} ---")
            if "error" in r:
                lines.append(f"Error: {r['error']}")
            else:
                lines.append(r["content"])
        return "\n".join(lines) if lines else "[No content extracted]"

    async def browser_navigate(self, url: str) -> str:
        """
        Navigate to a URL and return the page text content.
        Uses markitdown for HTML→Markdown conversion when available.
        """
        logger.info("🌐 Navigating to: %s", url)
        try:
            import httpx

            async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                r = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                )
                html = r.text
                import re

                title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else url

                # Try markitdown for rich HTML→Markdown
                try:
                    import os
                    import tempfile

                    from markitdown import MarkItDown

                    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
                        f.write(html)
                        tmp = f.name
                    try:
                        md = MarkItDown()
                        converted = md.convert(tmp)
                        if converted.text_content and converted.text_content.strip():
                            return f"Title: {title}\nURL: {url}\n\n{converted.text_content[:5000]}"
                    finally:
                        os.unlink(tmp)
                except ImportError:
                    pass

                # Fallback: strip HTML
                text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return f"Title: {title}\nURL: {url}\n\n{text[:5000]}"
        except Exception as e:
            return f"[Navigation failed: {e}]"

    async def convert_document(self, file_path: str) -> str:
        """
        Convert a local file to Markdown text.
        Supports PDF, DOCX, PPTX, XLSX, HTML, EPUB, images, and more.
        """
        logger.info("📎 Converting document: %s", file_path)
        try:
            from pathlib import Path

            p = Path(file_path).expanduser().resolve()
            if not p.exists():
                return f"[File not found: {file_path}]"
            if not p.is_file():
                return f"[Not a file: {file_path}]"

            from markitdown import MarkItDown

            md = MarkItDown()
            result = md.convert(str(p))
            text = result.text_content or ""
            if not text.strip():
                return f"[No extractable content from {p.name}]"
            if len(text) > 50_000:
                text = text[:50_000] + "\n\n... [truncated at 50,000 chars]"
            return text
        except ImportError:
            return "[markitdown not installed. Run: pip install markitdown]"
        except Exception as e:
            return f"[Document conversion failed: {e}]"

    async def run_code(self, python_code: str, timeout: int = 30) -> str:
        """
        Execute Python code in a sandbox subprocess.
        Returns stdout + stderr. Useful for data analysis, calculations, etc.
        """
        logger.info("🐍 Running code (%d chars)", len(python_code))
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                python_code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                output = stdout.decode("utf-8", errors="replace")
                error = stderr.decode("utf-8", errors="replace")
                result = f"Exit code: {proc.returncode}"
                if output:
                    result += f"\n\n--- stdout ---\n{output[:3000]}"
                if error:
                    result += f"\n\n--- stderr ---\n{error[:3000]}"
                return result
            except asyncio.TimeoutError:
                proc.kill()
                return f"[Code execution timed out after {timeout}s]"
        except Exception as e:
            return f"[Code execution failed: {e}]"

    async def run_shell(self, command: str, timeout: int = 30) -> str:
        """
        Execute a shell command in a sandbox. Returns stdout + stderr.
        Useful for git operations, file inspection, etc.
        """
        logger.info("🖥 Running shell: %s", command[:100])
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                output = stdout.decode("utf-8", errors="replace")
                error = stderr.decode("utf-8", errors="replace")
                result = f"Exit code: {proc.returncode}"
                if output:
                    result += f"\n\n{output[:3000]}"
                if error:
                    result += f"\n\n[stderr]\n{error[:3000]}"
                return result
            except asyncio.TimeoutError:
                proc.kill()
                return f"[Command timed out after {timeout}s]"
        except Exception as e:
            return f"[Shell command failed: {e}]"

    def get_available_tools_description(self) -> str:
        """Return a description of available tools for the agent system prompt."""
        return """
## Available Research Tools

You have access to the following tools to help with your research:

1. **search(query)** — Search the web for information. Returns top 5 results with titles, URLs, and snippets.

2. **extract(urls)** — Extract clean Markdown content from web page URLs using markitdown. Pass a list of URLs, returns rich text/markdown from each page.

3. **browser_navigate(url)** — Navigate to a single URL and get the page content as Markdown. Uses markitdown for superior HTML conversion.

4. **convert_document(file_path)** — Convert any local file to Markdown text. Supports PDF, DOCX, PPTX, XLSX, HTML, EPUB, images (EXIF/OCR), and more.

5. **run_code(python_code)** — Execute Python code and get the output. Useful for data analysis, calculations, JSON processing, etc.

6. **run_shell(command)** — Execute a shell command and get the output. Useful for git operations, file inspection, system commands.

Use these tools actively during your research. Don't just rely on your training knowledge — verify claims with live data.
"""
