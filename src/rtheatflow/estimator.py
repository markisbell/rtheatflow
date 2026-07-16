"""Estimation layer — the forward-simulation observer (SPEC §8a, M7).

pandapipes has **no state estimator** (nothing like pandapower's WLS exists),
so the ``estimated`` layer is a *forward observer* ("digital twin estimator"):
a second pandapipes net driven **only** by what the operator can actually
know —

* **plant SCADA** (always measured): flow temperature and the pump lift the
  operator set;
* **metered consumers**: their measured channels, respecting fidelity
  (standard-mode meters deliver 15-min-window means; a cold-start ``None``
  falls back to the prior);
* **unmetered consumers**: *pseudo-priors* — the expected profile from
  planning data (archetype expected profile where the archetype is known,
  otherwise the contracted plan), **never** the live per-tick truth;
* **operator equipment dispatch** (secondary producers, storages): the
  operator's own setpoints — configuration, not measurement (always visible
  on the wire per SPEC §6);
* **weather**: the true ambient (incl. override) and ground temperature —
  the plant has a weather station, and the override is the operator's own
  knob (documented design decision).

The twin solves with the same retry ladder as the truth; its deviation from
the *measurements* at sensored points quantifies estimate quality (the
``error`` field, mirroring the blueprint's ``estimated.error`` — but computed
against measurements, not truth, so it stays valid in strict mode). Twin
non-convergence is data: the estimate simply stops refreshing (stale
attachment) — never a crash.

Honesty rules (SPEC §11 tripwires; pinned in ``tests/test_estimation_m7.py``):

* the estimate must NOT contain information no sensor could deliver — an
  anomaly injected on an *unmetered* consumer stays invisible (its estimated
  values remain the prior), while the same anomaly on a *metered* consumer
  propagates;
* with the ``clear`` preset (plant SCADA only) the estimated per-consumer
  values equal the priors exactly (the twin is *driven* by them);
* the ``error`` metric rises visibly as sensor coverage shrinks.

Priors read ``sim.inputs`` (the immutable five-file planning contract) plus
the archetype cache and the runtime placement *recipes* — never the mutable
runtime ``sim.profiles``/net tables, so a runtime anomaly injection cannot
leak into the prior basis.

Throttling (blueprint ``estimator``/``simulator`` pattern): two gates ANDed —
a metering raster (estimates only at 15-min window boundaries when the
placed devices run in standard mode; new readings cannot exist in between)
and a wall-clock self-throttle (``throttle_factor ×`` the last estimation's
own runtime must have elapsed). The last estimate is attached to every
subsequent frame until refreshed; ``step``/``day``/``seq`` on the payload
tell the UI how stale it is.
"""
from __future__ import annotations

import copy
import logging
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from .consumers import ArchetypeLibrary, pick_day
from .network_builder import KELVIN, _resample_staircase
from .sensors import _r

log = logging.getLogger(__name__)

PRIOR_BASES = ("archetype", "design")

#: Archetype cache resolution (data/profiles/, 15-min year arrays).
_ARCH_SPD = 96


@dataclass
class EstimationConfig:
    """Operator-facing estimation policy (GET/POST /estimation/config).

    ``enabled`` — the observer runs at all (default on; SPEC §12 M7).
    ``prior_basis`` — what unmetered consumers are assumed to do:
      * ``archetype``: the archetype's *expected* profile (deterministic SH
        day-matched to the weather + the archetype's mean DHW across
        variants), scaled to the contracted design load; consumers without a
        known archetype fall back to the planning contract / design basis.
      * ``design``: the crude teaching prior — ``q_design · f(T_amb)`` degree-
        hour space heating, no DHW.
    ``throttle_factor`` — wall-clock self-throttle: a new estimate only after
    ``throttle_factor × solve_ms`` of the previous one has elapsed (~2× per
    the blueprint).
    """

    enabled: bool = True
    prior_basis: str = "archetype"
    throttle_factor: float = 2.0

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Priors — the operator's expectation for unmetered consumers
# ---------------------------------------------------------------------------

