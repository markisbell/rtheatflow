"""Best-effort enumeration of live ``python -m rtheatflow.main`` processes.

Two consumers, one helper (SPEC §9.3 ops hygiene):

* the **pytest session leak guard** (``tests/conftest.py``) — fails a run that
  leaks a real backend subprocess. The suite is meant to exercise the API
  entirely through the in-process Starlette ``TestClient`` (see
  ``conftest.make_api_client``); *no* fixture spawns ``rtheatflow.main``. The
  guard pins that invariant so a future live-uvicorn helper that forgets to
  ``kill``+``wait`` in finalization is caught instead of leaking an orphan.

* the **startup preflight** (``rtheatflow.main``) — logs a one-line notice when
  other ``rtheatflow.main`` processes are already alive, pointing at
  ``stop_rtheatflow.bat``. It never kills anything: a legitimately running
  sibling instance is fine; the note is conditional operator advice for the
  case a fresh start seems to hang before binding its port (a stray backend
  from an earlier ad-hoc/detached dev run left running — see the 2026-07-17
  dev-log entry).

Cross-platform (Windows ``Get-CimInstance`` / POSIX ``ps``), stdlib-only, and
defensive: any enumeration failure returns an empty set, so this can never
break startup or a test run.
"""
from __future__ import annotations

import os
import subprocess
import sys

#: substring identifying the backend entry point on a process command line
MARKER = "rtheatflow.main"


def live_backend_pids(exclude_self: bool = True) -> set[int]:
    """Return the PIDs of live ``python -m rtheatflow.main`` processes.

    Best-effort: returns an empty set on any enumeration error. ``exclude_self``
    drops the current process (so a backend can enumerate its *siblings*)."""
    try:
        if sys.platform.startswith("win"):
            pids = _win_pids()
        else:
            pids = _posix_pids()
    except Exception:
        return set()
    if exclude_self:
        pids.discard(os.getpid())
    return pids


def _win_pids() -> set[int]:
    # Restrict to python* processes so the CIM query never matches the
    # powershell.exe that runs it (its own command line contains MARKER).
    cmd = (
        "Get-CimInstance Win32_Process | Where-Object { "
        f"$_.Name -like 'python*' -and $_.CommandLine -like '*{MARKER}*' "
        "} | ForEach-Object { $_.ProcessId }"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=30,
    )
    return {int(tok) for tok in out.stdout.split() if tok.strip().isdigit()}


def _posix_pids() -> set[int]:
    out = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=30
    )
    pids: set[int] = set()
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, args = parts
        if not pid_s.isdigit() or MARKER not in args:
            continue
        # require the executable itself to be python — excludes shell wrappers
        # (e.g. ``bash -c '... python -m rtheatflow.main ...'``) that merely
        # mention the marker.
        exe = args.split(None, 1)[0]
        if "python" in os.path.basename(exe).lower():
            pids.add(int(pid_s))
    return pids
