"""Core endpoints (SPEC §7): monitor, health, status, network, state,
history, manual, WS.

Error-code discipline (blueprint): ``/state`` is **404 before the first
solve** — and *only* then. A non-converged solve still publishes a frame
(``converged=false``); solver trouble is data, never an HTTP error.
"""
from __future__ import annotations

import html as _html
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse

from .runtime import get_app, status_payload

log = logging.getLogger(__name__)

router = APIRouter(tags=["core"])


@router.get("/", response_class=HTMLResponse, include_in_schema=True,
            summary="Built-in HTML live monitor")
def monitor() -> HTMLResponse:
    """Minimal self-contained live monitor fed by ``WS /ws`` (blueprint style)."""
    return HTMLResponse(_MONITOR_HTML)


@router.get("/health", summary="Liveness probe")
def health() -> dict:
    """Cheap liveness check for launchers/containers (no engine access)."""
    from .runtime import API_VERSION
    return {"status": "ok", "name": "rtheatflow", "api_version": API_VERSION}


@router.get("/status", summary="Engine status")
def status() -> dict:
    """Engine clock, run state, interval, active network, latest-frame digest."""
    return status_payload()


@router.get("/network", summary="Static topology")
def network() -> dict:
    """Active network topology: nodes, trenches (shared supply/return geometry
    + per-side pipe element ids), consumers, producers. Rebuilt per request —
    equipment CRUD (M4) changes the consumer/producer inventory live."""
    from .runtime import build_topology
    app = get_app()
    return build_topology(app.network_id, app.sim)


@router.get("/state", summary="Latest solved frame")
def state() -> dict:
    """The latest StepResult wire frame (projected). **404 before the first
    solve**; a failed solve still yields a frame with ``converged=false``."""
    frame = get_app().store.latest_frame()
    if frame is None:
        raise HTTPException(status_code=404, detail="no solved step yet")
    return frame


@router.get("/history", summary="Recent frames")
def history(
    limit: int = Query(default=96, ge=1, le=10000,
                       description="number of most recent frames"),
) -> list[dict]:
    """The most recent frames (oldest first), through the same projection
    path as ``/state``. Bounded by ``RTHEATFLOW_HISTORY_SIZE``."""
    return get_app().store.history_frames(limit)


# ---------------------------------------------------------------------------
# GET /manual — the German Benutzerhandbuch (SPEC §2/§7), rendered as HTML.
# The manual is authored in-repo as Markdown (docs/Benutzerhandbuch.md); the
# renderer below covers exactly the subset the manual uses (headings, lists,
# tables, fenced code, bold/italic/inline code, links, blockquotes, rules) —
# deliberately no third-party markdown dependency for one static document.
# ---------------------------------------------------------------------------

_MANUAL_PATH = Path(__file__).resolve().parents[3] / "docs" / "Benutzerhandbuch.md"

_INLINE_RULES = (
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*([^*\s][^*]*)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)"), r'<a href="\2">\1</a>'),
)


def _inline(text: str) -> str:
    out = _html.escape(text, quote=False)
    for rx, repl in _INLINE_RULES:
        out = rx.sub(repl, out)
    return out


