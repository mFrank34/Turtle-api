"""
Everything about sending a command to a worker and matching up its reply
lives here, so routes.py stays a thin list of endpoints.
"""

import asyncio
import json
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import DEFAULT_COMMAND_TIMEOUT
from app.state import Worker


class CommandRequest(BaseModel):
    """Body for POST /api/v1/workers/{id}/command. Extra fields (slot,
    count, or anything you add to the Lua client later) pass straight
    through to the worker."""

    model_config = ConfigDict(extra="allow")
    command: str
    timeout: Optional[float] = DEFAULT_COMMAND_TIMEOUT


async def send_and_await(w: Worker, payload: dict, timeout: float) -> dict:
    """
    Send `payload` down a worker's WebSocket and wait for its next reply.

    Each worker only ever has one command in flight at a time (that's what
    w.lock enforces), because the Lua client itself processes commands one
    at a time — so "the next message we get back" is guaranteed to be the
    reply to what we just sent.
    """
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
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="worker did not respond in time")
        finally:
            w.pending = None
