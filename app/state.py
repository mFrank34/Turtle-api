"""
In-memory state for the whole server: one Worker object per connected
turtle/computer, plus the set of manager dashboards listening for live
updates. Nothing here talks HTTP or WebSocket protocol directly — routes.py,
workers_ws.py and manager_ws.py do that and just read/write these objects.
"""

import asyncio
import json
import time
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket


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

        # Guards send/await-response so a keepalive ping and a manager
        # command can't both be waiting on this turtle's one reply at once.
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
            "online": True,
        }

    def detail(self) -> dict:
        d = self.summary()
        d["last_block"] = self.last_block
        return d


# node_id -> Worker, for every currently-connected turtle/computer.
workers: Dict[str, Worker] = {}

# node_id -> last known detail(), for workers that have connected at some
# point but aren't connected right now. Populated on disconnect and loaded
# from disk at startup by app/persistence.py, so the manager still has
# something to show immediately after a restart.
last_known: Dict[str, dict] = {}

# Manager dashboards subscribed to the live update feed (/api/v1/manager/ws).
manager_sockets: Set[WebSocket] = set()


async def broadcast_to_managers(event: dict) -> None:
    """Push a state-change event to every connected manager dashboard."""
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
