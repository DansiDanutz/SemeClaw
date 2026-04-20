"""War Room adapters — bidirectional sync with external agent platforms."""

try:
    from war_room.adapters.base import Adapter, AdapterHealth
    from war_room.adapters.paperclip import PaperclipAdapter
    from war_room.adapters.multica import MulticaAdapter
    from war_room.adapters.github import GitHubAdapter
    from war_room.adapters.obsidian import ObsidianAdapter
    from war_room.adapters.ollama import OllamaAdapter
    from war_room.adapters.telegram import TelegramAdapter
except ImportError:
    from adapters.base import Adapter, AdapterHealth
    from adapters.paperclip import PaperclipAdapter
    from adapters.multica import MulticaAdapter
    from adapters.github import GitHubAdapter
    from adapters.obsidian import ObsidianAdapter
    from adapters.ollama import OllamaAdapter
    from adapters.telegram import TelegramAdapter

__all__ = [
    "Adapter",
    "AdapterHealth",
    "PaperclipAdapter",
    "MulticaAdapter",
    "GitHubAdapter",
    "ObsidianAdapter",
    "OllamaAdapter",
    "TelegramAdapter",
]
