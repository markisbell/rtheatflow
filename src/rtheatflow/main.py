"""Entry point: ``PYTHONPATH=src python -m rtheatflow.main`` (SPEC §9.3).

Runs uvicorn on ``RTHEATFLOW_HOST``/``RTHEATFLOW_PORT`` (default
127.0.0.1:8001 — bind IPv4 explicitly; Windows resolves ``localhost`` to
IPv6 first and an IPv4-only uvicorn refuses, SPEC §7; 8001 is the sibling
port next to netzsim's 8000).
"""
from __future__ import annotations

import logging

import uvicorn

from .config import get_settings
from .proc_guard import live_backend_pids

log = logging.getLogger("rtheatflow.main")


def _preflight_stray_backends() -> None:
    """Best-effort: note sibling ``rtheatflow.main`` processes before we start.

    uvicorn runs the app lifespan (network load + net build) *before* binding
    the port, so a fresh start that "hangs before binding" is stuck in startup,
    not fighting for the socket. Stray backends left running from an earlier
    ad-hoc/detached dev run are the usual context for that confusion
    (2026-07-17 dev-log). We never kill anything here — a legitimate sibling
    instance is fine; this is conditional advice, not an alarm."""
    try:
        pids = live_backend_pids()
    except Exception:
        return
    if pids:
        log.warning(
            "%d other rtheatflow.main process(es) already running: %s. That is "
            "fine for a deliberate second instance; but if THIS start hangs "
            "before binding the port, one of them may be a stray from an "
            "earlier dev run — clear them with stop_rtheatflow.bat.",
            len(pids), sorted(pids),
        )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    _preflight_stray_backends()
    uvicorn.run(
        "rtheatflow.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
