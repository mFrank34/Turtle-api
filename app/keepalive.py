"""
Background task: every PING_INTERVAL_SECONDS, poke every connected worker
so its connection stays alive and its fuel/inventory state stays fresh even
when the manager isn't actively sending it commands.
"""

import asyncio
import json

from app.config import PING_INTERVAL_SECONDS
from app.state import workers


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