@dataclass
class PriorBook:
    """Dense prior arrays aligned with the CURRENT consumer row order.

    Everything here derives from planning data (``sim.inputs``, archetype
    cache, placement recipes) — deliberately never from the runtime profile
    arrays, which carry the stochastic per-tick truth.
    """

    q_sh_w: np.ndarray       # [n_cons, T] expected space heating
    q_dhw_w: np.ndarray      # [n_cons, T] expected DHW
    treturn_k: np.ndarray    # [n_cons, T]; NaN rows for non-treturn consumers
    deltat_k: np.ndarray     # [n_cons];   NaN for non-deltat consumers
    mdot_kg_per_s: np.ndarray  # [n_cons]; NaN for non-mdot consumers

    def qext_w(self, tick: int, weather, q_design_w: np.ndarray,
               mdot_mask: np.ndarray, min_qext_w: float) -> np.ndarray:
        """Prior demand at *tick*: SH scaled by the live degree-hour factor
        (weather-station knowledge, override-aware — same §4.5 math as the
        truth) + expected DHW, floored like the truth (§3.2)."""
        q_sh = weather.scale_space_heating(
            self.q_sh_w[:, tick], q_design_w, tick)
        floor = np.where(mdot_mask, 0.0, float(min_qext_w))
        return np.maximum(q_sh + self.q_dhw_w[:, tick], floor)


def _dhw_expected_shape(doc: dict) -> np.ndarray:
    """The archetype's *expected* DHW: mean across the stochastic variants,
    then the mean diurnal shape across the year — deterministic operator
    knowledge (typical draw pattern), no realization leaks. [96] values."""
    variants = doc.get("q_dhw_w_variants") or [doc["q_dhw_w"]]
    year = np.mean([np.asarray(v, dtype=float) for v in variants], axis=0)
    n_days = len(year) // _ARCH_SPD
    return year[: n_days * _ARCH_SPD].reshape(n_days, _ARCH_SPD).mean(axis=0)


def _match_day(q_sh_year: np.ndarray, expected_wh: float) -> int:
    """Archetype-year day whose SH energy best matches *expected_wh* — the
    operator's load forecast: weather (degree hours) → expected day energy →
    the typical day that looks like it."""
    n_days = len(q_sh_year) // _ARCH_SPD
    sums_wh = (q_sh_year[: n_days * _ARCH_SPD]
               .reshape(n_days, _ARCH_SPD).sum(axis=1) * (15.0 / 60.0))
    return int(np.argmin(np.abs(sums_wh - float(expected_wh))))


