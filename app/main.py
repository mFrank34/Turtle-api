"""
TurtleNet Manager API — entry point.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8000

Everything is split by responsibility:
  config.py      tunable constants
  state.py       Worker model + in-memory registry + manager broadcast
  commands.py    send-a-command-and-wait-for-reply logic
  workers_ws.py  the WebSocket endpoint turtles connect to
  manager_ws.py  the WebSocket endpoint dashboards can subscribe to
  routes.py      the plain HTTP endpoints a manager calls
  keepalive.py   the background ping loop
  persistence.py saves/loads worker state to a JSON file on disk
  logging_config.py  filters noisy /health access-log spam
"""

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.keepalive import ping_loop
from app.logging_config import silence_health_checks
from app.persistence import load_last_known, save_snapshot, snapshot_loop

# 1. Import with unique aliases
from app.manager import router as manager_router
from app.routes import router as rest_router
from app.worker import router as worker_router

# 2. Use the exact alias names here


silence_health_checks()

app = FastAPI(title="TurtleNet Manager API")

# Allow a browser-based dashboard to hit this from anywhere during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(worker_router)
app.include_router(manager_router)

@app.on_event("startup")
async def on_startup():
    load_last_known()
    asyncio.create_task(ping_loop())
    asyncio.create_task(snapshot_loop())


@app.on_event("shutdown")
async def on_shutdown():
    save_snapshot()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
