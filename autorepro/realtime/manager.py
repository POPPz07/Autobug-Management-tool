"""
AutoRepro Enterprise — WebSocket Connection Manager (V2.0)

Manages active WebSocket connections in memory. Supports:
  - Per-user message delivery (send_to_user)
  - Per-company broadcast (broadcast_to_company)
  - Automatic cleanup of dead connections

Thread-safety: This is a singleton used by the FastAPI async event loop.
"""

import logging
from typing import Dict, Set
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    In-memory WebSocket connection registry.

    Maps:
      user_id -> set of WebSocket connections (a user can have multiple tabs)
      company_id -> set of user_ids (for company-wide broadcasts)
    """

    def __init__(self):
        # user_id -> set of active WebSocket connections
        self.active_connections: Dict[UUID, Set[WebSocket]] = {}
        # company_id -> set of user_ids currently connected
        self.company_users: Dict[UUID, Set[UUID]] = {}

    async def connect(self, websocket: WebSocket, user_id: UUID, company_id: UUID) -> None:
        """Accept a new WebSocket connection and register it."""
        await websocket.accept()

        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)

        if company_id not in self.company_users:
            self.company_users[company_id] = set()
        self.company_users[company_id].add(user_id)

        logger.info(f"WebSocket connected: user={user_id}, company={company_id}")

    def disconnect(self, websocket: WebSocket, user_id: UUID, company_id: UUID) -> None:
        """Remove a WebSocket connection from the registry."""
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)

            # If user has no more connections, remove from company mapping
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
                if company_id in self.company_users:
                    self.company_users[company_id].discard(user_id)

        logger.info(f"WebSocket disconnected: user={user_id}")

    async def send_to_user(self, user_id: UUID, event: dict) -> None:
        """Send an event to all of a user's active connections."""
        if user_id not in self.active_connections:
            return

        dead_sockets: Set[WebSocket] = set()

        for websocket in self.active_connections[user_id]:
            try:
                await websocket.send_json(event)
            except Exception:
                dead_sockets.add(websocket)

        # Cleanup any dead connections
        self.active_connections[user_id] -= dead_sockets

    async def broadcast_to_company(self, company_id: UUID, event: dict) -> None:
        """Broadcast an event to all users in a company."""
        if company_id not in self.company_users:
            return

        for user_id in self.company_users[company_id]:
            await self.send_to_user(user_id, event)


# Global singleton — used by websocket_routes.py and broadcaster.py
manager = ConnectionManager()
