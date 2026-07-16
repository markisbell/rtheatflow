"""Session recorder: every published simulation step, appended to CSV on disk.

Blueprint ``recorder.py`` port (SPEC §4.6, §9.1). The recorder consumes
exactly the payload that goes out on the WebSocket (``StateStore``'s
*projected* frame) — so it automatically respects strict observability:
what never reaches the wire never reaches the CSVs either. Rows are appended
incrementally by a dedicated writer thread fed through a queue, so the
engine loop is never blocked and memory stays flat however long the run
gets (``record()`` is a plain ``queue.put`` — SPEC §6 "sink never blocks").

Layout of one recording (``data/recordings/<id>/``):

  metadata.json                recipe + stats (network, loadgen, sensors,
                               heating-curve/Δp/plant config, engine clock,
                               expose_ground_truth — the reproducibility recipe)
  summary.csv                  one row per step: the truth summary (+ solver)
  observed_summary.csv         aggregates over metered elements only
  junctions.csv, pipes.csv, consumers.csv, producers.csv, storages.csv
                               tidy long format (day, step, time, element, ...)
  controls.csv                 flattened heating-curve/Δp settings + weather
  measurements_consumers.csv, measurements_nodes.csv, measurements_plant.csv
                               the Gemessen layer (heat meters, T/p sensors,
                               plant SCADA)

Files appear lazily on their first row: a net without storages produces no
``storages.csv``, and in strict mode no truth file exists at all. CSVs are
standard dialect (comma separator, dot decimals, UTF-8) — made for pandas &
Co.; ``None`` becomes an empty field, booleans become 0/1. Duplicate
``(day, step)`` publishes (e.g. around a pause) are dropped; a backward seek
legitimately repeats ``(day, step)`` keys later in the file — the wall-clock
``timestamp`` column keeps the recording order unambiguous.

Dict-valued payload keys (``summary``, ``observed_summary``, ``controls`` +
``weather``, ``measurements.plant``) get their column set from the FIRST row
of the recording (sorted) and every later row is emitted against that stored
header — a mid-recording config change can never shift columns.
"""
from __future__ import annotations

import csv
import json
import logging
import queue
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# column plans per file: (payload list key, csv name, element columns) —
# fixed tuples mirroring the SPEC §6 wire lists 1:1.
_ELEMENT_FILES = (
    ("junctions", "junctions.csv",
     ("id", "name", "side", "p_bar", "t_c")),
    ("pipes", "pipes.csv",
     ("id", "trench", "side", "mdot_kg_per_s", "v_m_per_s", "t_from_c",
      "t_to_c", "q_loss_kw", "dp_bar")),
    ("consumers", "consumers.csv",
     ("id", "name", "node", "kind", "q_kw", "mdot_kg_per_s", "t_supply_c",
      "t_return_c", "dp_bar")),
    ("producers", "producers.csv",
     ("id", "kind", "name", "node", "q_kw", "t_flow_c", "plift_bar",
      "pump_el_kw", "plant_kind", "cop", "p_el_kw", "p_fuel_kw", "t_cold_c",
      "mdot_kg_per_s")),
    ("storages", "storages.csv",
     ("id", "name", "node", "soc_kwh", "capacity_kwh", "power_kw", "mode",
      "active", "q_kw")),
)
# the Gemessen layer (measurements.consumers / measurements.nodes lists)
_MEAS_FILES = (
    ("consumers", "measurements_consumers.csv",
     ("id", "name", "node", "q_kw", "mdot_kg_per_s", "t_supply_c",
      "t_return_c", "dp_bar")),
    ("nodes", "measurements_nodes.csv",
     ("node", "p_supply_bar", "p_return_bar", "t_supply_c", "t_return_c")),
)
_STEP_COLS = ("day", "step", "time_of_day", "timestamp")
#: summary.csv carries the solver verdict next to the physics aggregates
_SUMMARY_EXTRA = ("converged", "solver_status", "solve_ms")
_FLUSH_EVERY = 50   # steps between fsync-less flushes (bounded loss on crash)


