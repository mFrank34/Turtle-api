
import asyncio
import json
import time
from typing import Any, Dict, Optional, Set

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

PING_INTERVAL_SECONDS = 15
DEFAULT_COMMAND_TIMEOUT = 10.0

app = FastAPI(title="TurtleNet Manager API")

# Allow a browser-based dashboard to hit this from anywhere during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# In-memory worker registry
# ---------------------------------------------------------------------------

class Worker:
    """Live state + connection handle for a single turtle/computer."""

    def __init__(self, node_id: str, ws: WebSocket):
        self.node_id = node_id
        self.ws = ws
        self.node_type: str = "unknown"
        self.status: str = "connected"
        self.fuel: Any = None
        self.inventory: Dict[str, Any] = {}
        self.location: Optional[Dict[str, float]] = None
        self.last_command: Optional[str] = None
        self.last_block: Any = None
        self.connected_at: float = time.time()
        self.last_seen: float = time.time()

        # Guards send/await-response so a broadcast ping and a manager command
        # can't both be waiting on the same turtle's single reply at once.
        self.lock = asyncio.Lock()
        self.pending: Optional["asyncio.Future[dict]"] = None

    def summary(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "status": self.status,
            "fuel": self.fuel,
            "inventory": self.inventory,
            "location": self.location,
            "last_command": self.last_command,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "seconds_since_seen": round(time.time() - self.last_seen, 1),
        }

    def detail(self) -> dict:
        d = self.summary()
        d["last_block"] = self.last_block
        return d


workers: Dict[str, Worker] = {}
manager_sockets: Set[WebSocket] = set()


async def broadcast_to_managers(event: dict) -> None:
    """Push a state-change event to every connected manager dashboard/socket."""
    if not manager_sockets:
        return
    dead = []
    payload = json.dumps(event)
    for ws in manager_sockets:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        manager_sockets.discard(ws)


# ---------------------------------------------------------------------------
# REST API for the manager
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Matches the client's startup check: GET /health."""
    return {"status": "ok", "workers_connected": len(workers)}


@app.get("/api/v1/workers")
async def list_workers():
    """Summary of every currently-connected worker."""
    return {node_id: w.summary() for node_id, w in workers.items()}


@app.get("/api/v1/workers/{node_id}")
async def get_worker(node_id: str):
    w = workers.get(node_id)
    if not w:
        raise HTTPException(status_code=404, detail="worker not connected")
    return w.detail()


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="allow")  # lets you pass e.g. slot / count / anything else
    command: str
    timeout: Optional[float] = DEFAULT_COMMAND_TIMEOUT


async def _send_and_await(w: Worker, payload: dict, timeout: float) -> dict:
    async with w.lock:
        loop = asyncio.get_event_loop()
        fut: "asyncio.Future[dict]" = loop.create_future()
        w.pending = fut
        w.last_command = payload.get("command")
        try:
            await w.ws.send_text(json.dumps(payload))
        except Exception as e:
            w.pending = None
            raise HTTPException(status_code=502, detail=f"failed to send to worker: {e}")

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="worker did not respond in time")
        finally:
            w.pending = None
        return result


@app.post("/api/v1/workers/{node_id}/command")
async def send_command(node_id: str, req: CommandRequest):
    """
    Send a single command to one turtle and wait for its reply.

    Example:
        POST /api/v1/workers/turtle_1/command
        {"command": "move_forward"}

        POST /api/v1/workers/turtle_1/command
        {"command": "select_slot", "slot": 3}

        POST /api/v1/workers/turtle_1/command
        {"command": "drop", "count": 32}
    """
    w = workers.get(node_id)
    if not w:
        raise HTTPException(status_code=404, detail="worker not connected")

    payload = req.model_dump(exclude_none=True, exclude={"timeout"})
    return await _send_and_await(w, payload, req.timeout or DEFAULT_COMMAND_TIMEOUT)


@app.post("/api/v1/workers/{node_id}/do/{command}")
async def do_command(
    node_id: str,
    command: str,
    slot: Optional[int] = None,
    count: Optional[int] = None,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
):
    """
    Shorthand for send_command with no JSON body needed — everything is in
    the URL, which is much friendlier to call from a frontend button.

    Examples:
        POST /api/v1/workers/turtle_1/do/move_forward
        POST /api/v1/workers/turtle_1/do/dig
        POST /api/v1/workers/turtle_1/do/select_slot?slot=3
        POST /api/v1/workers/turtle_1/do/drop?count=32
        POST /api/v1/workers/turtle_1/do/get_location
    """
    w = workers.get(node_id)
    if not w:
        raise HTTPException(status_code=404, detail="worker not connected")

    payload: Dict[str, Any] = {"command": command}
    if slot is not None:
        payload["slot"] = slot
    if count is not None:
        payload["count"] = count

    return await _send_and_await(w, payload, timeout)


@app.post("/api/v1/workers/broadcast")
async def broadcast_command(req: CommandRequest):
    """Send the same command to every connected worker in parallel."""
    payload = req.model_dump(exclude_none=True, exclude={"timeout"})
    timeout = req.timeout or DEFAULT_COMMAND_TIMEOUT

    async def run_one(node_id: str, w: Worker):
        try:
            return node_id, await _send_and_await(w, payload, timeout)
        except HTTPException as e:
            return node_id, {"error": e.detail}

    results = await asyncio.gather(*(run_one(nid, w) for nid, w in list(workers.items())))
    return dict(results)


@app.delete("/api/v1/workers/{node_id}")
async def disconnect_worker(node_id: str):
    w = workers.get(node_id)
    if not w:
        raise HTTPException(status_code=404, detail="worker not connected")
    try:
        await w.ws.close()
    except Exception:
        pass
    workers.pop(node_id, None)
    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# WebSocket endpoint the turtles connect to
# ---------------------------------------------------------------------------

@app.websocket("/api/v1/workers/ws/{node_id}")
async def worker_ws(websocket: WebSocket, node_id: str):
    await websocket.accept()
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
                w.node_type = "turtle" if isinstance(w.inventory, dict) and w.inventory and "note" not in w.inventory else w.node_type
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
        workers.pop(node_id, None)
        await broadcast_to_managers({"event": "worker_disconnected", "node_id": node_id, "ts": time.time()})


# ---------------------------------------------------------------------------
# WebSocket endpoint for manager dashboards (optional, live push updates)
# ---------------------------------------------------------------------------

@app.websocket("/api/v1/manager/ws")
async def manager_ws(websocket: WebSocket):
    await websocket.accept()
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


# ---------------------------------------------------------------------------
# Keepalive: ping every worker periodically so state (fuel/inventory) stays
# fresh even when the manager isn't actively issuing commands, and so a
# lost connection is detected quickly.
# ---------------------------------------------------------------------------

async def ping_loop():
    while True:
        await asyncio.sleep(PING_INTERVAL_SECONDS)
        for node_id, w in list(workers.items()):
            if w.lock.locked():
                # A command is already in flight for this worker; don't
                # collide with its expected reply.
                continue
            try:
                await w.ws.send_text(json.dumps({"type": "ping"}))
            except Exception:
                pass


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(ping_loop())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
