"""All the tunable knobs live here, so you don't have to hunt through logic
to change a timeout."""

import os

# How often the server pings every connected worker to keep the connection
# alive and refresh its fuel/inventory state.
PING_INTERVAL_SECONDS = 15

# How long a manager's command request waits for the worker's reply before
# giving up, unless overridden per-request with ?timeout= or "timeout" in
# the JSON body.
DEFAULT_COMMAND_TIMEOUT = 10.0

# API keys. Leave either unset/empty to disable that check (useful for local
# dev) — main.py prints a loud warning at startup if you do, so you don't
# accidentally leave a production instance open.
MANAGER_API_KEY = os.environ.get("TURTLENET_MANAGER_KEY", "")
WORKER_API_KEY = os.environ.get("TURTLENET_WORKER_KEY", "")
