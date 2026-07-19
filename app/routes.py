"""
Plain HTTP endpoints for the manager side: list workers, inspect one, send
it a command (two flavors — JSON body or URL-only shorthand), broadcast to
everyone, or disconnect one.
"""

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.commands import CommandRequest, send_and_await
from app.config import DEFAULT_COMMAND_TIMEOUT
from app.state import last_known, workers

router = APIRouter()


@router.get("/health")
async def health():
    """Matches the Lua client's startup check: GET /health."""
    return {"status": "ok", "workers_connected": len(workers)}


@router.get("/api/v1/workers")
async def list_workers():
    """Every worker we know about: currently-connected ones from live state,
    plus anything seen before that isn't connected right now (loaded from
    disk, marked online: false)."""
    merged = dict(last_known)
    for node_id, w in workers.items():
        merged[node_id] = w.summary()
    return merged


@router.get("/api/v1/workers/{node_id}")
async def get_worker(node_id: str):
    w = workers.get(node_id)
    if w:
        return w.detail()
    if node_id in last_known:
        return last_known[node_id]
    raise HTTPException(status_code=404, detail="worker not connected")


@router.post("/api/v1/workers/{node_id}/command")
async def send_command(node_id: str, req: CommandRequest):
    """
    Send a single command to one turtle and wait for its reply. Flexible
    form — any JSON field beyond `command`/`timeout` is forwarded as-is.

    Example: {"command": "select_slot", "slot": 3}
    """
    w = workers.get(node_id)
    if not w:
        raise HTTPException(status_code=404, detail="worker not connected")

    payload = req.model_dump(exclude_none=True, exclude={"timeout"})
    return await send_and_await(w, payload, req.timeout or DEFAULT_COMMAND_TIMEOUT)


@router.post("/api/v1/workers/{node_id}/do/{command}")
async def do_command(
    node_id: str,
    command: str,
    slot: Optional[int] = None,
    count: Optional[int] = None,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
):
    """
    Shorthand for send_command with no JSON body — everything lives in the
    URL, which is much friendlier to call from a frontend button.

    Examples:
        POST /api/v1/workers/turtle_1/do/move_forward
        POST /api/v1/workers/turtle_1/do/select_slot?slot=3
        POST /api/v1/workers/turtle_1/do/drop?count=32
    """
    w = workers.get(node_id)
    if not w:
        raise HTTPException(status_code=404, detail="worker not connected")

    payload: Dict[str, Any] = {"command": command}
    if slot is not None:
        payload["slot"] = slot
    if count is not None:
        payload["count"] = count

    return await send_and_await(w, payload, timeout)


@router.post("/api/v1/workers/broadcast")
async def broadcast_command(req: CommandRequest):
    """Send the same command to every connected worker in parallel."""
    payload = req.model_dump(exclude_none=True, exclude={"timeout"})
    timeout = req.timeout or DEFAULT_COMMAND_TIMEOUT

    async def run_one(node_id: str, w):
        try:
            return node_id, await send_and_await(w, payload, timeout)
        except HTTPException as e:
            return node_id, {"error": e.detail}

    results = await asyncio.gather(*(run_one(nid, w) for nid, w in list(workers.items())))
    return dict(results)


@router.delete("/api/v1/workers/{node_id}")
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
