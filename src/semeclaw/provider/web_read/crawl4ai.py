"""Crawl4AI web reading provider implementation."""

from __future__ import annotations

from crawl4ai import AsyncWebCrawler

from semeclaw.provider.web_read.base import ReadResult, WebReadProvider


class Crawl4AIProvider(WebReadProvider):
    """Web reading provider using Crawl4AI."""

    async def read(self, url: str) -> ReadResult:
        """Read and extract markdown from web page using Crawl4AI.

        Args:
            url: URL to read

        Returns:
            ReadResult with extracted markdown or error message
        """
        try:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url)

            return ReadResult(
                url=url,
                title=result.metadata.get("title", "") if result.metadata else "",
                content=result.markdown or "",
            )

        except Exception as e:
            return ReadResult(
                url=url,
                title="",
                content="",
                error=f"Failed to read {url}: {str(e)}",
            )
