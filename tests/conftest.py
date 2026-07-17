"""Shared fixtures: the Appendix A five-file bundle + env-isolated settings."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from rtheatflow.config import Settings
from rtheatflow.data_loader import load_network
from rtheatflow.proc_guard import live_backend_pids

REPO_ROOT = Path(__file__).resolve().parents[1]
APPENDIX_A_DIR = REPO_ROOT / "data" / "networks" / "appendix_a"


@pytest.fixture(scope="session", autouse=True)
def _no_leaked_backend_processes():
    """Session leak guard: the suite must spawn **no** ``rtheatflow.main``.

    Every test drives the API through the in-process ``TestClient`` (see
    ``make_api_client``); nothing here launches a real uvicorn subprocess. This
    guard snapshots the live ``rtheatflow.main`` PIDs at session start and fails
    the run if any *new* one is still alive at teardown — catching a future
    live-server fixture that forgets to kill+wait its process (the leak that
    stranded three orphaned backends on 2026-07-17). Pre-existing backends (a
    dev instance, a parallel agent's) are in the baseline and never flagged;
    the check is best-effort and no-ops if process enumeration is unavailable.
    Runs even when tests fail (fixture finalizer), not on hard interrupt."""
    before = live_backend_pids()
    yield
    leaked = live_backend_pids() - before
    assert not leaked, (
        f"test session leaked rtheatflow.main process(es) {sorted(leaked)}: a "
        "fixture spawned a real backend and did not terminate it. Use the "
        "in-process TestClient; if a real server is truly needed, kill+wait on "
        "an ephemeral port in fixture finalization (SPEC §9.3 ops hygiene)."
    )


def make_settings(**overrides) -> Settings:
    """Settings isolated from any local .env / environment drift."""
    return Settings(_env_file=None, **overrides)


def make_api_client(**settings_overrides) -> TestClient:
    """TestClient on the Appendix A fixture; use as a context manager so the
    lifespan (network load, engine construction, autostart) actually runs.

    Defaults: fast ticks (0.02 s), no autostart — tests opt in explicitly.
    """
    from rtheatflow.api import create_app  # deferred: fastapi import is slow

    defaults: dict = dict(autostart=False, step_interval_seconds=0.02)
    defaults.update(settings_overrides)
    settings = make_settings(**defaults)
    return TestClient(create_app(settings, network_dir=APPENDIX_A_DIR))


def wait_for(predicate, timeout: float = 60.0, poll: float = 0.02):
    """Poll *predicate* until truthy (returning its value) or fail.

    Generous default timeout: the very first solve in a pytest process pays
    the numba JIT warm-up (documented M1, ~seconds)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(poll)
    raise AssertionError(f"condition not met within {timeout}s: {predicate}")


@pytest.fixture(scope="session")
def appendix_a_inputs():
    """The SPEC Appendix A 3-consumer loop, loaded through the contract."""
    return load_network(APPENDIX_A_DIR)


@pytest.fixture()
def settings() -> Settings:
    return make_settings()


@pytest.fixture()
def appendix_a_docs() -> dict[str, dict]:
    """Fresh mutable dicts of the five fixture documents (for negative tests)."""
    docs = {}
    for name in ("network_structure", "pipes", "consumers", "producers", "weather"):
        with open(APPENDIX_A_DIR / f"{name}.json", encoding="utf-8") as fh:
            docs[name] = json.load(fh)
    return docs
