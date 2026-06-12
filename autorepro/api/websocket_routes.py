"""
AutoRepro Enterprise — WebSocket Endpoint (V2.0)

Single WebSocket endpoint at /ws?token={jwt}.
Authenticates via JWT query parameter, then keeps connection alive with ping/pong.
Events are pushed from the server (via the broadcaster) — clients only send pings.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from realtime.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    """
    WebSocket connection endpoint.

    Client usage:
        const ws = new WebSocket(`ws://localhost:8000/ws?token=${jwt_token}`);
        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'job.progress') {
                updateProgressBar(data.data.progress_percent);
            }
        };
    """
    # Authenticate via JWT from query parameter
    try:
        from api.auth import decode_access_token
        payload = decode_access_token(token)
        user_id = UUID(payload["sub"])
        company_id = UUID(payload["company_id"])
    except Exception as e:
        logger.warning(f"WebSocket auth failed: {e}")
        await websocket.close(code=1008)  # Policy violation
        return

    # Accept and register connection
    await manager.connect(websocket, user_id, company_id)

    try:
        # Keep connection alive — server pushes events, client sends pings
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id, company_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, user_id, company_id)
