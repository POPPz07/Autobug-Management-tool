"""
AutoRepro Enterprise — Server-Sent Events (SSE) Endpoint (V2.0)

SSE is the fallback for clients that cannot use WebSocket.
Subscribes to the user's personal Redis channel: realtime:user:{user_id}

Client usage:
    const eventSource = new EventSource('/api/v1/events/stream', {
        headers: { 'Authorization': `Bearer ${token}` }
    });
    eventSource.addEventListener('job.progress', (e) => {
        const data = JSON.parse(e.data);
        console.log('Job progress:', data.progress_percent);
    });
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.dependencies import Ctx
from utils.config import REDIS_URL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["realtime"])


@router.get("/stream")
async def event_stream(ctx: Ctx):
    """
    SSE endpoint for real-time updates.

    Each authenticated user gets their own channel: realtime:user:{user_id}.
    The worker and services publish to this channel via Redis PUBLISH.
    """
    async def generate():
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        channel = f"realtime:user:{ctx.user_id}"

        await pubsub.subscribe(channel)
        logger.info(f"SSE stream opened: user={ctx.user_id}")

        try:
            # Send initial keep-alive comment so the connection registers
            yield ": connected\n\n"

            async for message in pubsub.listen():
                if message["type"] == "message":
                    event_data = message["data"]
                    # Parse event type for named SSE events
                    try:
                        parsed = json.loads(event_data)
                        event_type = parsed.get("type", "message")
                        yield f"event: {event_type}\ndata: {event_data}\n\n"
                    except (json.JSONDecodeError, Exception):
                        yield f"data: {event_data}\n\n"
        except asyncio.CancelledError:
            logger.info(f"SSE stream closed: user={ctx.user_id}")
        finally:
            await pubsub.unsubscribe(channel)
            await redis.aclose()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