def render_markdown(md: str) -> str:
    """Markdown-subset → HTML body (line-based, no recursion)."""
    out: list[str] = []
    lines = md.replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    para: list[str] = []
    in_list: str | None = None   # "ul" | "ol"

    def flush_para():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para()
            close_list()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            out.append("<pre><code>"
                       + _html.escape("\n".join(code)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_para()
            close_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if stripped in ("---", "***"):
            flush_para()
            close_list()
            out.append("<hr>")
            i += 1
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_para()
            close_list()
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append("<table><thead><tr>"
                           + "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
                           + "</tr></thead><tbody>")
                for r in rows[1:]:
                    out.append("<tr>" + "".join(
                        f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                out.append("</tbody></table>")
            continue
        if stripped.startswith("> "):
            flush_para()
            close_list()
            quote: list[str] = []
            while i < n and lines[i].strip().startswith("> "):
                quote.append(lines[i].strip()[2:])
                i += 1
            out.append(f"<blockquote><p>{_inline(' '.join(quote))}</p></blockquote>")
            continue
        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            flush_para()
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue
        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            flush_para()
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue
        if not stripped:
            flush_para()
            close_list()
            i += 1
            continue
        para.append(stripped)
        i += 1
    flush_para()
    close_list()
    return "\n".join(out)


_MANUAL_CSS = """
  body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 46rem;
         padding: 0 1rem; background: #fff; color: #1c2530; line-height: 1.55; }
  @media (prefers-color-scheme: dark) {
    body { background: #14181d; color: #e6e9ec; }
    a { color: #6cb2ff; } code, pre { background: #1d232b; }
    th, td { border-color: #39424f; } blockquote { border-color: #39424f; }
  }
  h1 { font-size: 1.6rem; border-bottom: 2px solid #d8dde3; padding-bottom: .3rem; }
  h2 { font-size: 1.25rem; margin-top: 2rem; }
  h3 { font-size: 1.05rem; margin-top: 1.4rem; }
  code { background: #f0f2f5; padding: .1em .3em; border-radius: .25em;
         font-size: .9em; }
  pre { background: #f0f2f5; padding: .7em; border-radius: .4em; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  table { border-collapse: collapse; margin: 1em 0; width: 100%; }
  th, td { border: 1px solid #d8dde3; padding: .35em .6em; text-align: left;
           font-size: .92em; }
  blockquote { border-left: 4px solid #d8dde3; margin: 1em 0; padding: .1em 1em;
               opacity: .9; }
"""


@router.get("/manual", summary="Benutzerhandbuch (German user manual)")
def manual(format: str = Query(default="html", pattern="^(html|md)$")):
    """The German user manual, rendered as HTML (``?format=md`` for the raw
    Markdown source). Authored in ``docs/Benutzerhandbuch.md``."""
    if not _MANUAL_PATH.is_file():
        raise HTTPException(404, "Benutzerhandbuch not found "
                                 "(docs/Benutzerhandbuch.md missing)")
    md = _MANUAL_PATH.read_text(encoding="utf-8")
    if format == "md":
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")
    return HTMLResponse(
        "<!DOCTYPE html><html lang=\"de\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>rtheatflow — Benutzerhandbuch</title>"
        f"<style>{_MANUAL_CSS}</style></head><body>"
        f"{render_markdown(md)}</body></html>")


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """One message type: the full projected StepResult per solved step.

    accept → subscribe → send latest if present → receive loop (the client
    sends nothing; receiving only detects disconnect). Dead sockets are
    discarded by the store on send failure (SPEC §7).
    """
    app = get_app()
    await websocket.accept()
    await app.store.subscribe(websocket)
    try:
        latest = app.store.latest_frame()
        if latest is not None:
            await websocket.send_json(latest)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # broken transport — same as a disconnect
        log.debug("ws receive loop ended abnormally", exc_info=True)
    finally:
        await app.store.unsubscribe(websocket)


# ---------------------------------------------------------------------------
# Built-in monitor page: no assets, no framework — a table fed by /ws.
# Falls back to observed_summary when strict mode withholds summary.
# ---------------------------------------------------------------------------

_MONITOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>rtheatflow monitor</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 46rem;
         background: #14181d; color: #e6e9ec; }
  h1 { font-size: 1.2rem; } h1 small { color: #8a949e; font-weight: normal; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr));
          gap: .6rem; margin-top: 1rem; }
  .tile { background: #1d232b; border-radius: .5rem; padding: .6rem .8rem; }
  .tile .k { color: #8a949e; font-size: .75rem; }
  .tile .v { font-size: 1.25rem; margin-top: .15rem; font-variant-numeric: tabular-nums; }
  #conn { padding: .15rem .5rem; border-radius: .3rem; font-size: .8rem; }
  .ok { background: #1e4620; } .bad { background: #5b1f1f; }
  #clock { color: #8a949e; margin-left: .6rem; }
</style>
</head>
<body>
<h1>rtheatflow <small>live monitor</small>
    <span id="conn" class="bad">connecting…</span><span id="clock"></span></h1>
<div class="grid" id="tiles"></div>
<script>
"use strict";
const TILES = [
  ["solver", f => f.solver_status + (f.converged ? "" : " (not converged)")],
  ["solve [ms]", f => f.solve_ms],
  ["feed-in [kW]", f => s(f).q_feed_kw],
  ["demand [kW]", f => s(f).q_demand_kw ?? s(f).q_demand_metered_kw],
  ["losses [kW]", f => s(f).q_loss_kw],
  ["losses [%]", f => s(f).loss_pct],
  ["pump P_el [kW]", f => s(f).pump_el_kw],
  ["worst-point \\u0394p [bar] (Schlechtpunkt)",
     f => n(s(f).dp_worst_bar) + " @ " + (s(f).worst_consumer ?? "?")],
  ["plant T_flow / T_return [\\u00b0C]",
     f => n(s(f).t_flow_plant_c) + " / " + n(s(f).t_return_plant_c)],
  ["plant mdot [kg/s]", f => s(f).mdot_plant_kg_per_s],
  ["T_amb [\\u00b0C]", f => n(f.weather?.t_amb_c) + (f.weather?.override ? " (override)" : "")],
  ["T_ground [\\u00b0C]", f => f.weather?.t_ground_c],
];
const s = f => f.summary ?? f.observed_summary ?? {};
const n = x => (x === null || x === undefined) ? "\\u2014" : x;
const tiles = document.getElementById("tiles");
for (const [k] of TILES) {
  const d = document.createElement("div"); d.className = "tile";
  d.innerHTML = `<div class="k">${k}</div><div class="v">\\u2014</div>`;
  tiles.appendChild(d);
}
function render(f) {
  document.getElementById("clock").textContent =
    ` day ${f.day} \\u00b7 ${f.time_of_day} \\u00b7 step ${f.step}`;
  const vs = tiles.querySelectorAll(".v");
  TILES.forEach(([_, fn], i) => {
    let v; try { v = fn(f); } catch { v = null; }
    vs[i].textContent = n(v);
  });
}
function connect() {
  const conn = document.getElementById("conn");
  const ws = new WebSocket(
    (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => { conn.textContent = "live"; conn.className = "ok"; };
  ws.onmessage = ev => render(JSON.parse(ev.data));
  ws.onclose = () => {
    conn.textContent = "reconnecting\\u2026"; conn.className = "bad";
    setTimeout(connect, 1500);
  };
}
connect();
</script>
</body>
</html>
"""
