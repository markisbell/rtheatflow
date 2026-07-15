"""Entry point: ``PYTHONPATH=src python -m rtheatflow.main`` (SPEC §9.3).

Runs uvicorn on ``RTHEATFLOW_HOST``/``RTHEATFLOW_PORT`` (default
127.0.0.1:8000 — bind IPv4 explicitly; Windows resolves ``localhost`` to
IPv6 first and an IPv4-only uvicorn refuses, SPEC §7).
"""
from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "rtheatflow.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
