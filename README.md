# TurtleNet Manager API

A small FastAPI backend for your ComputerCraft `TurtleNet` Lua client. It:

- accepts a persistent WebSocket from every turtle/computer at
  `ws://HOST:8000/api/v1/workers/ws/{node_id}` (matches `connect()` in the Lua script)
- answers `GET /health` (matches `wait_for_server()` in the Lua script)
- pings every connected worker every 15s to keep the connection alive and
  refresh its fuel/inventory state
- tracks each worker's live state in memory (status, fuel, inventory, location, last command),
  and periodically persists it to a JSON file so it survives a restart (see below)
- lets a **manager** (you, a script, a dashboard) send a command to one turtle
  (or all of them) over plain HTTP and get the turtle's JSON reply back
- optionally streams every state change to manager dashboards over a second WebSocket
- requires an API key for both the manager API and turtle connections (see below)

## Project layout

```
app/
  main.py         entry point — creates the FastAPI app, wires everything together
  config.py       tunable constants + API keys read from the environment
  auth.py         API key checks for REST routes and WebSocket endpoints
  state.py        Worker model, in-memory registry, manager broadcast helper
  commands.py     "send a command and wait for the reply" logic
  workers_ws.py   the WebSocket endpoint turtles connect to (worker key)
  manager_ws.py   the WebSocket endpoint dashboards can subscribe to (manager key)
  routes.py       the plain HTTP endpoints a manager calls (manager key)
  health.py       the one endpoint that's deliberately public
  keepalive.py    background task that pings every worker periodically
  persistence.py  saves/loads worker state to a JSON file on disk
lua/
  turtlenet_client.lua   the turtle-side client (set CONFIG.host/api_key here)
requirements.txt
Dockerfile
docker-compose.yml
.env.example      copy to .env and fill in real key values
```

Each file has one job, so if you want to, say, change how commands time out
you only need to open `commands.py`; if you want to add a new REST endpoint
you only need to touch `routes.py`.

## Run it

### 0. Set your API keys first
```bash
cp .env.example .env
# then edit .env and set real random values, e.g.:
#   openssl rand -hex 32
```
Put `TURTLENET_WORKER_KEY`'s value into `CONFIG.api_key` in
`lua/turtlenet_client.lua` too — they must match.

### Option A: Docker Compose (recommended)
```bash
docker compose up -d --build
```
This builds the image and starts the API on port `8000`, restarting
automatically if it crashes or the host reboots (`restart: unless-stopped`).
Check it's healthy with:
```bash
docker compose ps
docker compose logs -f
curl http://localhost:8000/health
```
Stop it with `docker compose down`.

