# TurtleNet Manager API

A small FastAPI backend for your ComputerCraft `TurtleNet` Lua client. It:

- accepts a persistent WebSocket from every turtle/computer at
  `ws://HOST:8000/api/v1/workers/ws/{node_id}` (matches `connect()` in the Lua script)
- answers `GET /health` (matches `wait_for_server()` in the Lua script)
- pings every connected worker every 15s to keep the connection alive and
  refresh its fuel/inventory state
- tracks each worker's live state in memory (status, fuel, inventory, location, last command)
- lets a **manager** (you, a script, a dashboard) send a command to one turtle
  (or all of them) over plain HTTP and get the turtle's JSON reply back
- optionally streams every state change to manager dashboards over a second WebSocket

No database — state lives in memory and resets if the server restarts (turtles
will just reconnect and re-register).

## Project layout

```
app/
  main.py         entry point — creates the FastAPI app, wires everything together
  config.py       tunable constants (ping interval, default timeout)
  state.py        Worker model, in-memory registry, manager broadcast helper
  commands.py     "send a command and wait for the reply" logic
  workers_ws.py   the WebSocket endpoint turtles connect to
  manager_ws.py   the WebSocket endpoint dashboards can subscribe to
  routes.py       the plain HTTP endpoints a manager calls
  keepalive.py    background task that pings every worker periodically
  persistence.py  saves/loads worker state to a JSON file on disk
requirements.txt
Dockerfile
docker-compose.yml
```

Each file has one job, so if you want to, say, change how commands time out
you only need to open `commands.py`; if you want to add a new REST endpoint
you only need to touch `routes.py`.

## Run it

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
python server.py
# or: uvicorn server:app --host 0.0.0.0 --port 8000
```

Point your turtles at it by setting, in the Lua script:

```lua
host = "192.168.10.2:8000",   -- wherever this server actually runs
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

## Manager API

### List connected workers
```bash
curl http://192.168.10.2:8000/api/v1/workers
```

### Get one worker's full state (includes last inspected block)
```bash
curl http://192.168.10.2:8000/api/v1/workers/turtle_1
```

### Send a command — simple way (no JSON body, good for frontend buttons)
```bash
curl -X POST http://192.168.10.2:8000/api/v1/workers/turtle_1/do/move_forward
curl -X POST http://192.168.10.2:8000/api/v1/workers/turtle_1/do/dig
curl -X POST "http://192.168.10.2:8000/api/v1/workers/turtle_1/do/select_slot?slot=3"
curl -X POST "http://192.168.10.2:8000/api/v1/workers/turtle_1/do/drop?count=32"
curl -X POST http://192.168.10.2:8000/api/v1/workers/turtle_1/do/get_location
```
Everything (command name, `slot`, `count`, `timeout`) lives in the URL, so
from JavaScript a button click is just:
```js
fetch(`/api/v1/workers/${id}/do/move_forward`, { method: "POST" });
fetch(`/api/v1/workers/${id}/do/select_slot?slot=3`, { method: "POST" });
```
No headers, no `JSON.stringify`, no body.

### Send a command — flexible way (JSON body)
```bash
curl -X POST http://192.168.10.2:8000/api/v1/workers/turtle_1/command \
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
  -H "Content-Type: application/json" \
  -d '{"command": "refuel"}'
```
Runs on all workers in parallel and returns `{node_id: response, ...}`.

### Disconnect a worker from the server side
```bash
curl -X DELETE http://192.168.10.2:8000/api/v1/workers/turtle_1
```

### Live dashboard feed (optional)
Connect a WebSocket client to `ws://HOST:8000/api/v1/manager/ws` to receive a
push message every time a worker connects, disconnects, or sends an update —
useful for building a live map/dashboard instead of polling `/api/v1/workers`.

## How command/response matching works

Each worker connection has one in-flight "pending" slot. When you POST a
command, the server sends it down that turtle's WebSocket and waits for the
very next non-handshake message from it, then returns that as your HTTP
response. Since each turtle processes one command at a time in its main loop,
this lines up correctly. The keepalive ping skips any worker that currently
has a command in flight, so it won't steal that reply.

## Notes / things you may want to extend

- State is in-memory only — restart the server and workers just re-register
  on reconnect (their client already retries every 5s).
- There's no auth on the manager endpoints. If this is reachable beyond your
  LAN, put it behind a reverse proxy with basic auth or an API key check.
- `node_type` is guessed from the handshake inventory shape; feel free to
  have the Lua client send `node_type = CONFIG.node_type` explicitly in its
  handshake payload and read `data.node_type` server-side instead.
