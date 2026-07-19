"""All the tunable knobs live here, so you don't have to hunt through logic
to change a timeout."""

# How often the server pings every connected worker to keep the connection
# alive and refresh its fuel/inventory state.
PING_INTERVAL_SECONDS = 15

# How long a manager's command request waits for the worker's reply before
# giving up, unless overridden per-request with ?timeout= or "timeout" in
# the JSON body.
DEFAULT_COMMAND_TIMEOUT = 10.0
