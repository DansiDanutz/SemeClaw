"""Web search tool for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semeclaw.tools.base import BaseTool

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


async def websearch(query: str, search_provider) -> str:
    """Perform a web search and return results as formatted text.

    Args:
        query: Search query
        search_provider: WebSearchProvider instance

    Returns:
        Formatted search results or error message
    """
    try:
        results = await search_provider.search(query)

        if not results:
            return f"No results found for: {query}"

        lines = [f"Web search results for '{query}':\n"]
        for i, result in enumerate(results, 1):
            lines.append(f"{i}. {result.title}")
            lines.append(f"   URL: {result.url}")
            lines.append(f"   {result.snippet}\n")

        return "\n".join(lines)

    except Exception as e:
        return f"Web search failed: {str(e)}"


def create_websearch_tool(config: Config) -> BaseTool | None:
    """Create web search tool if configured.

    Args:
        config: SemeClaw configuration

    Returns:
        BaseTool instance or None if not configured
    """
    if not config.websearch:
        return None

    from semeclaw.provider.web_search.brave import BraveSearchProvider

    provider = None
    if config.websearch.provider == "brave":
        provider = BraveSearchProvider(api_key=config.websearch.api_key)
    else:
        raise ValueError(f"Unknown web search provider: {config.websearch.provider}")

    class WebSearchTool(BaseTool):
        name = "web_search"
        description = "Search the web for information using a search engine"
        input_schema = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                }
            },
            "required": ["query"],
        }

        async def execute(self, query: str, **kwargs) -> str:
            """Execute web search."""
            return await websearch(query, provider)

    return WebSearchTool()
