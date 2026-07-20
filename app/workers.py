"""
The WebSocket endpoint turtles/computers connect to. This is a straight
translation of the Lua client's connect() + main receive loop:

  - it sends a handshake ({"status": "connected", "fuel": ..., "inventory": ...})
  - then it sends either a ping reply or a command result for every message
    after that

Requires the worker API key (see app/auth.py) so a random client can't
pretend to be a turtle. The Lua client sends this as an X-Api-Key header
when it opens the connection.
"""

import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import worker_key_ok
from app.persistence import save_snapshot
from app.state import Worker, broadcast_to_managers, last_known, workers

router = APIRouter()


@router.websocket("/api/v1/workers/ws/{node_id}")
async def worker_ws(websocket: WebSocket, node_id: str):
    await websocket.accept()

    if not worker_key_ok(websocket):
        print(f"[TurtleNet] Rejected connection from '{node_id}': invalid or missing API key")
        await websocket.close(code=4401, reason="invalid or missing API key")
        return

    w = Worker(node_id, websocket)
    workers[node_id] = w
    print(f"[TurtleNet] Worker connected: {node_id}")
    await broadcast_to_managers({"event": "worker_connected", "node_id": node_id, "ts": time.time()})

    try:
        while True:
            raw = await websocket.receive_text()
            w.last_seen = time.time()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[TurtleNet] Bad JSON from {node_id}: {raw!r}")
                continue

            # Initial handshake sent right after connect() in the Lua client.
            if data.get("status") == "connected" and "command" not in data:
                w.fuel = data.get("fuel", w.fuel)
                w.inventory = data.get("inventory", w.inventory)
                w.status = "connected"
                await broadcast_to_managers({"event": "handshake", "node_id": node_id, "data": data})
                continue

            # Every other message is either a ping reply or a command result.
            w.status = data.get("status", w.status)
            w.fuel = data.get("fuel", w.fuel)
            if data.get("inventory") is not None:
                w.inventory = data.get("inventory")
            if data.get("block") is not None:
                w.last_block = data.get("block")
            if data.get("location") is not None:
                w.location = data.get("location")

            if w.pending is not None and not w.pending.done():
                w.pending.set_result(data)

            await broadcast_to_managers({"event": "update", "node_id": node_id, "data": data})

    except WebSocketDisconnect:
        print(f"[TurtleNet] Worker disconnected: {node_id}")
    except Exception as e:
        print(f"[TurtleNet] Worker {node_id} error: {e}")
    finally:
        if w.pending is not None and not w.pending.done():
            w.pending.set_exception(RuntimeError("worker disconnected before responding"))

        detail = w.detail()
        detail["online"] = False
        last_known[node_id] = detail
        workers.pop(node_id, None)

        try:
            save_snapshot()
        except Exception as e:
            print(f"[TurtleNet] Failed to persist state on disconnect: {e}")

        await broadcast_to_managers({"event": "worker_disconnected", "node_id": node_id, "ts": time.time()})
