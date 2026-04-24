"""FastAPI application for SemeClaw server."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

if TYPE_CHECKING:
    from semeclaw.core.context import SharedContext


logger = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    """Request model for sending a message."""

    session_id: str
    content: str


class MessageResponse(BaseModel):
    """Response model for a message."""

    session_id: str
    content: str
    timestamp: float


def create_app(context: SharedContext) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        context: The shared application context.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="SemeClaw Server",
        description="AI agent system server",
        version="1.0.0",
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "ok",
            "context": str(context),
        }

    @app.get("/stats")
    async def get_stats():
        """Get server statistics."""
        ws_connections = 0
        if context.websocket_worker:
            ws_connections = context.websocket_worker.get_connection_count()

        return {
            "websocket_connections": ws_connections,
            "agents_loaded": bool(context.agent_loader),
            "eventbus_running": bool(context.eventbus),
            "channels": list(context.channels.keys()),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        user_id: str = Query(...),
        session_id: str = Query(None),
    ):
        """WebSocket endpoint for real-time communication.

        Args:
            websocket: The WebSocket connection.
            user_id: The user ID.
            session_id: Optional existing session ID.
        """
        if not context.websocket_worker:
            await websocket.close(code=1000, reason="WebSocket worker not initialized")
            return

        # Use provided session_id or generate one
        if not session_id:
            session_id = f"ws-{user_id}-{asyncio.current_task().get_name()}"

        await websocket.accept()

        try:
            # Register the connection
            message_queue: asyncio.Queue = asyncio.Queue()
            context.websocket_worker.register_connection(user_id, session_id, message_queue)

            # Create a task to handle incoming messages
            receive_task = asyncio.create_task(_handle_ws_receive(websocket, context, session_id))

            # Create a task to handle outgoing messages
            send_task = asyncio.create_task(_handle_ws_send(websocket, message_queue))

            # Wait for either task to complete (which means disconnect)
            done, pending = await asyncio.wait(
                [receive_task, send_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel remaining tasks
            for task in pending:
                task.cancel()

            # Wait for cancellation to complete
            await asyncio.gather(*pending, return_exceptions=True)

            logger.info(f"WebSocket disconnected: {session_id}")

        except Exception as e:
            logger.exception(f"WebSocket error: {e}")
        finally:
            # Unregister the connection
            context.websocket_worker.unregister_connection(session_id)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.post("/api/messages")
    async def send_message(request: MessageRequest):
        """Send a message via HTTP.

        Args:
            request: The message request.

        Returns:
            The response.
        """
        if not context.agent_loader or not context.eventbus:
            raise HTTPException(status_code=503, detail="Service not fully initialized")

        try:
            from semeclaw.core.events import CliEventSource, InboundEvent

            source = CliEventSource()
            event = InboundEvent(
                session_id=request.session_id,
                source=source,
                content=request.content,
            )

            await context.eventbus.publish(event)

            return {
                "session_id": request.session_id,
                "status": "accepted",
            }
        except Exception as e:
            logger.exception(f"Error sending message: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return app


async def _handle_ws_receive(
    websocket: WebSocket,
    context: SharedContext,
    session_id: str,
) -> None:
    """Handle receiving messages from WebSocket client.

    Args:
        websocket: The WebSocket connection.
        context: The shared context.
        session_id: The session ID.
    """
    try:
        while True:
            data = await websocket.receive_json()

            if not context.eventbus:
                continue

            from semeclaw.core.events import InboundEvent, WebSocketEventSource

            # Extract user_id from session_id if possible, otherwise use generic
            user_id = session_id.split("-")[1] if "-" in session_id else "unknown"
            source = WebSocketEventSource(user_id=user_id)

            event = InboundEvent(
                session_id=session_id,
                source=source,
                content=data.get("message", ""),
            )

            await context.eventbus.publish(event)
            logger.debug(f"Received message from {session_id}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception(f"Error receiving WebSocket message: {e}")


async def _handle_ws_send(websocket: WebSocket, message_queue: asyncio.Queue) -> None:
    """Handle sending messages to WebSocket client.

    Args:
        websocket: The WebSocket connection.
        message_queue: The message queue.
    """
    try:
        while True:
            message = await message_queue.get()
            await websocket.send_json(message)
            logger.debug("Sent message via WebSocket")
    except Exception as e:
        logger.exception(f"Error sending WebSocket message: {e}")
