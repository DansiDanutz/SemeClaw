"""Base web search provider abstraction."""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class SearchResult(BaseModel):
    """A single search result."""

    title: str
    url: str
    snippet: str


class WebSearchProvider(ABC):
    """Abstract base class for web search providers."""

    @abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        """Search the web for the given query.

        Args:
            query: Search query string

        Returns:
            List of search results
        """
        pass