def build_prior_book(sim, basis: str) -> PriorBook:
    """Assemble the priors for the CURRENT consumer inventory of *sim*.

    Sources, in order of preference per consumer (matched by NAME — the same
    convention the scenario replay uses):

    1. runtime placement recipe (``sim.consumer_ops``) — archetype recipe →
       expected archetype profile; ``q_kw`` recipe → the flat plan; bypass →
       the configured pair;
    2. planning contract (``sim.inputs.consumers``) — ``building`` tag →
       expected archetype profile (day-matched to the weather); untagged →
       the hand-authored plan curves (teaching nets carry no stochastic
       realization worth hiding);
    3. fallback / ``basis="design"`` — ``q_design · f(T_amb)`` degree-hour
       space heating, no DHW.
    """
    idx, inputs, settings = sim.index, sim.inputs, sim.settings
    n = len(idx.consumers)
    steps = sim.profiles.steps
    spd = sim.profiles.steps_per_day

    library = ArchetypeLibrary(settings.profiles_dir)
    file_by_name = {(c.name or f"consumer_{c.node}"): c
                    for c in inputs.consumers.consumers}
    op_by_name: dict[str, dict] = {}
    for op in sim.consumer_ops:
        if op.get("op") in ("add_consumer", "add_bypass") and op.get("name"):
            op_by_name[str(op["name"])] = op

    # mean daily degree-hour factor per horizon day (profile ambient — the
    # override is applied per tick in PriorBook.qext_w, mirroring the truth)
    f_day = sim.weather.degree_hour_factor(
        np.asarray(sim.profiles.t_amb_c, dtype=float))
    f_day = f_day[: sim.profiles.n_days * spd].reshape(
        sim.profiles.n_days, spd).mean(axis=1)

    q_sh = np.zeros((n, steps))
    q_dhw = np.zeros((n, steps))
    treturn = np.full((n, steps), np.nan)
    deltat = np.full(n, np.nan)
    mdot = np.full(n, np.nan)

    def design_prior(pos: int) -> None:
        """q_design · f(T_amb) space heating, no DHW (crude but honest)."""
        f_t = sim.weather.degree_hour_factor(
            np.asarray(sim.profiles.t_amb_c[:steps], dtype=float))
        q_sh[pos] = float(idx.q_design_w[pos]) * np.asarray(f_t, dtype=float)

    def archetype_prior(pos: int, arch_id: str, q_design_target: float,
                        scale: float, percentile: float | None) -> bool:
        """Expected archetype profile; returns False when unavailable.

        The day window comes from the placement recipe's ``day_percentile``
        (operator config) when known; file consumers are day-matched to the
        weather (degree-hour load forecast) instead.
        """
        try:
            doc = library.load(arch_id)
        except (KeyError, OSError, ValueError):
            return False
        year_sh = np.asarray(doc["q_sh_w"], dtype=float)
        q_design_arch = float(doc["q_design_w"]) or 1.0
        ratio = (float(q_design_target) / q_design_arch
                 if q_design_target else 1.0) * float(scale or 1.0)
        dhw_shape = _dhw_expected_shape(doc) * ratio
        day0 = (pick_day(year_sh, float(percentile))
                if percentile is not None else None)
        rows_sh = []
        rows_dhw = []
        for d in range(sim.profiles.n_days):
            if day0 is not None:
                match = day0 + d
            else:
                expected_wh = q_design_arch * float(f_day[d]) * 24.0
                match = _match_day(year_sh, expected_wh)
            start = (match * _ARCH_SPD) % len(year_sh)
            window = (np.arange(_ARCH_SPD) + start) % len(year_sh)
            rows_sh.append(year_sh[window] * ratio)
            rows_dhw.append(dhw_shape)
        q_sh[pos] = _resample_staircase(np.concatenate(rows_sh), steps)
        q_dhw[pos] = _resample_staircase(np.concatenate(rows_dhw), steps)
        return True

    for i in range(n):
        name = idx.consumer_names[i]
        spec = file_by_name.get(name)
        op = op_by_name.get(name)

        # -- setpoint partners: planning values, never runtime state --------
        if idx.mdot_mask[i]:
            if op is not None and op.get("op") == "add_bypass":
                mdot[i] = float(op.get("mdot_kg_per_s", 0.02))
                q_dhw[i, :] = float(op.get("qext_w", 100.0))
                continue
            if spec is not None and spec.controlled_mdot_kg_per_s is not None:
                mdot[i] = float(spec.controlled_mdot_kg_per_s)
        elif idx.deltat_mask[i]:
            if spec is not None and spec.deltat_k is not None:
                deltat[i] = float(spec.deltat_k)
        else:  # treturn row
            if op is not None and op.get("treturn_c") is not None:
                treturn[i, :] = float(op["treturn_c"]) + KELVIN
            elif spec is not None and spec.treturn_k is not None:
                treturn[i, :] = _resample_staircase(
                    np.asarray(spec.treturn_k, dtype=float), steps)

        # -- expected demand -------------------------------------------------
        if basis == "design":
            design_prior(i)
            continue
        if op is not None:
            if op.get("archetype"):
                if archetype_prior(i, str(op["archetype"]),
                                   q_design_target=0.0,
                                   scale=float(op.get("scale", 1.0)),
                                   percentile=float(
                                       op.get("day_percentile", 0.9))):
                    continue
            elif op.get("q_kw"):
                q_sh[i, :] = float(op["q_kw"]) * 1000.0  # the flat plan
                continue
            design_prior(i)
            continue
        if spec is not None:
            if spec.building and archetype_prior(
                    i, str(spec.building),
                    q_design_target=float(spec.q_design_w),
                    scale=1.0, percentile=None):
                continue
            # untagged plan curves: hand-authored planning data
            q_sh[i] = _resample_staircase(
                np.asarray(spec.q_sh_w, dtype=float), steps)
            q_dhw[i] = _resample_staircase(
                np.asarray(spec.q_dhw_w, dtype=float), steps)
            continue
        design_prior(i)

    return PriorBook(q_sh_w=q_sh, q_dhw_w=q_dhw, treturn_k=treturn,
                     deltat_k=deltat, mdot_kg_per_s=mdot)


