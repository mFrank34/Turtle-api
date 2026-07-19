"""
Persists worker state to a JSON file on disk (TURTLENET_DATA_DIR, defaults
to ./data locally and /data in the Docker image), so the fleet's last-known
state survives a server restart instead of only living in RAM.

Nothing here holds a WebSocket — only plain dicts (fuel, inventory, status,
etc.) are ever written out, since a live connection can't be serialized.
"""

import asyncio
import json
import os
from pathlib import Path

from app.state import last_known, workers

DATA_DIR = Path(os.environ.get("TURTLENET_DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "workers.json"


def load_last_known() -> None:
    """Read the last saved snapshot into `last_known` at startup."""
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text())
        for node_id, snapshot in data.items():
            snapshot["online"] = False
            last_known[node_id] = snapshot
        print(f"[TurtleNet] Loaded {len(data)} known worker(s) from {STATE_FILE}")
    except Exception as e:
        print(f"[TurtleNet] Could not read {STATE_FILE}: {e}")


def save_snapshot() -> None:
    """Write current + last-known worker state to disk as JSON.

    Uses a write-then-rename so a crash mid-write can't corrupt the file
    a future startup would try to read.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    snapshot = dict(last_known)
    for node_id, w in workers.items():
        snapshot[node_id] = w.detail()

    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2))
    tmp.replace(STATE_FILE)


async def snapshot_loop(interval_seconds: float = 10.0) -> None:
    """Periodically flush state to disk so a crash doesn't lose everything
    since the last save."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            save_snapshot()
        except Exception as e:
            print(f"[TurtleNet] Failed to write state snapshot: {e}")
