"""
Public health check — intentionally NOT behind the manager API key, so
container healthchecks, uptime monitors, etc. don't need credentials just
to ask "are you alive". It only ever reveals a worker count, nothing
sensitive.
"""

from fastapi import APIRouter

from app.state import workers

router = APIRouter()


@router.get("/health")
async def health():
    """Matches the Lua client's startup check: GET /health."""
    return {"status": "ok", "workers_connected": len(workers)}
