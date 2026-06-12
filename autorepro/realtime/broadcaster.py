"""
AutoRepro Enterprise — Redis Pub/Sub Broadcaster (V2.0)

Bridges Redis pub/sub events to in-process WebSocket connections.

Multi-worker architecture:
  - Worker A updates job progress → publishes to Redis PUBLISH realtime:events
  - All API server processes subscribe to realtime:events
  - Each API server forwards events to its locally connected WebSockets

Started automatically in api/main.py lifespan if ENABLE_WEBSOCKETS=true.
"""

import asyncio
import json
import logging
from uuid import UUID

import redis.asyncio as aioredis

from realtime.manager import manager
from utils.config import REDIS_URL

logger = logging.getLogger(__name__)


class RedisBroadcaster:
    """
    Async Redis pub/sub listener that forwards events to the WebSocket manager.
    """

    def __init__(self, redis_url: str):
        self.redis = aioredis.from_url(redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()

    async def start(self) -> None:
        """Subscribe to the global realtime channel and start processing events."""
        await self.pubsub.subscribe("realtime:events")
        logger.info("Redis broadcaster started — listening on realtime:events")

        async for message in self.pubsub.listen():
            if message["type"] == "message":
                await self._handle_event(message["data"])

    async def _handle_event(self, data: str) -> None:
        """Parse a Redis event and forward it to the appropriate WebSocket clients."""
        try:
            event = json.loads(data)
            user_id = event.get("user_id")
            company_id = event.get("company_id")

            if user_id:
                # Targeted: send to a specific user
                await manager.send_to_user(UUID(user_id), event)
            elif company_id:
                # Broadcast: send to all users in a company
                await manager.broadcast_to_company(UUID(company_id), event)
        except Exception as e:
            logger.error(f"Error handling realtime event: {e}")

    async def publish(self, event: dict) -> None:
        """Publish an event to Redis for all API servers to pick up."""
        await self.redis.publish("realtime:events", json.dumps(event))


# Global singleton — imported by api/main.py for lifespan startup
broadcaster = RedisBroadcaster(REDIS_URL)
