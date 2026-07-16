"""Generate docs/API.md from the live FastAPI route table (SPEC §9.1/§9.3).

Blueprint convention: the API reference is **generated, never hand-edited** —
run this script after any route change and commit the result. The
API-surface pinning test (`tests/test_api_surface.py`) is the enforcement
twin: both consume the same route inventory.

Usage:  python scripts/gen_api_doc.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from rtheatflow.api import create_app  # noqa: E402
from rtheatflow.api.runtime import API_VERSION  # noqa: E402

OUT = REPO_ROOT / "docs" / "API.md"

HEADER = f"""# rtheatflow API reference

> **Generated** by `scripts/gen_api_doc.py` — do not edit by hand.
> API version **{API_VERSION}** · interactive docs at `/docs` (Swagger) when the
> backend runs · default bind `127.0.0.1:8000`, no auth (teaching tool).

The single wire format is the projected `StepResult` (SPEC §6): `/state`,
`/history` items and every `WS /ws` message share one `asdict()` + projection
path. In strict mode (`RTHEATFLOW_EXPOSE_GROUND_TRUTH=false`) the
ground-truth keys (`junctions`, `pipes`, `consumers`, `summary`) are stripped
and the free-text `error` detail is blanked; `measurements` /
`observed_summary` (the operator view) always remain.

**Error-code conventions** (SPEC §7): `400` validation/import rejection ·
`404` missing resource or `/state` before the first solve · `409` conflicts
(second pressure slack, slack removal, export running) · `422` semantic
limits (weather override out of range) · `500` internal failures only —
**solver non-convergence is data (`converged=false` frames), never a 500.**
"""


def _walk_ws_routes(routes) -> list:
    """Recursively find WebSocket routes (FastAPI ≥0.139 nests included
    routers lazily as ``_IncludedRouter``; expand anything with ``.routes``)."""
    found = []
    for route in routes:
        name = type(route).__name__
        if name.endswith("WebSocketRoute"):
            found.append(route)
        inner = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None)
        if inner:
            found.extend(_walk_ws_routes(inner))
    return found


def collect(app) -> list[tuple[str, str, str, str, str]]:
    """(tag, method, path, summary, description) per route.

    REST routes come from the OpenAPI schema (version-stable across FastAPI
    releases); the WebSocket route — invisible to OpenAPI — is walked out of
    the (possibly lazily nested) route table.
    """
    rows = []
    spec = app.openapi()
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            tag = (op.get("tags") or ["misc"])[0]
            summary = op.get("summary") or ""
            desc = " ".join((op.get("description") or "").split())
            rows.append((tag, method.upper(), path, summary, desc))
    for ws_route in _walk_ws_routes(app.routes):
        doc = (ws_route.endpoint.__doc__ or "").strip().splitlines()
        summary = doc[0].strip() if doc else ""
        desc = " ".join(" ".join(doc[1:]).split())
        rows.append(("core", "WS", ws_route.path, summary, desc))
    # stable order: tag first-seen, then path, then method
    tag_order = {t: i for i, t in enumerate(
        dict.fromkeys(tag for tag, *_ in rows))}
    rows.sort(key=lambda r: (tag_order[r[0]], r[2], r[1]))
    return rows


def render(rows) -> str:
    tags: list[str] = []
    for tag, *_ in rows:
        if tag not in tags:
            tags.append(tag)
    parts = [HEADER]
    for tag in tags:
        parts.append(f"\n## {tag}\n")
        parts.append("| Method | Path | Summary |")
        parts.append("|---|---|---|")
        tag_rows = [r for r in rows if r[0] == tag]
        for _, method, path, summary, _desc in tag_rows:
            parts.append(f"| `{method}` | `{path}` | {summary} |")
        details = [(m, p, d) for _, m, p, _s, d in tag_rows if d]
        if details:
            parts.append("")
            for method, path, desc in details:
                parts.append(f"- **`{method} {path}`** — {desc}")
    parts.append("")
    return "\n".join(parts)


def main() -> None:
    app = create_app()
    md = render(collect(app))
    OUT.write_text(md, encoding="utf-8", newline="\n")
    n = sum(1 for r in collect(app))
    print(f"wrote {OUT.relative_to(REPO_ROOT)} ({n} routes, API {API_VERSION})")


if __name__ == "__main__":
    main()
