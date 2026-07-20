"""
Optional push feed for a manager dashboard: connect here and get a message
every time a worker connects, disconnects, or sends an update — instead of
polling GET /api/v1/workers on a timer.

Requires the manager API key, same as app/routes.py. Since a browser's
native WebSocket API can't set custom headers, pass it as
ws://.../api/v1/manager/ws?api_key=... instead of X-API-Key if needed.
"""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import manager_key_ok
from app.state import manager_sockets, workers

router = APIRouter()


@router.websocket("/api/v1/manager/ws")
async def manager_ws(websocket: WebSocket):
    await websocket.accept()

    if not manager_key_ok(websocket):
        await websocket.close(code=4401, reason="invalid or missing API key")
        return

    manager_sockets.add(websocket)
    try:
        await websocket.send_text(json.dumps({
            "event": "init",
            "workers": {nid: w.summary() for nid, w in workers.items()},
        }))
        while True:
            # We don't expect input, but keep the receive loop alive so we
            # notice disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager_sockets.discard(websocket)
