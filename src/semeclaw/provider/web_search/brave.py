"""Brave Search provider implementation."""

import httpx

from semeclaw.provider.web_search.base import SearchResult, WebSearchProvider


class BraveSearchProvider(WebSearchProvider):
    """Web search provider using Brave Search API."""

    API_BASE = "https://api.search.brave.com/res/v1/web/search"
    MAX_RESULTS = 10

    def __init__(self, api_key: str) -> None:
        """Initialize Brave Search provider.

        Args:
            api_key: Brave Search API key
        """
        self.api_key = api_key

    async def search(self, query: str) -> list[SearchResult]:
        """Search using Brave Search API.

        Args:
            query: Search query string

        Returns:
            List of up to 10 search results
        """
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        params = {
            "q": query,
            "count": self.MAX_RESULTS,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                self.API_BASE,
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

        results = []
        web_results = data.get("web", [])

        for item in web_results[: self.MAX_RESULTS]:
            result = SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            )
            results.append(result)

        return results
