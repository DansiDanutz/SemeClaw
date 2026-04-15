"""Session history persistence for SemeClaw."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from litellm.types.completion import ChatCompletionMessageParam as Message

logger = logging.getLogger(__name__)


class HistoryMessage(BaseModel):
    """Message stored in history."""

    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    timestamp: datetime = None

    def __init__(self, **data: Any) -> None:
        """Initialize with default timestamp if not provided."""
        if "timestamp" not in data or data["timestamp"] is None:
            data["timestamp"] = datetime.now()
        super().__init__(**data)

    class Config:
        """Pydantic config."""

        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


class HistorySession(BaseModel):
    """Session metadata stored in history."""

    id: str
    agent_id: str
    source: str
    title: str | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic config."""

        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


class HistoryStore:
    """Persistent storage for session history."""

    def __init__(self, history_path: Path) -> None:
        """Initialize history store.

        Args:
            history_path: Path to history directory
        """
        self.history_path = Path(history_path)
        self.sessions_dir = self.history_path / "sessions"

        # Create directories
        self.history_path.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # Index file for session metadata
        self.index_file = self.history_path / "index.jsonl"

    def create_session(
        self,
        session_id: str,
        agent_id: str,
        source: str,
        title: str | None = None,
    ) -> HistorySession:
        """Create a new session in history.

        Args:
            session_id: Unique session identifier
            agent_id: Agent ID for this session
            source: Source of the session (e.g., "cli", "api")
            title: Optional human-readable title

        Returns:
            HistorySession instance
        """
        now = datetime.now()
        session = HistorySession(
            id=session_id,
            agent_id=agent_id,
            source=source,
            title=title or f"Session {session_id[:8]}",
            message_count=0,
            created_at=now,
            updated_at=now,
        )

        # Append to index
        self._append_index(session)

        return session

    def save_message(self, session_id: str, message: Message) -> None:
        """Save a message to session history.

        Args:
            session_id: Session ID
            message: Message to save (ChatCompletionMessageParam)
        """
        try:
            # Convert Message to HistoryMessage
            hist_msg = HistoryMessage(
                role=message.get("role", "user"),
                content=message.get("content", ""),
                tool_calls=message.get("tool_calls"),
                tool_call_id=message.get("tool_call_id"),
                name=message.get("name"),
            )

            # Append to session file
            session_file = self.sessions_dir / f"{session_id}.jsonl"
            with open(session_file, "a") as f:
                f.write(hist_msg.model_dump_json() + "\n")

            # Update session count in index
            self._increment_session_count(session_id)

        except Exception as e:
            logger.error(f"Failed to save message to history: {e}")

    def list_sessions(self) -> list[HistorySession]:
        """Get all sessions from history.

        Returns:
            List of HistorySession instances
        """
        sessions = []
        if not self.index_file.exists():
            return sessions

        try:
            with open(self.index_file) as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        sessions.append(HistorySession.model_validate(data))
        except Exception as e:
            logger.error(f"Failed to read sessions from index: {e}")

        return sessions

    def get_messages(self, session_id: str) -> list[HistoryMessage]:
        """Get all messages for a session.

        Args:
            session_id: Session ID

        Returns:
            List of HistoryMessage instances
        """
        messages = []
        session_file = self.sessions_dir / f"{session_id}.jsonl"

        if not session_file.exists():
            return messages

        try:
            with open(session_file) as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        messages.append(HistoryMessage.model_validate(data))
        except Exception as e:
            logger.error(f"Failed to read messages for session {session_id}: {e}")

        return messages

    def get_session_info(self, session_id: str) -> HistorySession | None:
        """Get metadata for a session.

        Args:
            session_id: Session ID

        Returns:
            HistorySession or None if not found
        """
        sessions = self.list_sessions()
        for session in sessions:
            if session.id == session_id:
                return session
        return None

    def _append_index(self, session: HistorySession) -> None:
        """Append session to index file."""
        try:
            with open(self.index_file, "a") as f:
                f.write(session.model_dump_json() + "\n")
        except Exception as e:
            logger.error(f"Failed to append to index: {e}")

    def _increment_session_count(self, session_id: str) -> None:
        """Increment message count for a session.

        Reads all sessions, finds the one to update, and rewrites the index.
        """
        try:
            sessions = self.list_sessions()

            # Find and increment the session
            for session in sessions:
                if session.id == session_id:
                    session.message_count += 1
                    session.updated_at = datetime.now()
                    break

            # Rewrite index
            with open(self.index_file, "w") as f:
                for session in sessions:
                    f.write(session.model_dump_json() + "\n")

        except Exception as e:
            logger.error(f"Failed to update session count: {e}")
