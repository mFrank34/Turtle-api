"""
Minimal API-key auth, two independent keys:

  - TURTLENET_MANAGER_KEY protects the manager-facing REST API
    (app/routes.py) and the dashboard push feed (app/manager_ws.py).
  - TURTLENET_WORKER_KEY protects the WebSocket endpoint turtles connect to
    (app/workers_ws.py), so a random client can't pretend to be a turtle.

Both are sent as the `X-API-Key` header. WebSocket clients that can't set
custom headers (e.g. a browser's native WebSocket API) can instead pass
`?api_key=...` in the URL.

Leaving a key unset disables that check entirely — handy for local dev,
but main.py prints a warning at startup so it's obvious if that's still
the case in production.
"""

from typing import Optional

from fastapi import Header, HTTPException, WebSocket, status

from app.config import MANAGER_API_KEY, WORKER_API_KEY


def require_manager_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    """FastAPI dependency for REST endpoints — raise 401 on a bad/missing key."""
    if not MANAGER_API_KEY:
        return
    if x_api_key != MANAGER_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key")


def _extract_key(websocket: WebSocket) -> Optional[str]:
    return websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")


def manager_key_ok(websocket: WebSocket) -> bool:
    if not MANAGER_API_KEY:
        return True
    return _extract_key(websocket) == MANAGER_API_KEY


def worker_key_ok(websocket: WebSocket) -> bool:
    if not WORKER_API_KEY:
        return True
    return _extract_key(websocket) == WORKER_API_KEY