# ---------------------------------------------------------------------------
# The forward observer
# ---------------------------------------------------------------------------

class ForwardObserver:
    """Maintains the twin net + priors; produces the ``estimated`` payload."""

    def __init__(self, sim, config: EstimationConfig):
        self.sim = sim
        self.config = config
        self.twin = None
        self.book: PriorBook | None = None
        self._signature: tuple | None = None
        self._fluid = None
        self.last: dict | None = None
        self.seq = 0
        self._wall = 0.0        # monotonic time of the last estimation run
        self._ms = 0.0          # its duration (drives the adaptive spacing)

    # -- twin lifecycle ---------------------------------------------------------

    def _current_signature(self) -> tuple:
        sim, idx = self.sim, self.sim.index
        return (
            tuple(int(c) for c in idx.consumers),
            tuple(idx.consumer_names),
            tuple(int(h) for h in idx.heat_exchangers),
            tuple(int(p) for p in idx.pump_mass),
            tuple((s.charge_element, s.discharge_element)
                  for s in sim.storages),
            len(sim.net.junction), len(sim.net.pipe),
            self.config.prior_basis,
            round(float(sim.weather.t_room_c), 6),
            round(float(sim.weather.t_design_c), 6),
        )

    def _ensure_twin(self) -> None:
        """(Re)build the twin + priors when topology or policy changed."""
        sig = self._current_signature()
        if self.twin is not None and sig == self._signature:
            return
        import pandapipes as pp

        sim = self.sim
        self.twin = copy.deepcopy(sim.net)
        self._fluid = pp.get_fluid(self.twin)
        # honest initialization: the twin never inherits the truth's warm
        # state — supply-temperature init like any fresh net (§3.4); after
        # each converged estimate the twin warm-starts from ITSELF.
        t_flow_now = float(self.twin.circ_pump_pressure.at[
            sim.index.slack, "t_flow_k"])
        self.twin.junction["tfluid_k"] = t_flow_now
        self.twin.junction["pn_bar"] = sim.index.init_pn_bar
        self.book = build_prior_book(sim, self.config.prior_basis)
        self._signature = sig
        log.info("forward observer: twin rebuilt (%d consumers, basis=%s)",
                 len(sim.index.consumers), self.config.prior_basis)

    # -- boundary application -----------------------------------------------------

    def _apply(self, tick: int, measurements: dict, controls: dict) -> None:
        """Write the operator's knowledge onto the twin for this tick."""
        sim, idx, tw = self.sim, self.sim.index, self.twin
        book = self.book

        # 1. consumer rows: priors ...
        q = book.qext_w(tick, sim.weather, idx.q_design_w, idx.mdot_mask,
                        sim.settings.min_qext_w)
        treturn = book.treturn_k[:, tick].copy()
        deltat = book.deltat_k.copy()
        mdot = book.mdot_kg_per_s.copy()

        # ... overlaid with measured channels where a meter delivers
        # (fidelity-respecting: a standard-mode cold start is None → prior)
        pos_of = {int(c): i for i, c in enumerate(idx.consumers)}
        for m in measurements.get("consumers") or []:
            i = pos_of.get(int(m["id"]))
            if i is None:
                continue
            if m.get("q_kw") is not None:
                q[i] = float(m["q_kw"]) * 1000.0
            if idx.treturn_mask[i] and m.get("t_return_c") is not None:
                treturn[i] = float(m["t_return_c"]) + KELVIN
            if idx.deltat_mask[i] and (m.get("t_supply_c") is not None
                                       and m.get("t_return_c") is not None):
                deltat[i] = float(m["t_supply_c"]) - float(m["t_return_c"])
            if idx.mdot_mask[i] and m.get("mdot_kg_per_s") is not None:
                mdot[i] = abs(float(m["mdot_kg_per_s"]))

        rows = idx.consumers
        tw.heat_consumer.loc[rows, "qext_w"] = q
        tw.heat_consumer.loc[rows, "treturn_k"] = treturn
        tw.heat_consumer.loc[rows, "deltat_k"] = deltat
        tw.heat_consumer.loc[rows, "controlled_mdot_kg_per_s"] = mdot

        # 2. plant boundary: SCADA flow temperature + the operator's pump lift
        plant = measurements.get("plant") or {}
        if plant.get("t_flow_c") is not None:
            tw.circ_pump_pressure.at[idx.slack, "t_flow_k"] = \
                float(plant["t_flow_c"]) + KELVIN
        plift = (controls.get("dp_control") or {}).get("plift_bar")
        if plift is not None:
            tw.circ_pump_pressure.at[idx.slack, "plift_bar"] = float(plift)

        # 3. weather-station / planning knowledge: ground temperature
        tw.pipe.loc[idx.pipes_all, "text_k"] = \
            sim.weather.t_ground(tick) + KELVIN

        # 4. operator equipment dispatch (config, not measurement): copy the
        # live INPUT columns — secondary producers, pump_mass, storage
        # branches (every heat_consumer row that is not a consumer).
        live = sim.net
        if len(live.heat_exchanger):
            tw.heat_exchanger["qext_w"] = live.heat_exchanger["qext_w"].values
        if len(live.circ_pump_mass):
            for col in ("mdot_flow_kg_per_s", "t_flow_k", "in_service"):
                tw.circ_pump_mass[col] = live.circ_pump_mass[col].values
        storage_rows = [r for r in live.heat_consumer.index
                        if int(r) not in pos_of]
        if storage_rows:
            for col in ("qext_w", "controlled_mdot_kg_per_s", "treturn_k"):
                tw.heat_consumer.loc[storage_rows, col] = \
                    live.heat_consumer.loc[storage_rows, col].values

    # -- error metric: deviation at SENSORED points --------------------------------

    def _error(self, est: dict, measurements: dict) -> dict:
        """|twin − measurement| at every sensored point. Buckets per the
        design rails: return temperature, mass flow, differential pressure.
        Computed against MEASUREMENTS (not truth) — the innovation of the
        observer, valid in strict mode by construction."""
        dt_ret: list[float] = []
        dmdot: list[float] = []
        ddp: list[float] = []

        est_cons = {int(c["id"]): c for c in est["consumers"]}
        for m in measurements.get("consumers") or []:
            c = est_cons.get(int(m["id"]))
            if c is None:
                continue
            if m.get("t_return_c") is not None and c["t_return_c"] is not None:
                dt_ret.append(abs(float(m["t_return_c"]) - c["t_return_c"]))
            if (m.get("mdot_kg_per_s") is not None
                    and c["mdot_kg_per_s"] is not None):
                dmdot.append(abs(float(m["mdot_kg_per_s"])
                                 - c["mdot_kg_per_s"]))
            if m.get("dp_bar") is not None and c["dp_bar"] is not None:
                ddp.append(abs(float(m["dp_bar"]) - c["dp_bar"]))

        est_junc = {(j["name"], j["side"]): j for j in est["junctions"]}
        for nm in measurements.get("nodes") or []:
            js = est_junc.get((nm["node"], "s"))
            jr = est_junc.get((nm["node"], "r"))
            if jr is not None and nm.get("t_return_c") is not None \
                    and jr["t_c"] is not None:
                dt_ret.append(abs(float(nm["t_return_c"]) - jr["t_c"]))
            if (js is not None and jr is not None
                    and nm.get("p_supply_bar") is not None
                    and nm.get("p_return_bar") is not None
                    and js["p_bar"] is not None and jr["p_bar"] is not None):
                dp_meas = float(nm["p_supply_bar"]) - float(nm["p_return_bar"])
                ddp.append(abs(dp_meas - (js["p_bar"] - jr["p_bar"])))

        plant = measurements.get("plant") or {}
        s = est["summary"]
        if plant.get("t_return_c") is not None \
                and s.get("t_return_plant_c") is not None:
            dt_ret.append(abs(float(plant["t_return_c"])
                              - s["t_return_plant_c"]))
        if plant.get("mdot_kg_per_s") is not None \
                and s.get("mdot_plant_kg_per_s") is not None:
            dmdot.append(abs(float(plant["mdot_kg_per_s"])
                             - s["mdot_plant_kg_per_s"]))

        def agg(vals: list[float]) -> tuple:
            return ((_r(max(vals)), _r(sum(vals) / len(vals)))
                    if vals else (None, None))

        max_dt, mean_dt = agg(dt_ret)
        max_dm, mean_dm = agg(dmdot)
        max_dp, mean_dp = agg(ddp)
        return {
            "max_dt_return_k": max_dt, "mean_dt_return_k": mean_dt,
            "max_dmdot_kg_per_s": max_dm, "mean_dmdot_kg_per_s": mean_dm,
            "max_ddp_bar": max_dp, "mean_ddp_bar": mean_dp,
            "n_points": len(dt_ret) + len(dmdot) + len(ddp),
        }

    # -- the estimate ---------------------------------------------------------------

    def maybe_estimate(self, payload: dict, tick: int,
                       step: int, day: int) -> dict | None:
        """Refresh the estimate if the gates allow; return the current one.

        Called from ``Simulator.run_step`` after a converged truth solve.
        Two gates ANDed (blueprint): the metering raster (standard-mode
        devices publish only at window boundaries — no new information in
        between) and the wall-clock self-throttle. Estimation failure keeps
        the last estimate attached (stale) — failure is data.
        """
        if not self.config.enabled:
            return None
        ms = self.sim.measurements
        raster = (ms.window_steps
                  if ms.mode == "standard"
                  and (ms.consumer_meters or ms.node_sensors) else 1)
        now = time.monotonic()
        throttled = (now - self._wall
                     < self.config.throttle_factor * self._ms / 1000.0)
        if int(tick) % raster != 0 or throttled:
            return self.last

        try:
            est = self._estimate(payload, tick)
        except Exception:  # estimation failure is data, never a crash
            log.exception("forward observer failed — estimate stays stale")
            est = None
        self._wall = time.monotonic()   # stamped even on failure (no retry
        if est is not None:             # storm against a broken twin)
            est["step"], est["day"] = int(step), int(day)
            self.seq += 1
            est["seq"] = self.seq
            self._ms = float(est["solve_ms"] or 0.0)
            self.last = est
        return self.last

    def _estimate(self, payload: dict, tick: int) -> dict | None:
        from .simulator import collect_physics, solve_with_retry

        self._ensure_twin()
        self._apply(tick, payload.get("measurements") or {},
                    payload.get("controls") or {})
        outcome = solve_with_retry(self.twin, self.sim.settings.solver_iter)
        if not outcome.converged:
            return None
        # twin warm start (§3.4) — from its OWN converged results
        self.twin.junction["pn_bar"] = self.twin.res_junction.p_bar.values
        self.twin.junction["tfluid_k"] = self.twin.res_junction.t_k.values

        physics = collect_physics(
            self.twin, self.sim.index, self._fluid, self.sim._pipe_trench,
            self.sim.settings.pump_eta, self.sim.storages)
        est = {
            "junctions": physics["junctions"],
            "pipes": physics["pipes"],
            "consumers": physics["consumers"],
            "summary": physics["summary"],
            "solver_status": outcome.status,
            "solve_ms": _r(outcome.solve_ms, 3) or 0.0,
        }
        est["error"] = self._error(est, payload.get("measurements") or {})
        return est
