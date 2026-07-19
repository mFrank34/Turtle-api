"""
Uvicorn's access log logs every single HTTP request — including the
Docker/Podman healthcheck hitting /health every 15s, which drowns out
anything actually worth seeing. This filters those lines out while leaving
every other request (worker connects, manager commands, etc.) logged as
normal.
"""

import logging


class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def silence_health_checks() -> None:
    logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())
