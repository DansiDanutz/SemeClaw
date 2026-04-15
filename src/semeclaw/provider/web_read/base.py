"""Base web reading provider abstraction."""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class ReadResult(BaseModel):
    """Result of reading a web page."""

    url: str
    title: str
    content: str
    error: str | None = None


class WebReadProvider(ABC):
    """Abstract base class for web reading providers."""

    @abstractmethod
    async def read(self, url: str) -> ReadResult:
        """Read and extract content from a web page.

        Args:
            url: URL to read

        Returns:
            ReadResult with extracted markdown content or error
        """
        pass
