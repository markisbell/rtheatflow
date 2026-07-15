"""API-surface pinning (SPEC §11, §12 M2 acceptance).

The complete route inventory is pinned here: **any** route added, removed,
renamed or re-methoded must force an edit of this file (blueprint's
distinctive test pattern). The generated ``docs/API.md`` is checked for
staleness against the same inventory.
"""
from __future__ import annotations

import importlib.util

from conftest import REPO_ROOT, make_settings

from rtheatflow.api import create_app
from rtheatflow.api.runtime import API_VERSION

# ---------------------------------------------------------------------------
# THE M2 SURFACE. Deliberately exhaustive and alphabetical — change the API,
# change this list, consciously. (SPEC §7; /dpcontrol, storage, consumers,
# sensors, networks, scenarios, recording arrive in M4/M5/M6.)
# ---------------------------------------------------------------------------
EXPECTED = {
    ("DELETE", "/producer/{producer_id}"),
    ("DELETE", "/weather/override"),
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/heatingcurve"),
    ("GET", "/history"),
    ("GET", "/network"),
    ("GET", "/producers"),
    ("GET", "/state"),
    ("GET", "/status"),
    ("GET", "/weather"),
    ("POST", "/control/interval"),
    ("POST", "/control/pause"),
    ("POST", "/control/resume"),
    ("POST", "/control/seek"),
    ("POST", "/control/seekday"),
    ("POST", "/control/start"),
    ("POST", "/heatingcurve"),
    ("POST", "/producer"),
    ("PUT", "/weather/override"),
    ("WS", "/ws"),
}


def _walk_ws_routes(routes) -> list:
    """FastAPI ≥0.139 nests included routers lazily (_IncludedRouter)."""
    found = []
    for route in routes:
        if type(route).__name__.endswith("WebSocketRoute"):
            found.append(route)
        inner = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None)
        if inner:
            found.extend(_walk_ws_routes(inner))
    return found


def inventory(app) -> set[tuple[str, str]]:
    spec = app.openapi()
    routes = {(m.upper(), path)
              for path, methods in spec["paths"].items() for m in methods}
    routes |= {("WS", r.path) for r in _walk_ws_routes(app.routes)}
    return routes


def test_route_inventory_is_pinned():
    app = create_app(make_settings())
    actual = inventory(app)
    added = actual - EXPECTED
    removed = EXPECTED - actual
    assert not added and not removed, (
        f"API surface changed — update EXPECTED consciously.\n"
        f"  added:   {sorted(added)}\n  removed: {sorted(removed)}")


def test_api_version_reported():
    assert API_VERSION == "0.2.0"
    app = create_app(make_settings())
    assert app.version == API_VERSION


def test_generated_api_md_is_current():
    """docs/API.md is generated (scripts/gen_api_doc.py) — never stale."""
    spec = importlib.util.spec_from_file_location(
        "gen_api_doc", REPO_ROOT / "scripts" / "gen_api_doc.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    expected_md = gen.render(gen.collect(create_app(make_settings())))
    on_disk = (REPO_ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    assert on_disk.replace("\r\n", "\n") == expected_md.replace("\r\n", "\n"), (
        "docs/API.md is stale — regenerate: python scripts/gen_api_doc.py")
