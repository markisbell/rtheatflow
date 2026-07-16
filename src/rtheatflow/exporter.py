"""Bulk export: simulate whole days as fast as possible into a recording pack.

Blueprint ``exporter.py`` port (SPEC §6). The live recorder (recorder.py)
captures what happens while the accelerated clock ticks — fine for
interactive sessions, but waiting wall-clock minutes per simulated day is
pointless when one just wants "3 days of data for the exercise group". The
bulk exporter REPLAYS the current setup offline: a deep copy of the live
Simulator (net, profiles, runtime equipment, storages, controllers, sensor
placement) is driven through ``run_step`` for every step of the requested
days, back to back, and every projected frame is fed into a private
``Recorder`` — so the output pack is **byte-compatible** with a live
recording (same CSVs, same columns, same ``_r()`` rounding, same row order,
same metadata.json recipe, same /recordings listing and ZIP download).
Frames pass through the same strict-mode projection as the live wire
(``StateStore.frame``): in strict mode an export carries no truth either.

Replay semantics (deliberately the LIVE physics, not a day-graph sweep):
the copy is normalized to a **from-midnight replay** by
:meth:`BulkExporter.prepare_replay` — the same convention a fresh scenario
load produces (storages at SoC 0, cold initialization, fresh measurement
windows, a controlled pump released to its file-defined lift) — and then
controllers really regulate (closed loop), storages really integrate across
day boundaries, standard-mode meters really cold-start. The replay is
**quasi-static** like the live loop; the experimental transient flag
(SPEC §3.5) is M7 scope.

One export at a time (the API maps a second start to 409); progress is
polled via ``status()`` (steps done/total, ETA) and a run can be cancelled
between steps (the partial pack is finalized and marked).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from .recorder import Recorder
from .state import StateStore

log = logging.getLogger(__name__)


class BulkExporter:
    """Replays whole days of the CURRENT configuration into a recording pack."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state: dict[str, Any] = {"active": False}

    # -- control -------------------------------------------------------------- #

    def start(self, sim_copy, meta: dict[str, Any], days: list[int],
              name: str | None = None) -> dict:
        """Start the replay on an already ISOLATED simulator copy (the caller
        deep-copies while the engine is briefly parked, so the copy is clean)."""
        if self._state.get("active"):
            raise RuntimeError("a bulk export is already running")
        rec = Recorder(self.root)
        meta = {**meta, "export": {"days": days}}
        rec.start(meta, name=name or f"export-{len(days)}-tage")
        spd = int(sim_copy.settings.steps_per_day)
        self._state = {
            "active": True,
            "id": rec.status()["id"],
            "days": days,
            "steps_total": len(days) * spd,
            "steps_done": 0,
            "day": days[0] if days else None,
            "started": time.time(),
            "error": None,
            "cancelled": False,
        }
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(sim_copy, rec, days),
            name="rtheatflow-exporter", daemon=True)
        self._thread.start()
        return self.status()

    def cancel(self) -> dict:
        """Request a stop between steps; the partial pack is kept + finalized."""
        if not self._state.get("active"):
            raise RuntimeError("no bulk export is running")
        self._cancel.set()
        if self._thread is not None:
            self._thread.join(timeout=60)
        return self.status()

    def status(self) -> dict:
        s = dict(self._state)
        if s.get("active") and s.get("steps_done"):
            rate = s["steps_done"] / max(time.time() - s["started"], 1e-6)
            s["eta_seconds"] = round(
                (s["steps_total"] - s["steps_done"]) / max(rate, 1e-6))
        return s

    @property
    def active_id(self) -> str | None:
        """The pack currently being written (guards download/delete)."""
        return self._state.get("id") if self._state.get("active") else None

    # -- replay thread ----------------------------------------------------------- #

    def _run(self, sim, rec: Recorder, days: list[int]) -> None:
        t0 = time.time()
        # frames go through the SAME projection path as the live wire — in
        # strict mode the export pack carries no ground truth either (this is
        # what makes live vs export byte-compatibility hold in both modes)
        store = StateStore(sim.settings)
        try:
            self.prepare_replay(sim, first_day=days[0] if days else 0)
            spd = int(sim.settings.steps_per_day)
            for d in days:
                self._state["day"] = d
                for t in range(spd):
                    if self._cancel.is_set():
                        self._state["cancelled"] = True
                        raise _Cancelled()
                    rec.record(store.frame(sim.run_step(t, d)))
                    self._state["steps_done"] += 1
        except _Cancelled:
            log.info("bulk export cancelled after %d steps",
                     self._state["steps_done"])
        except Exception as exc:  # noqa: BLE001 — surface via status, keep partial
            log.exception("bulk export failed")
            self._state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            rec._meta = {**rec._meta,
                         "export": {**rec._meta.get("export", {}),
                                    "cancelled": self._state.get("cancelled", False),
                                    "error": self._state.get("error"),
                                    "duration_seconds": round(time.time() - t0, 1)}}
            rec.stop()
            self._state["active"] = False
            log.info("bulk export finished: %d steps in %.1f s",
                     self._state["steps_done"], time.time() - t0)

    @staticmethod
    def prepare_replay(sim, first_day: int = 0) -> None:
        """Normalize *sim* for a deterministic from-midnight replay.

        The state reset here is exactly the run-state a fresh scenario load
        discards too (M4 convention) — configuration (heating curve, Δp
        setpoint/mode, plant kind, weather override, equipment, sensor
        placement) is kept:

        * plant flow temperature evaluated for the first replayed tick (so
          the cold initialization below is deterministic, not "whatever the
          heating curve last wrote");
        * cold initialization (supply-temp init, build-time pressures) —
          replacing the live warm-start state;
        * a **controlled** Δp pump is released to its file-defined lift (the
          run-state of the controller, like the blueprint's released
          controllers); a **fixed** pump keeps the user's setting (config);
        * storages start at SoC 0 (the M4 scenario-load convention);
        * measurement windows fresh (standard-mode meters cold-start
          honestly), last-payload/blind-spot cleared.

        Public on purpose: the live-vs-export byte-compatibility test starts
        its live recording from this same normalized state.
        """
        tick0 = sim._tick(0, first_day)
        idx = sim.index
        if sim.heating_curve is not None:
            sim.net.circ_pump_pressure.at[idx.slack, "t_flow_k"] = \
                sim.heating_curve.t_flow_k(sim.weather.t_amb(tick0))
        if sim.dp_control.mode == "controlled":
            slack_spec = next(p for p in sim.inputs.producers.producers
                              if p.kind == "slack")
            if slack_spec.plift_bar is not None:
                sim.set_plift(float(slack_spec.plift_bar))
        sim.dp_control.last_dp_observed_bar = None
        for s in sim.storages:
            s.soc_kwh = 0.0
            s.q_kw = 0.0
        sim.measurements._reset_windows()
        sim._last_payload = None
        sim._blind_spot = None
        sim._reset_initialization()


class _Cancelled(Exception):
    pass