### Option B: Plain Python
```bash
pip install -r requirements.txt
export TURTLENET_MANAGER_KEY=...
export TURTLENET_WORKER_KEY=...
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Point your turtles at it by setting, in `lua/turtlenet_client.lua`:

```lua
host = "192.168.10.2:8000",   -- wherever this server actually runs
api_key = "...",              -- must match TURTLENET_WORKER_KEY
```

## State persistence

Worker state (fuel, inventory, status, location, last block seen) is written
to `data/workers.json` (or `/data/workers.json` in the Docker image, if you
mount a volume there — the compose file already does this) every 10 seconds,
and immediately whenever a worker disconnects. It's loaded back on startup.

This means:
- `GET /api/v1/workers` still shows turtles that connected before a restart,
  marked `"online": false`, until they reconnect and get marked `true` again.
- A crash or restart doesn't wipe your fleet's last-known state.

A live WebSocket connection itself obviously can't be saved to disk — only
the plain state data is. When a turtle reconnects, its entry gets replaced
with live state again automatically.

If you don't care about surviving restarts, you can ignore this — it's
transparent and doesn't need any configuration. To change where the file is
written, set the `TURTLENET_DATA_DIR` environment variable (defaults to
`./data` locally, `/data` in the Docker image via the Dockerfile's `ENV`).

## Authentication

Two independent API keys, both read from environment variables (see
`.env.example`):

- **`TURTLENET_MANAGER_KEY`** — required for every endpoint in `routes.py`
  and for `/api/v1/manager/ws`. Send it as an `X-API-Key` header:
  ```bash
  curl -H "X-API-Key: your-manager-key" http://192.168.10.2:8000/api/v1/workers
  ```
  For the manager WebSocket feed, since a browser's native `WebSocket` API
  can't set custom headers, you can pass it as a query param instead:
  ```
  ws://192.168.10.2:8000/api/v1/manager/ws?api_key=your-manager-key
  ```

- **`TURTLENET_WORKER_KEY`** — required for a turtle to connect at all. The
  Lua client sends it as an `X-Api-Key` header when opening the WebSocket
  (see `CONFIG.api_key` in `lua/turtlenet_client.lua`). A connection with a
  missing or wrong key gets accepted, immediately rejected with close code
  `4401`, and logged server-side (`Rejected connection from '<node_id>':
  invalid or missing API key`) — it never gets registered as a worker.

`GET /health` is intentionally the one endpoint that stays public, so
container healthchecks and uptime monitors don't need credentials.

**Leaving either key unset disables that check entirely** — convenient for
local development, but the server prints a loud warning at startup
(`WARNING: TURTLENET_MANAGER_KEY is not set — the manager API is
UNPROTECTED`) so you don't forget to set it before exposing this to the
internet. Given you're already putting this behind nginx + a real domain,
make sure both are set in your `.env` before it's reachable outside your LAN.

## Manager API

### List connected workers
```bash
curl -H "X-API-Key: your-manager-key" http://192.168.10.2:8000/api/v1/workers
```

### Get one worker's full state (includes last inspected block)
```bash
curl -H "X-API-Key: your-manager-key" http://192.168.10.2:8000/api/v1/workers/turtle_1
```

### Send a command — simple way (no JSON body, good for frontend buttons)
```bash
curl -X POST -H "X-API-Key: your-manager-key" http://192.168.10.2:8000/api/v1/workers/turtle_1/do/move_forward
curl -X POST -H "X-API-Key: your-manager-key" http://192.168.10.2:8000/api/v1/workers/turtle_1/do/dig
curl -X POST -H "X-API-Key: your-manager-key" "http://192.168.10.2:8000/api/v1/workers/turtle_1/do/select_slot?slot=3"
curl -X POST -H "X-API-Key: your-manager-key" "http://192.168.10.2:8000/api/v1/workers/turtle_1/do/drop?count=32"
curl -X POST -H "X-API-Key: your-manager-key" http://192.168.10.2:8000/api/v1/workers/turtle_1/do/get_location
```
Everything (command name, `slot`, `count`, `timeout`) lives in the URL, so
from JavaScript a button click is just:
```js
fetch(`/api/v1/workers/${id}/do/move_forward`, {
  method: "POST",
  headers: { "X-API-Key": apiKey },
});
```
No JSON body needed either way.

### Send a command — flexible way (JSON body)
```bash
curl -X POST http://192.168.10.2:8000/api/v1/workers/turtle_1/command \
  -H "X-API-Key: your-manager-key" \
  -H "Content-Type: application/json" \
  -d '{"command": "move_forward"}'
```
Use this form if you ever add a command that needs a field beyond `slot`/
`count`/`timeout` — any extra JSON fields you pass are forwarded straight to
`handle_command` in the Lua script. Add `"timeout": 20` (or `?timeout=20` on
the `/do/` route) to wait longer than the 10s default before giving up (e.g.
for long `dig`/`refuel` operations).

### Broadcast a command to every connected turtle
```bash
curl -X POST http://192.168.10.2:8000/api/v1/workers/broadcast \
  -H "X-API-Key: your-manager-key" \
  -H "Content-Type: application/json" \
  -d '{"command": "refuel"}'
```
Runs on all workers in parallel and returns `{node_id: response, ...}`.

### Disconnect a worker from the server side
```bash
curl -X DELETE -H "X-API-Key: your-manager-key" http://192.168.10.2:8000/api/v1/workers/turtle_1
```

### Live dashboard feed (optional)
Connect a WebSocket client to `ws://HOST:8000/api/v1/manager/ws?api_key=your-manager-key`
to receive a push message every time a worker connects, disconnects, or
sends an update — useful for building a live map/dashboard instead of
polling `/api/v1/workers`.

## How command/response matching works

Each worker connection has one in-flight "pending" slot. When you POST a
command, the server sends it down that turtle's WebSocket and waits for the
very next non-handshake message from it, then returns that as your HTTP
response. Since each turtle processes one command at a time in its main loop,
this lines up correctly. The keepalive ping skips any worker that currently
has a command in flight, so it won't steal that reply.

## Notes / things you may want to extend

- `node_type` is guessed from the handshake inventory shape; feel free to
  have the Lua client send `node_type = CONFIG.node_type` explicitly in its
  handshake payload and read `data.node_type` server-side instead.
- Right now both keys are single shared secrets. If you want per-turtle or
  per-user keys later, `app/auth.py` is the only file that needs to change —
  swap the equality check for a lookup against a set/dict of valid keys.