class Recorder:
    """Records the published step stream of ONE configuration to disk."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._active = False
        self._id: str | None = None
        self._dir: Path | None = None
        self._meta: dict[str, Any] = {}
        self._started: float = 0.0
        self._steps = 0
        self._last_key: tuple | None = None
        # name -> (fh, writer, header) — header stored so dict-valued files
        # keep their first-row column set for the whole recording
        self._files: dict[str, tuple[TextIO, Any, tuple[str, ...]]] = {}

    # -- lifecycle ----------------------------------------------------------- #

    def start(self, meta: dict[str, Any], name: str | None = None) -> dict:
        if self._active:
            raise RuntimeError("a recording is already active")
        rid = time.strftime("%Y%m%d-%H%M%S")
        if name:
            slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:40]
            if slug:
                rid = f"{rid}_{slug}"
        d = self.root / rid
        n = 1
        while d.exists():                       # same-second restart
            n += 1
            d = self.root / f"{rid}-{n}"
        d.mkdir(parents=True)
        self._id = d.name
        self._dir = d
        self._meta = meta
        self._started = time.time()
        self._steps = 0
        self._last_key = None
        self._files = {}
        self._q = queue.Queue()
        self._active = True
        self._thread = threading.Thread(
            target=self._run, name="rtheatflow-recorder", daemon=True)
        self._thread.start()
        log.info("recording started: %s", self._id)
        return self.status()

    def record(self, payload: dict[str, Any]) -> None:
        """Enqueue one published frame (called on the event loop — never
        blocks; the writer thread does all disk work)."""
        if self._active:
            self._q.put(payload)

    def stop(self) -> dict | None:
        """Finish the active recording: drain the queue, close the files and
        write metadata.json. Returns the final status (None if idle)."""
        if not self._active:
            return None
        self._active = False
        self._q.put(None)                       # sentinel
        if self._thread is not None:
            self._thread.join(timeout=30)
        final = {
            **self._meta,
            "id": self._id,
            "started": _iso(self._started),
            "ended": _iso(time.time()),
            "steps_recorded": self._steps,
            "files": sorted(p.name for p in self._dir.iterdir()),
        }
        (self._dir / "metadata.json").write_text(
            json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("recording stopped: %s (%d steps)", self._id, self._steps)
        out = self.status()
        self._id = None
        self._dir = None
        return out

    def status(self) -> dict:
        return {
            "active": self._active,
            "id": self._id,
            "steps": self._steps,
            "started": _iso(self._started) if self._id else None,
            "bytes": _dir_bytes(self._dir) if self._dir else 0,
        }

    # -- stored recordings ---------------------------------------------------- #

    def list(self) -> list[dict]:
        out = []
        if not self.root.is_dir():
            return out
        for d in sorted(self.root.iterdir()):
            mf = d / "metadata.json"
            if not d.is_dir() or not mf.is_file():
                continue
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — half-written/corrupt: still list it
                meta = {"id": d.name}
            out.append({"id": d.name,
                        "network": (meta.get("network") or {}).get("name"),
                        "started": meta.get("started"),
                        "ended": meta.get("ended"),
                        "steps": meta.get("steps_recorded"),
                        "bytes": _dir_bytes(d)})
        return out

    def dir_of(self, rid: str) -> Path:
        """Validated path of a stored recording (guards path traversal)."""
        if not _ID_RE.match(rid):
            raise KeyError(rid)
        d = self.root / rid
        if not d.is_dir() or not (d / "metadata.json").is_file():
            raise KeyError(rid)
        return d

    def pack(self, rid: str) -> Path:
        """ZIP a finished recording (cached — recordings are immutable)."""
        d = self.dir_of(rid)
        zp = self.root / f"{rid}.zip"
        if zp.is_file() and zp.stat().st_mtime >= (d / "metadata.json").stat().st_mtime:
            return zp
        tmp = zp.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(d.iterdir()):
                z.write(p, arcname=f"{rid}/{p.name}")
        tmp.replace(zp)
        return zp

    def delete(self, rid: str) -> None:
        d = self.dir_of(rid)
        shutil.rmtree(d)
        (self.root / f"{rid}.zip").unlink(missing_ok=True)

    # -- writer thread --------------------------------------------------------- #

    def _run(self) -> None:
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                try:
                    self._write(item)
                except Exception:  # noqa: BLE001 — a bad frame must not kill the run
                    log.exception("recorder failed to write a step; skipping it")
        finally:
            for fh, _, _ in self._files.values():
                try:
                    fh.close()
                except Exception:  # noqa: BLE001
                    pass
            self._files = {}

    def _w(self, name: str, header: tuple[str, ...]):
        """Writer + stored header for *name* (header written lazily once)."""
        got = self._files.get(name)
        if got is None:
            fh = (self._dir / name).open("w", newline="", encoding="utf-8")
            w = csv.writer(fh)
            w.writerow(header)
            got = self._files[name] = (fh, w, header)
        return got[1], got[2]

    def _dict_row(self, name: str, stamp: list, d: dict,
                  extra: dict | None = None) -> None:
        """One row of a dict-valued payload key; column set = first row."""
        extra = extra or {}
        header = _STEP_COLS + tuple(extra) + tuple(sorted(d))
        w, cols = self._w(name, header)
        merged = {**extra, **d}
        w.writerow(stamp + [_c(merged.get(c)) for c in cols[len(_STEP_COLS):]])

    def _write(self, p: dict[str, Any]) -> None:
        key = (p.get("day"), p.get("step"))
        if key == self._last_key:               # double publish around a pause
            return
        self._last_key = key
        stamp = [p.get("day"), p.get("step"), p.get("time_of_day"),
                 p.get("timestamp")]

        for pkey, fname, cols in _ELEMENT_FILES:
            rows = p.get(pkey)
            if rows:
                w, _ = self._w(fname, _STEP_COLS + cols)
                for r in rows:
                    w.writerow(stamp + [_c(r.get(c)) for c in cols])

        summary = p.get("summary")
        if summary:
            self._dict_row("summary.csv", stamp, summary,
                           extra={c: p.get(c) for c in _SUMMARY_EXTRA})
        observed = p.get("observed_summary")
        if observed:
            self._dict_row("observed_summary.csv", stamp, observed)

        # operator settings row: flattened controls + the weather state
        controls = _flatten(p.get("controls") or {})
        controls.update(_flatten({"weather": p.get("weather") or {}}))
        if controls:
            self._dict_row("controls.csv", stamp, controls)

        meas = p.get("measurements") or {}
        for mkey, fname, cols in _MEAS_FILES:
            rows = meas.get(mkey)
            if rows:
                w, _ = self._w(fname, _STEP_COLS + cols)
                for r in rows:
                    w.writerow(stamp + [_c(r.get(c)) for c in cols])
        plant = meas.get("plant")
        if plant:
            self._dict_row("measurements_plant.csv", stamp, plant)

        self._steps += 1
        if self._steps % _FLUSH_EVERY == 0:
            for fh, _, _ in self._files.values():
                fh.flush()


def _flatten(d: dict, prefix: str = "") -> dict:
    """``{"a": {"b": 1}} -> {"a.b": 1}`` — one level of dotted flattening."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, prefix=f"{key}."))
        else:
            out[key] = v
    return out


def _c(v):
    """CSV cell: None → empty, bools → 0/1 (spreadsheet-friendly)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    return v


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def _dir_bytes(d: Path | None) -> int:
    if d is None or not d.is_dir():
        return 0
    return sum(p.stat().st_size for p in d.iterdir() if p.is_file())
