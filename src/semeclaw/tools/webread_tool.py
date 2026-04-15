"""Web reading tool for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semeclaw.tools.base import BaseTool

if TYPE_CHECKING:
    from semeclaw.utils.config import Config


async def webread(url: str, read_provider) -> str:
    """Read a web page and return its content as markdown.

    Args:
        url: URL to read
        read_provider: WebReadProvider instance

    Returns:
        Page content as markdown or error message
    """
    try:
        result = await read_provider.read(url)

        if result.error:
            return f"Error reading page: {result.error}"

        lines = []
        if result.title:
            lines.append(f"# {result.title}\n")
        lines.append(f"Source: {result.url}\n")
        lines.append(result.content)

        return "\n".join(lines)

    except Exception as e:
        return f"Web reading failed: {str(e)}"


def create_webread_tool(config: "Config") -> BaseTool | None:
    """Create web reading tool if configured.

    Args:
        config: SemeClaw configuration

    Returns:
        BaseTool instance or None if not configured
    """
    if not config.webread:
        return None

    from semeclaw.provider.web_read.crawl4ai import Crawl4AIProvider

    provider = None
    if config.webread.provider == "crawl4ai":
        provider = Crawl4AIProvider()
    else:
        raise ValueError(f"Unknown web read provider: {config.webread.provider}")

    class WebReadTool(BaseTool):
        name = "web_read"
        description = "Read the content of a web page and extract its text as markdown"
        input_schema = {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to read",
                }
            },
            "required": ["url"],
        }

        async def execute(self, url: str, **kwargs) -> str:
            """Execute web read."""
            return await webread(url, provider)

    return WebReadTool()
