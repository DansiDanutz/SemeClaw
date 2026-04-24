"""SemeClaw agent package — registry, browser search, scraper.

Public surface:
    from war_room.agents import registry, browser_search, scraper
    registry.core_agents()           # Research, Writer, Scraping, Coder, Browser
    registry.adapters()              # Paperclip, Moltica, Claude Code, GPT
    await browser_search.search(q)
    await scraper.scrape_url(url)
"""

from . import _browser_search as browser_search
from . import _registry as registry
from . import _scraper as scraper

__all__ = ["registry", "browser_search", "scraper"]
