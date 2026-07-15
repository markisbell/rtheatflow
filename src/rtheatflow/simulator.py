"""Simulator — owns the net, profiles, controllers and the per-tick solve (SPEC §6).

``run_step(step, day)`` = ``_apply_step`` (profiles → weather → controllers)
→ retry-ladder solve (SPEC §3.3) → ``_collect()`` (derived quantities, §3.6)
→ platform warm start (§3.4).

Failure policy (binding, SPEC §3.3): try each ladder tier, catching
``PipeflowNotConverged`` *and* a deliberate catch-all ``Exception`` arm
(racing runtime mutations may poison one step — no locks by design). If all
tiers fail, **reuse the last converged state** and publish the frame with
``converged=false`` / ``solver_status="failed"``. Never crash the loop;
non-convergence is data.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandapipes as pp
from pandapipes import pipeflow
from pandapipes.pf.pipeflow_setup import PipeflowNotConverged

from .config import Settings, get_settings
from .heating_curve import HeatingCurve, from_config
from .net_inputs import NetInputs
from .network_builder import KELVIN, ProfileArrays, build_network
from .sensors import MeasurementSet, _r
from .weather import WeatherModel

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StepResult — the single wire format (SPEC §6). REST /state, /history, WS
# frames and the recorder all use the same asdict() + projection path (M2+).
# M1 fills the headless subset; the observability fields keep their defaults.
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    step: int
    day: int
    time_of_day: str          # "HH:MM"
    converged: bool
    solver_status: str        # "ok" | "degraded" | "failed"
    solve_ms: float
    timestamp: float
    # -- ground-truth layer (stripped in strict mode, M5) --
    junctions: list = field(default_factory=list)
    pipes: list = field(default_factory=list)
    consumers: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    # -- runtime equipment (always visible) --
    producers: list = field(default_factory=list)
    storages: list = field(default_factory=list)
    weather: dict = field(default_factory=dict)
    controls: dict = field(default_factory=dict)
    # -- observability layers (M2/M5/M7) --
    measurements: dict = field(default_factory=dict)
    observed_summary: dict | None = None
    estimated: dict | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Retry ladder (SPEC §3.3)
# ---------------------------------------------------------------------------

def retry_attempts(iter_base: int) -> list[dict]:
    """The §3.3 ladder. ``RTHEATFLOW_SOLVER_ITER`` is the base ``iter`` of
    tiers 1, 2 and 4; tier 3 uses 2x (the single knob's defined semantics).

    Never ``nonlinear_method="automatic"`` here — it raises ``ValueError``
    under ``mode="bidirectional"`` in 0.14.0 (SPEC Appendix B item 2).
    """
    n = int(iter_base)
    return [
        dict(mode="bidirectional", iter=n),
        dict(mode="bidirectional", iter=n, alpha=0.5),
        dict(mode="bidirectional", iter=2 * n, alpha=0.2),
        dict(mode="sequential", iter=n),  # degraded: set points not honored
    ]


@dataclass
class SolveOutcome:
    converged: bool
    status: str           # "ok" | "degraded" | "failed"
    tier: int             # 1-based tier that converged; 0 if none
    solve_ms: float
    error: str | None = None


def solve_with_retry(net, iter_base: int = 100) -> SolveOutcome:
    """Run the retry ladder on *net*. Never raises for non-convergence."""
    t0 = time.perf_counter()
    errors: list[str] = []
    for tier, kwargs in enumerate(retry_attempts(iter_base), start=1):
        try:
            pipeflow(net, **kwargs)
        except PipeflowNotConverged as exc:
            errors.append(f"tier {tier} {kwargs}: not converged ({exc})")
            continue
        except Exception as exc:  # deliberate catch-all arm (SPEC §3.3)
            errors.append(f"tier {tier} {kwargs}: {type(exc).__name__}: {exc}")
            continue
        ms = (time.perf_counter() - t0) * 1000.0
        if kwargs["mode"] == "bidirectional":
            return SolveOutcome(True, "ok", tier, ms)
        return SolveOutcome(
            True, "degraded", tier, ms,
            error="sequential fallback: temperature set points not honored")
    ms = (time.perf_counter() - t0) * 1000.0
    err = "; ".join(errors) or "no solver tier attempted"
    log.warning("all retry-ladder tiers failed: %s", err)
    return SolveOutcome(False, "failed", 0, ms, error=err)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class Simulator:
    """Builds net + profiles once; steps the physics; collects results."""

    def __init__(self, inputs: NetInputs, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.inputs = inputs
        self.net, self.profiles = build_network(
            inputs,
            steps_per_day=self.settings.steps_per_day,
            min_qext_w=self.settings.min_qext_w,
        )
        self.index = self.profiles.index
        self._fluid = pp.get_fluid(self.net)

        slack_spec = next(p for p in inputs.producers.producers if p.kind == "slack")
        self.heating_curve: HeatingCurve | None = (
            from_config(slack_spec.heating_curve)
            if slack_spec.heating_curve is not None else None)
        self._dp_config = slack_spec.dp_control  # controller ships in M4

        self.weather = WeatherModel(
            t_amb_c=self.profiles.t_amb_c,
            t_ground_c=self.profiles.t_ground_c,
        )
        if self.heating_curve is not None:
            # couple the §4.5 degree-hour factor to the curve's design points
            self.weather.t_room_c = self.heating_curve.t_room_c
            self.weather.t_design_c = self.heating_curve.t_amb_design_c

        # pipe index -> (trench, side) for the wire payload
        self._pipe_trench: dict[int, tuple[int, str]] = {}
        for t, (ps, pr) in enumerate(
                zip(self.index.pipes_supply, self.index.pipes_return)):
            self._pipe_trench[int(ps)] = (t, "s")
            self._pipe_trench[int(pr)] = (t, "r")

        # measurable layer (SPEC §8a interim rule): exists from M2 with the
        # default preset all_consumers + plant SCADA; CRUD/fidelity in M5
        self.measurements = MeasurementSet()

        self._last_payload: dict | None = None  # last converged _collect()

    # -- tick bookkeeping ---------------------------------------------------

    def _tick(self, step: int, day: int) -> int:
        """Global profile tick for (step, day); days wrap modulo the horizon."""
        p = self.profiles
        step = min(max(int(step), 0), p.steps_per_day - 1)
        return (int(day) % p.n_days) * p.steps_per_day + step

    def _time_of_day(self, step: int) -> str:
        minute = int(round(step * 1440.0 / self.settings.steps_per_day)) % 1440
        return f"{minute // 60:02d}:{minute % 60:02d}"

    # -- per-tick input application (SPEC §3.4 order) ------------------------

    def _apply_step(self, tick: int) -> None:
        """profiles → weather → controllers (storage bookkeeping arrives M4)."""
        net, p, idx = self.net, self.profiles, self.index

        # demand: space heating (weather-override aware, §4.5) + DHW, floored
        q_sh = self.weather.scale_space_heating(
            p.q_sh_w[:, tick], idx.q_design_w, tick)
        qext = np.maximum(q_sh + p.q_dhw_w[:, tick], self.settings.min_qext_w)
        net.heat_consumer.loc[idx.consumers, "qext_w"] = qext

        # return-temperature behavior (only treturn-controlled rows)
        if idx.treturn_mask.any():
            rows = idx.consumers[idx.treturn_mask]
            net.heat_consumer.loc[rows, "treturn_k"] = \
                p.treturn_k[idx.treturn_mask, tick]

        # weather → heating curve → plant flow temperature
        t_amb = self.weather.t_amb(tick)
        if self.heating_curve is not None:
            net.circ_pump_pressure.at[idx.slack, "t_flow_k"] = \
                self.heating_curve.t_flow_k(t_amb)

        # slow seasonal: ground temperature onto every pipe (explicit text_k)
        net.pipe.loc[idx.pipes_all, "text_k"] = self.weather.t_ground(tick) + KELVIN

        # secondary producers: feed-in = negative qext_w (SPEC §3.1)
        if len(idx.heat_exchangers):
            net.heat_exchanger.loc[idx.heat_exchangers, "qext_w"] = \
                -p.producer_qext_w[:, tick]
        if len(idx.pump_mass):
            net.circ_pump_mass.loc[idx.pump_mass, "mdot_flow_kg_per_s"] = \
                p.pump_mass_mdot[:, tick]

    # -- the step ------------------------------------------------------------

    def run_step(self, step: int, day: int) -> StepResult:
        """One simulation step. Never raises for non-convergence (SPEC §3.3)."""
        tick = self._tick(step, day)
        apply_error: str | None = None
        try:
            self._apply_step(tick)
        except Exception as exc:  # racing CRUD may poison one step — self-heal
            apply_error = f"apply_step: {type(exc).__name__}: {exc}"
            log.warning("apply_step failed (frame degrades to failed): %s",
                        apply_error)

        if apply_error is None:
            outcome = solve_with_retry(self.net, self.settings.solver_iter)
        else:
            outcome = SolveOutcome(False, "failed", 0, 0.0, error=apply_error)

        if outcome.converged:
            try:
                payload = self._collect(tick)
                self._last_payload = payload
            except Exception as exc:
                log.exception("result collection failed")
                outcome = SolveOutcome(
                    False, "failed", outcome.tier, outcome.solve_ms,
                    error=f"collect: {type(exc).__name__}: {exc}")
                payload = self._reused_payload(tick)
            else:
                # platform warm start (§3.4): converged results become the
                # next initialization — validated continuation strategy
                self.net.junction["pn_bar"] = self.net.res_junction.p_bar.values
                self.net.junction["tfluid_k"] = self.net.res_junction.t_k.values
        else:
            # failed step: reset init to the current supply temperature
            # (tutorial guidance; §3.4) and reuse the last converged state
            self._reset_initialization()
            payload = self._reused_payload(tick)

        return StepResult(
            step=int(step),
            day=int(day),
            time_of_day=self._time_of_day(step),
            converged=bool(outcome.converged),
            solver_status=outcome.status,
            solve_ms=_r(outcome.solve_ms, 3) or 0.0,
            timestamp=time.time(),
            error=outcome.error,
            **payload,
        )

    def _reset_initialization(self) -> None:
        """After swaps/failures: supply-temperature init, build-time pressures."""
        t_flow_now = float(
            self.net.circ_pump_pressure.at[self.index.slack, "t_flow_k"])
        self.net.junction["tfluid_k"] = t_flow_now
        self.net.junction["pn_bar"] = self.index.init_pn_bar

    def _reused_payload(self, tick: int) -> dict:
        """Last converged physics (or empty shells) + live weather/controls."""
        payload = dict(self._last_payload) if self._last_payload else {
            "junctions": [], "pipes": [], "consumers": [],
            "producers": [], "summary": {},
        }
        payload["weather"] = self._weather_dict(tick)
        payload["controls"] = self._controls_dict()
        return payload

    # -- derived quantities & wire payload (SPEC §3.6, §6) --------------------

    def _weather_dict(self, tick: int) -> dict:
        return {
            "t_amb_c": _r(self.weather.t_amb(tick)),
            "t_ground_c": _r(self.weather.t_ground(tick)),
            "override": self.weather.override_active,
        }

    def _controls_dict(self) -> dict:
        plift = float(
            self.net.circ_pump_pressure.at[self.index.slack, "plift_bar"])
        return {
            "heating_curve": (self.heating_curve.params()
                              if self.heating_curve is not None else None),
            "dp_control": {
                "mode": (self._dp_config.mode if self._dp_config else "fixed"),
                "setpoint_bar": (_r(self._dp_config.setpoint_bar)
                                 if self._dp_config else None),
                "plift_bar": _r(plift),
            },
        }

    def _collect(self, tick: int) -> dict:
        net, idx = self.net, self.index
        fluid = self._fluid
        rj, rp, rhc = net.res_junction, net.res_pipe, net.res_heat_consumer
        rc = net.res_circ_pump_pressure.loc[idx.slack]

        # --- per-pipe heat loss [W], direction-aware (SPEC §3.6) ---
        # inlet = upstream node temp (t_from if mdot >= 0 else t_to); outlet =
        # the branch's own t_outlet_k (before junction mixing), never t_to_k.
        mdot_pipe = rp.mdot_from_kg_per_s.values
        fwd = mdot_pipe >= 0
        t_in = np.where(fwd, rp.t_from_k.values, rp.t_to_k.values)
        cp_pipe = fluid.get_heat_capacity((t_in + rp.t_outlet_k.values) / 2)
        q_loss_w = np.abs(mdot_pipe) * cp_pipe * (t_in - rp.t_outlet_k.values)
        q_loss_total_w = float(q_loss_w.sum())

        # --- plant feed-in [W] = mdot · cp̄ · ΔT (SPEC §3.6) — NOT the raw
        # res_circ_pump_pressure.qext_w column (enthalpy form, ~+4.7 %) ---
        mdot_plant = float(abs(rc.mdot_from_kg_per_s))
        t_mean = (float(rc.t_outlet_k) + float(rc.t_from_k)) / 2
        cp_plant = float(fluid.get_heat_capacity(np.array([t_mean]))[0])
        q_feed_plant_w = mdot_plant * cp_plant * (
            float(rc.t_outlet_k) - float(rc.t_from_k))

        # --- secondary feed-ins carry negative qext_w: negate, never sum raw.
        # NB: res_heat_exchanger has NO qext_w column at runtime (0.14.0 —
        # verified M2; §10.1 "branch cols" only). qext_w is a fixed input
        # setpoint, so the component table is the authoritative source. ---
        q_secondary_w = 0.0
        if len(idx.heat_exchangers):
            hx_q = net.heat_exchanger.loc[
                idx.heat_exchangers, "qext_w"].to_numpy(dtype=float)
            q_secondary_w = float(-hx_q[hx_q < 0].sum())
        q_feed_in_w = q_feed_plant_w + q_secondary_w

        q_demand_w = float(rhc.qext_w.sum())
        balance_err_w = q_feed_in_w - (q_demand_w + q_loss_total_w)
        loss_pct = 100.0 * q_loss_total_w / q_feed_in_w if q_feed_in_w else None

        # --- worst-point Δp + argmin (SPEC §3.6) ---
        dp_cons = (rhc.p_from_bar - rhc.p_to_bar).to_numpy()
        worst_pos = int(np.argmin(dp_cons))
        dp_worst_bar = float(dp_cons[worst_pos])
        worst_consumer = idx.consumer_names[worst_pos]

        # --- pump electric power (SPEC §3.6): P_hyd = V̇·Δp, P_el = P_hyd/η ---
        dp_pump_pa = (float(rc.p_to_bar) - float(rc.p_from_bar)) * 1e5
        p_hyd_w = abs(float(rc.vdot_m3_per_s)) * abs(dp_pump_pa)
        pump_el_w = p_hyd_w / self.settings.pump_eta

        # --- wire payload: temperatures in °C, everything through _r() ---
        junctions = [
            {"id": int(i), "name": idx.junction_names[i],
             "side": idx.junction_sides[i],
             "p_bar": _r(rj.p_bar.iloc[i]), "t_c": _r(rj.t_k.iloc[i] - KELVIN)}
            for i in range(len(rj))
        ]
        pipes = []
        for i in range(len(rp)):
            trench, side = self._pipe_trench.get(int(net.pipe.index[i]), (-1, "?"))
            pipes.append({
                "id": int(net.pipe.index[i]), "trench": trench, "side": side,
                "mdot_kg_per_s": _r(rp.mdot_from_kg_per_s.iloc[i]),
                "v_m_per_s": _r(rp.v_mean_m_per_s.iloc[i]),
                "t_from_c": _r(rp.t_from_k.iloc[i] - KELVIN),
                "t_to_c": _r(rp.t_to_k.iloc[i] - KELVIN),
                "q_loss_kw": _r(q_loss_w[i] / 1000.0),
                "dp_bar": _r(rp.p_from_bar.iloc[i] - rp.p_to_bar.iloc[i]),
            })
        consumers = [
            {"id": int(idx.consumers[i]), "name": idx.consumer_names[i],
             "node": idx.consumer_nodes[i],
             "q_kw": _r(rhc.qext_w.iloc[i] / 1000.0),
             "mdot_kg_per_s": _r(rhc.mdot_from_kg_per_s.iloc[i]),
             "t_supply_c": _r(rhc.t_from_k.iloc[i] - KELVIN),
             "t_return_c": _r(rhc.t_outlet_k.iloc[i] - KELVIN),
             "dp_bar": _r(dp_cons[i])}
            for i in range(len(idx.consumers))
        ]
        producers = []
        for meta in idx.producer_meta:
            # wire id = platform-unique pid, never the per-kind element index
            entry = {"id": int(meta["pid"]), "kind": meta["kind"],
                     "name": meta["name"], "node": meta["node"]}
            if meta["kind"] == "slack":
                entry.update({
                    "q_kw": _r(q_feed_plant_w / 1000.0),
                    "t_flow_c": _r(rc.t_outlet_k - KELVIN),
                    "plift_bar": _r(net.circ_pump_pressure.at[
                        idx.slack, "plift_bar"]),
                    "pump_el_kw": _r(pump_el_w / 1000.0),
                })
            elif meta["kind"] == "heat_exchanger":
                # input setpoint — res_heat_exchanger has no qext_w column
                q = float(net.heat_exchanger.at[meta["element"], "qext_w"])
                entry["q_kw"] = _r(-q / 1000.0)  # feed-in positive on the wire
            else:  # pump_mass
                rm = net.res_circ_pump_mass.loc[meta["element"]]
                entry.update({
                    "q_kw": None,  # display value only from M4 dispatch models
                    "t_flow_c": _r(rm.t_outlet_k - KELVIN),
                    "mdot_kg_per_s": _r(abs(rm.mdot_from_kg_per_s)),
                })
            producers.append(entry)

        summary = {
            "q_feed_kw": _r(q_feed_in_w / 1000.0),
            "q_demand_kw": _r(q_demand_w / 1000.0),
            "q_loss_kw": _r(q_loss_total_w / 1000.0),
            "loss_pct": _r(loss_pct, 3),
            "pump_el_kw": _r(pump_el_w / 1000.0),
            "dp_worst_bar": _r(dp_worst_bar),
            "worst_consumer": worst_consumer,
            "t_flow_plant_c": _r(rc.t_outlet_k - KELVIN),
            "t_return_plant_c": _r(rc.t_from_k - KELVIN),
            "mdot_plant_kg_per_s": _r(mdot_plant),
            "balance_err_kw": _r(balance_err_w / 1000.0),
        }
        payload = {
            "junctions": junctions,
            "pipes": pipes,
            "consumers": consumers,
            "producers": producers,
            "summary": summary,
            "weather": self._weather_dict(tick),
            "controls": self._controls_dict(),
        }
        # observed layer (SPEC §8a): projection of the truth payload onto the
        # sensored elements — every frame carries measurements/observed_summary
        payload["measurements"], payload["observed_summary"] = \
            self.measurements.observe(payload)
        return payload

    # -- runtime equipment CRUD (SPEC §4.4 pattern; minimal M2 subset) ---------
    #
    # Full equipment CRUD ships in M4. M2 provides the minimal functional
    # add/remove for a heat_exchanger secondary that the §12 M2 error-code
    # acceptance requires: direct net mutation (no rebuild), indices extended,
    # init reset per §3.4 (topology CRUD), racing solves self-heal per §3.3.

    def add_heat_exchanger(
        self,
        node: str,
        qext_w: float,
        inner_diameter_mm: float,
        name: str | None = None,
    ) -> dict:
        """Place a secondary heat_exchanger feed-in at *node* (return→supply).

        *qext_w* is the constant feed-in dispatch in W (positive on the API;
        negated onto the element per the SPEC §3.1 convention). Returns the
        producer meta dict. Raises ``KeyError`` for an unknown node.
        """
        idx, p = self.index, self.profiles
        jr = idx.junction_return[node]  # KeyError -> unknown node (API: 400)
        js = idx.junction_supply[node]
        name = name or f"heat_exchanger_{node}"
        dispatch = float(qext_w)
        hx = pp.create_heat_exchanger(
            self.net, from_junction=jr, to_junction=js,
            qext_w=-dispatch,  # feed-in = negative qext_w (SPEC §3.1)
            inner_diameter_mm=float(inner_diameter_mm), name=name)
        # extend the dense dispatch profiles + index (row order = element order)
        idx.heat_exchangers = np.append(idx.heat_exchangers, hx)
        p.producer_qext_w = np.vstack(
            [p.producer_qext_w, np.full((1, p.steps), dispatch)])
        meta = {"pid": idx.next_pid(), "kind": "heat_exchanger",
                "element": int(hx), "node": node, "name": name}
        idx.producer_meta.append(meta)
        self._reset_initialization()  # topology CRUD → cold init (SPEC §3.4)
        return meta

    def remove_heat_exchanger(self, element: int) -> dict:
        """Remove the heat_exchanger with pandapipes *element* index.

        Returns the removed meta dict. Raises ``KeyError`` if no
        heat_exchanger has that element index.
        """
        idx, p = self.index, self.profiles
        pos_arr = np.nonzero(idx.heat_exchangers == int(element))[0]
        if len(pos_arr) == 0:
            raise KeyError(f"no heat_exchanger with element index {element}")
        pos = int(pos_arr[0])
        self.net.heat_exchanger.drop(index=int(element), inplace=True)
        if "res_heat_exchanger" in self.net and len(self.net.res_heat_exchanger):
            self.net.res_heat_exchanger.drop(
                index=int(element), inplace=True, errors="ignore")
        idx.heat_exchangers = np.delete(idx.heat_exchangers, pos)
        p.producer_qext_w = np.delete(p.producer_qext_w, pos, axis=0)
        meta = next(m for m in idx.producer_meta
                    if m["kind"] == "heat_exchanger"
                    and int(m["element"]) == int(element))
        idx.producer_meta.remove(meta)
        self._reset_initialization()  # topology CRUD → cold init (SPEC §3.4)
        return meta
