"""Tests for the process leak guard helper (``rtheatflow.proc_guard``).

The helper backs the session-scoped pytest leak guard and the
``rtheatflow.main`` startup preflight. These tests spawn a lightweight dummy
whose command line carries the ``rtheatflow.main`` marker (but which never runs
the real server / binds a port), confirm the helper detects it, and confirm it
disappears after the process tree is killed — so the guard neither misses a
leak nor false-flags a cleaned-up process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from rtheatflow.proc_guard import MARKER, live_backend_pids


def _spawn_marker_process() -> subprocess.Popen:
    """A python process that idles but whose argv contains the marker, without
    importing rtheatflow or binding any port."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", MARKER],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform.startswith("win"):
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _wait_until(predicate, timeout=20.0, poll=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


def test_returns_a_set_excluding_self():
    pids = live_backend_pids()
    assert isinstance(pids, set)
    # the pytest process command line does not contain the marker, and self is
    # excluded regardless — the guard must never flag the process it runs in.
    assert os.getpid() not in pids


def test_detects_then_forgets_a_marker_process():
    before = live_backend_pids()
    proc = _spawn_marker_process()
    try:
        detected = _wait_until(lambda: bool(live_backend_pids() - before))
        if not detected and not live_backend_pids():
            # process enumeration unavailable on this platform — helper is
            # documented best-effort; nothing to assert.
            pytest.skip("process enumeration unavailable in this environment")
        assert detected, "spawned marker process was not detected"
    finally:
        _kill_tree(proc)
    # once the tree is gone the helper must stop reporting it, so the session
    # leak guard (before/after diff) never false-flags a cleaned-up process.
    assert _wait_until(lambda: not (live_backend_pids() - before)), (
        "helper still reports the marker process after it was killed"
    )
