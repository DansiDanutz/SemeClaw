"""Event-driven architecture for SemeClaw."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar


class EventSource(ABC):
    """Base class for event sources with registry."""

    _registry: ClassVar[dict[str, type[EventSource]]] = {}
    _namespace: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs):
        """Auto-register subclasses by namespace."""
        super().__init_subclass__(**kwargs)
        if cls._namespace:
            EventSource._registry[cls._namespace] = cls

    @property
    def is_platform(self) -> bool:
        """Check if this is a platform event source."""
        return self._namespace.startswith("platform-")

    @property
    def is_cron(self) -> bool:
        """Check if this is a cron event source."""
        return self._namespace == "cron"

    @property
    def is_agent(self) -> bool:
        """Check if this is an agent event source."""
        return self._namespace == "agent"

    @property
    def platform_name(self) -> str | None:
        """Get the platform name if this is a platform source."""
        if not self.is_platform:
            return None
        return self._namespace.split("-", 1)[1]

    @classmethod
    def from_string(cls, s: str) -> EventSource:
        """Parse an event source from string format."""
        namespace = s.split(":")[0]
        source_cls = cls._registry.get(namespace)
        if not source_cls:
            raise ValueError(f"Unknown source: {namespace}")
        return source_cls.from_string(s)

    @abstractmethod
    def __str__(self) -> str:
        """Convert to string format."""
        pass


@dataclass
class CliEventSource(EventSource):
    """Event source for CLI interactions."""

    _namespace = "platform-cli"

    def __str__(self) -> str:
        return "platform-cli"

    @classmethod
    def from_string(cls, s: str) -> CliEventSource:
        """Parse from string."""
        return cls()


@dataclass
class AgentEventSource(EventSource):
    """Event source for agent-to-agent communication."""

    _namespace = "agent"
    agent_id: str

    def __str__(self) -> str:
        return f"agent:{self.agent_id}"

    @classmethod
    def from_string(cls, s: str) -> AgentEventSource:
        """Parse from string."""
        agent_id = s.split(":", 1)[1]
        return cls(agent_id=agent_id)


@dataclass
class TelegramEventSource(EventSource):
    """Event source for Telegram platform."""

    _namespace = "platform-telegram"
    user_id: str
    chat_id: str

    def __str__(self) -> str:
        return f"platform-telegram:{self.user_id}:{self.chat_id}"

    @classmethod
    def from_string(cls, s: str) -> TelegramEventSource:
        """Parse from string."""
        parts = s.split(":")
        return cls(user_id=parts[1], chat_id=parts[2])


@dataclass
class WebSocketEventSource(EventSource):
    """Event source for WebSocket connections."""

    _namespace = "platform-ws"
    user_id: str

    def __str__(self) -> str:
        return f"platform-ws:{self.user_id}"

    @classmethod
    def from_string(cls, s: str) -> WebSocketEventSource:
        """Parse from string."""
        user_id = s.split(":", 1)[1]
        return cls(user_id=user_id)


@dataclass
class CronEventSource(EventSource):
    """Event source for scheduled cron jobs."""

    _namespace = "cron"
    cron_id: str

    def __str__(self) -> str:
        return f"cron:{self.cron_id}"

    @classmethod
    def from_string(cls, s: str) -> CronEventSource:
        """Parse from string."""
        cron_id = s.split(":", 1)[1]
        return cls(cron_id=cron_id)


@dataclass
class Event:
    """Base event class."""

    session_id: str
    source: EventSource
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class InboundEvent(Event):
    """Event coming into the system from external sources."""

    retry_count: int = 0


@dataclass
class OutboundEvent(Event):
    """Event being sent out to external platforms."""

    error: str | None = None


@dataclass
class DispatchEvent(Event):
    """Event dispatching work to a child agent session."""

    parent_session_id: str = ""
    retry_count: int = 0


@dataclass
class DispatchResultEvent(Event):
    """Result of a dispatch event."""

    error: str | None = None
