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
from .consumers import ConsumerProfile
from .dp_control import DpController
from .heating_curve import HeatingCurve, from_config
from .net_inputs import NetInputs
from .network_builder import KELVIN, ProfileArrays, build_network
from .producers import PlantModel
from .sensors import MeasurementSet, _r
from .storage import IDLE_MDOT_KG_PER_S, IDLE_QEXT_W, BufferStorage
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
        # SPEC §4.3 worst-point Δp controller (post-solve, once per tick;
        # consumes the OBSERVED layer only) + §4.4 plant dispatch model
        self.dp_control = DpController.from_config(slack_spec.dp_control)
        self.plant = PlantModel()
        # SPEC §4.4 buffer storages (charge/discharge branch pairs + SoC)
        self.storages: list[BufferStorage] = []
        self._next_sid = 0
        # runtime consumer op log (recipes, SPEC §4.6 scenario save/replay)
        self.consumer_ops: list[dict] = []
        # simulated seconds per engine step (storage SoC integration)
        self._dt_s = 86400.0 / float(self.settings.steps_per_day)

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
        """profiles → weather → controllers → storage bookkeeping (§3.4)."""
        net, p, idx = self.net, self.profiles, self.index

        # demand: space heating (weather-override aware, §4.5) + DHW. The
        # §3.2 zero-flow floor applies to temperature-controlled rows only;
        # mdot-pair rows (bypasses, appendix_a consumer C) have a fixed flow
        # and keep their configured standby qext (e.g. the 100 W bypass).
        q_sh = self.weather.scale_space_heating(
            p.q_sh_w[:, tick], idx.q_design_w, tick)
        floor = np.where(idx.mdot_mask, 0.0, self.settings.min_qext_w)
        qext = np.maximum(q_sh + p.q_dhw_w[:, tick], floor)
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

        # storage bookkeeping (SPEC §4.4): exactly ONE branch active per
        # storage — requested mode after power/capacity limits; idle keeps
        # the charge branch at the §3.2 floor pair so the stub pipes never
        # reach zero flow (zero-flow rows are singular).
        dt_h = self._dt_s / 3600.0
        for s in self.storages:
            active, p_kw = s.desired_state(dt_h)
            s.active = active
            hc, cm = net.heat_consumer, net.circ_pump_mass
            if active == "charge":
                hc.at[s.charge_element, "controlled_mdot_kg_per_s"] = np.nan
                hc.at[s.charge_element, "treturn_k"] = s.t_bottom_c + KELVIN
                hc.at[s.charge_element, "qext_w"] = p_kw * 1000.0
                cm.at[s.discharge_element, "in_service"] = False
            elif active == "discharge":
                hc.at[s.charge_element, "treturn_k"] = np.nan
                hc.at[s.charge_element, "controlled_mdot_kg_per_s"] = \
                    IDLE_MDOT_KG_PER_S
                hc.at[s.charge_element, "qext_w"] = IDLE_QEXT_W
                cm.at[s.discharge_element, "mdot_flow_kg_per_s"] = \
                    s.discharge_mdot(p_kw)
                cm.at[s.discharge_element, "t_flow_k"] = s.t_top_c + KELVIN
                cm.at[s.discharge_element, "in_service"] = True
            else:  # idle: charge branch at the §3.2 floor, pump off
                hc.at[s.charge_element, "treturn_k"] = np.nan
                hc.at[s.charge_element, "controlled_mdot_kg_per_s"] = \
                    IDLE_MDOT_KG_PER_S
                hc.at[s.charge_element, "qext_w"] = IDLE_QEXT_W
                cm.at[s.discharge_element, "in_service"] = False

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
                # storage SoC advances from the SOLVED results, before the
                # frame is collected (the frame carries this tick's SoC)
                self._integrate_storages()
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
                # Δp control (§4.3): ONE clamped proportional step per tick,
                # after the solve, fed from the OBSERVED layer only — the
                # pump visibly converges over ticks, no inner re-solve loop.
                obs = payload.get("observed_summary") or {}
                plift = float(self.net.circ_pump_pressure.at[
                    self.index.slack, "plift_bar"])
                new_plift = self.dp_control.step(plift, obs.get("dp_worst_bar"))
                if abs(new_plift - plift) > 1e-12:
                    self.net.circ_pump_pressure.at[
                        self.index.slack, "plift_bar"] = new_plift
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
        dp = self.dp_control
        return {
            "heating_curve": (self.heating_curve.params()
                              if self.heating_curve is not None else None),
            "dp_control": {
                "mode": dp.mode,
                "setpoint_bar": _r(dp.setpoint_bar),
                "k": _r(dp.k),
                "max_step_bar": _r(dp.max_step_bar),
                "plift_bar": _r(plift),
                "dp_worst_observed_bar": _r(dp.last_dp_observed_bar),
            },
        }

    def set_plift(self, plift_bar: float) -> float:
        """Direct pump setting (fixed mode, 'ungeregelte Pumpe')."""
        v = max(self.dp_control.plift_min_bar,
                min(self.dp_control.plift_max_bar, float(plift_bar)))
        self.net.circ_pump_pressure.at[self.index.slack, "plift_bar"] = v
        return v

    def _integrate_storages(self) -> None:
        """Advance every storage's SoC from the solved results (§4.4).

        Charge: the branch's ``qext_w`` setpoint is the realized heat drawn.
        Discharge: realized heat = ``mdot·cp·(t_outlet − t_from)`` from
        ``res_circ_pump_mass`` (arriving return → store-top flow). Idle: the
        floor standby draws ~0.1 kW from the net but is a standing loss —
        the SoC does not move.
        """
        dt_h = self._dt_s / 3600.0
        for s in self.storages:
            if s.active == "charge":
                q_kw = float(self.net.heat_consumer.at[
                    s.charge_element, "qext_w"]) / 1000.0
                s.integrate(q_kw, dt_h)
            elif s.active == "discharge":
                rm = self.net.res_circ_pump_mass.loc[s.discharge_element]
                t_mean = (float(rm.t_outlet_k) + float(rm.t_from_k)) / 2
                cp = float(self._fluid.get_heat_capacity(
                    np.array([t_mean]))[0])
                q_dis_w = abs(float(rm.mdot_from_kg_per_s)) * cp * (
                    float(rm.t_outlet_k) - float(rm.t_from_k))
                s.integrate(-q_dis_w / 1000.0, dt_h)
            else:  # idle: standby draw, no SoC movement (standing loss)
                s.q_kw = float(self.net.heat_consumer.at[
                    s.charge_element, "qext_w"]) / 1000.0

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

        # --- storage branches (SPEC §4.4): the charge branch always draws
        # from the net (charging power, or the §3.2 idle standby — a standing
        # loss); an in-service discharge pump feeds in like a producer. ---
        q_charge_w = 0.0
        q_discharge_w = 0.0
        for s in self.storages:
            q_charge_w += float(net.heat_consumer.at[
                s.charge_element, "qext_w"])
            if s.active == "discharge" and bool(net.circ_pump_mass.at[
                    s.discharge_element, "in_service"]):
                rm = net.res_circ_pump_mass.loc[s.discharge_element]
                t_mean = (float(rm.t_outlet_k) + float(rm.t_from_k)) / 2
                cp_s = float(fluid.get_heat_capacity(np.array([t_mean]))[0])
                q_discharge_w += abs(float(rm.mdot_from_kg_per_s)) * cp_s * (
                    float(rm.t_outlet_k) - float(rm.t_from_k))

        # pump_mass producers feed like the plant: mdot·cp·(t_out − t_in)
        # (realized, from the result table — the M2 fixture had none, so
        # they only enter the balance since their M4 placement CRUD)
        q_pump_mass_w: dict[int, float] = {}
        for el in idx.pump_mass:
            rm = net.res_circ_pump_mass.loc[int(el)]
            t_mean = (float(rm.t_outlet_k) + float(rm.t_from_k)) / 2
            cp_pm = float(fluid.get_heat_capacity(np.array([t_mean]))[0])
            q_pump_mass_w[int(el)] = abs(float(rm.mdot_from_kg_per_s)) * \
                cp_pm * (float(rm.t_outlet_k) - float(rm.t_from_k))

        q_feed_in_w = (q_feed_plant_w + q_secondary_w + q_discharge_w
                       + float(sum(q_pump_mass_w.values())))

        # consumers only — storage charge branches are heat_consumer rows
        # too but belong to the storage bucket, never to the demand KPI
        rhc_c = rhc.loc[idx.consumers]
        q_demand_w = float(rhc_c.qext_w.sum())
        balance_err_w = q_feed_in_w - (
            q_demand_w + q_loss_total_w + q_charge_w)
        loss_pct = 100.0 * q_loss_total_w / q_feed_in_w if q_feed_in_w else None

        # --- worst-point Δp + argmin (SPEC §3.6) ---
        dp_cons = (rhc_c.p_from_bar - rhc_c.p_to_bar).to_numpy()
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
             "kind": (idx.consumer_kinds[i] if idx.consumer_kinds
                      else "consumer"),
             "q_kw": _r(rhc_c.qext_w.iloc[i] / 1000.0),
             "mdot_kg_per_s": _r(rhc_c.mdot_from_kg_per_s.iloc[i]),
             "t_supply_c": _r(rhc_c.t_from_k.iloc[i] - KELVIN),
             "t_return_c": _r(rhc_c.t_outlet_k.iloc[i] - KELVIN),
             "dp_bar": _r(dp_cons[i])}
            for i in range(len(idx.consumers))
        ]
        t_amb_now = self.weather.t_amb(tick)
        t_ground_now = self.weather.t_ground(tick)
        producers = []
        for meta in idx.producer_meta:
            # wire id = platform-unique pid, never the per-kind element index
            entry = {"id": int(meta["pid"]), "kind": meta["kind"],
                     "name": meta["name"], "node": meta["node"]}
            if meta["kind"] == "slack":
                q_kw = q_feed_plant_w / 1000.0
                entry.update({
                    "q_kw": _r(q_kw),
                    "t_flow_c": _r(rc.t_outlet_k - KELVIN),
                    "plift_bar": _r(net.circ_pump_pressure.at[
                        idx.slack, "plift_bar"]),
                    "pump_el_kw": _r(pump_el_w / 1000.0),
                    "plant_kind": self.plant.kind,
                })
                # §4.4 platform dispatch model: electric/fuel side per tick
                # (HP COP from the LIVE flow temperature and weather)
                entry.update({k: _r(v) for k, v in self.plant.metrics(
                    q_kw, float(rc.t_outlet_k) - KELVIN,
                    t_amb_now, t_ground_now).items()})
            elif meta["kind"] == "heat_exchanger":
                # input setpoint — res_heat_exchanger has no qext_w column
                q = float(net.heat_exchanger.at[meta["element"], "qext_w"])
                entry["q_kw"] = _r(-q / 1000.0)  # feed-in positive on the wire
            else:  # pump_mass
                rm = net.res_circ_pump_mass.loc[meta["element"]]
                entry.update({
                    "q_kw": _r(q_pump_mass_w.get(
                        int(meta["element"]), 0.0) / 1000.0),  # realized
                    "t_flow_c": _r(rm.t_outlet_k - KELVIN),
                    "mdot_kg_per_s": _r(abs(rm.mdot_from_kg_per_s)),
                })
            producers.append(entry)

        storages = [
            {"id": s.sid, "name": s.name, "node": s.node,
             "soc_kwh": _r(s.soc_kwh, 3), "capacity_kwh": _r(s.capacity_kwh),
             "power_kw": _r(s.power_kw), "mode": s.mode, "active": s.active,
             "q_kw": _r(s.q_kw)}
            for s in self.storages
        ]

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
            "q_storage_kw": _r((q_charge_w - q_discharge_w) / 1000.0),
            "balance_err_kw": _r(balance_err_w / 1000.0),
        }
        payload = {
            "junctions": junctions,
            "pipes": pipes,
            "consumers": consumers,
            "producers": producers,
            "storages": storages,
            "summary": summary,
            "weather": self._weather_dict(tick),
            "controls": self._controls_dict(),
        }
        # observed layer (SPEC §8a): projection of the truth payload onto the
        # sensored elements — every frame carries measurements/observed_summary
        payload["measurements"], payload["observed_summary"] = \
            self.measurements.observe(payload)
        return payload

    # -- runtime equipment CRUD (SPEC §4.4) ------------------------------------
    #
    # Blueprint pattern throughout: mutate the live net directly (no rebuild),
    # extend the dense profile arrays + index records (row order = element
    # order), reset the initialization per §3.4 (topology CRUD), tolerate
    # racing solves per §3.3 (a poisoned step self-heals next tick).

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
                "element": int(hx), "node": node, "name": name,
                "runtime": True,
                "inner_diameter_mm": float(inner_diameter_mm)}
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

    def config_heat_exchanger(self, element: int, qext_w: float) -> None:
        """Re-dispatch a placed heat_exchanger (constant feed-in, W > 0)."""
        idx, p = self.index, self.profiles
        pos_arr = np.nonzero(idx.heat_exchangers == int(element))[0]
        if len(pos_arr) == 0:
            raise KeyError(f"no heat_exchanger with element index {element}")
        p.producer_qext_w[int(pos_arr[0]), :] = float(qext_w)
        self.net.heat_exchanger.at[int(element), "qext_w"] = -float(qext_w)

    def add_pump_mass(
        self,
        node: str,
        mdot_flow_kg_per_s: float,
        t_flow_k: float,
        p_flow_bar: float | None = None,
        name: str | None = None,
    ) -> dict:
        """Place a ``circ_pump_const_mass_flow`` producer at *node*.

        ``p_flow_bar=None`` (default) creates the **pressure-free** ``type=
        "t"`` variant: fixed mdot at fixed flow temperature, no pressure
        constraint. ⚠ Runtime-verified (2026-07-16): a pressure-fixing
        "pt" pump (the M1 *file* contract) over-determines the hydraulics
        of a loop that already has its pressure slack — ALL retry-ladder
        tiers fail on the Appendix A net. Passing an explicit ``p_flow_bar``
        keeps the "pt" booster semantics for expert use (frames may come
        back ``converged=false`` — that is data, not an error).
        """
        idx, p = self.index, self.profiles
        jr = idx.junction_return[node]  # KeyError -> unknown node (API: 400)
        js = idx.junction_supply[node]
        name = name or f"pump_mass_{node}"
        mdot = float(mdot_flow_kg_per_s)
        pm = pp.create_circ_pump_const_mass_flow(
            self.net, return_junction=jr, flow_junction=js,
            p_flow_bar=(None if p_flow_bar is None else float(p_flow_bar)),
            mdot_flow_kg_per_s=mdot, t_flow_k=float(t_flow_k),
            type=("t" if p_flow_bar is None else "pt"), name=name)
        idx.pump_mass = np.append(idx.pump_mass, pm)
        p.pump_mass_mdot = np.vstack(
            [p.pump_mass_mdot, np.full((1, p.steps), mdot)])
        meta = {"pid": idx.next_pid(), "kind": "pump_mass",
                "element": int(pm), "node": node, "name": name,
                "runtime": True}
        idx.producer_meta.append(meta)
        self._reset_initialization()  # topology CRUD → cold init (SPEC §3.4)
        return meta

    def remove_pump_mass(self, element: int) -> dict:
        """Remove the pump_mass producer with pandapipes *element* index."""
        idx, p = self.index, self.profiles
        pos_arr = np.nonzero(idx.pump_mass == int(element))[0]
        if len(pos_arr) == 0:
            raise KeyError(f"no pump_mass with element index {element}")
        pos = int(pos_arr[0])
        self.net.circ_pump_mass.drop(index=int(element), inplace=True)
        if "res_circ_pump_mass" in self.net and len(self.net.res_circ_pump_mass):
            self.net.res_circ_pump_mass.drop(
                index=int(element), inplace=True, errors="ignore")
        idx.pump_mass = np.delete(idx.pump_mass, pos)
        p.pump_mass_mdot = np.delete(p.pump_mass_mdot, pos, axis=0)
        meta = next(m for m in idx.producer_meta
                    if m["kind"] == "pump_mass"
                    and int(m["element"]) == int(element))
        idx.producer_meta.remove(meta)
        self._reset_initialization()
        return meta

    def config_pump_mass(
        self,
        element: int,
        mdot_flow_kg_per_s: float | None = None,
        t_flow_k: float | None = None,
    ) -> None:
        """Re-dispatch a pump_mass producer (constant mdot and/or t_flow)."""
        idx, p = self.index, self.profiles
        pos_arr = np.nonzero(idx.pump_mass == int(element))[0]
        if len(pos_arr) == 0:
            raise KeyError(f"no pump_mass with element index {element}")
        if mdot_flow_kg_per_s is not None:
            p.pump_mass_mdot[int(pos_arr[0]), :] = float(mdot_flow_kg_per_s)
            self.net.circ_pump_mass.at[
                int(element), "mdot_flow_kg_per_s"] = float(mdot_flow_kg_per_s)
        if t_flow_k is not None:
            self.net.circ_pump_mass.at[
                int(element), "t_flow_k"] = float(t_flow_k)

    # -- buffer storage (SPEC §4.4) --------------------------------------------

    def add_storage(
        self,
        node: str,
        capacity_kwh: float,
        power_kw: float,
        name: str | None = None,
        t_top_c: float = 80.0,
        t_bottom_c: float = 45.0,
        mode: str = "idle",
    ) -> BufferStorage:
        """Place a buffer storage at *node* (charge/discharge branch pair).

        Discharge pump: ``type="t"`` with **no** ``p_flow_bar`` — the default
        "pt" type fixes the junction pressure against the slack field and the
        solve diverges (runtime-verified 2026-07-16; see storage.py).
        """
        idx = self.index
        jr = idx.junction_return[node]  # KeyError -> unknown node (API: 400)
        js = idx.junction_supply[node]
        name = name or f"storage_{node}"
        s_tmp = BufferStorage(  # for the nominal discharge mdot only
            sid=-1, node=node, name=name, capacity_kwh=float(capacity_kwh),
            power_kw=float(power_kw), charge_element=-1, discharge_element=-1,
            t_top_c=float(t_top_c), t_bottom_c=float(t_bottom_c))
        ch = pp.create_heat_consumer(
            self.net, from_junction=js, to_junction=jr,
            qext_w=IDLE_QEXT_W, controlled_mdot_kg_per_s=IDLE_MDOT_KG_PER_S,
            name=f"{name}_charge")
        dis = pp.create_circ_pump_const_mass_flow(
            self.net, return_junction=jr, flow_junction=js,
            p_flow_bar=None, t_flow_k=float(t_top_c) + KELVIN,
            mdot_flow_kg_per_s=s_tmp.discharge_mdot(float(power_kw)),
            type="t", in_service=False, name=f"{name}_discharge")
        s = BufferStorage(
            sid=self._next_sid, node=node, name=name,
            capacity_kwh=float(capacity_kwh), power_kw=float(power_kw),
            charge_element=int(ch), discharge_element=int(dis),
            mode=mode if mode in ("idle", "charge", "discharge") else "idle",
            t_top_c=float(t_top_c), t_bottom_c=float(t_bottom_c))
        self._next_sid += 1
        self.storages.append(s)
        self._reset_initialization()  # topology CRUD → cold init (SPEC §3.4)
        return s

    def get_storage(self, sid: int) -> BufferStorage:
        for s in self.storages:
            if s.sid == int(sid):
                return s
        raise KeyError(f"no storage with id {sid}")

    def remove_storage(self, sid: int) -> BufferStorage:
        s = self.get_storage(sid)
        self.net.heat_consumer.drop(index=s.charge_element, inplace=True)
        if "res_heat_consumer" in self.net and len(self.net.res_heat_consumer):
            self.net.res_heat_consumer.drop(
                index=s.charge_element, inplace=True, errors="ignore")
        self.net.circ_pump_mass.drop(index=s.discharge_element, inplace=True)
        if "res_circ_pump_mass" in self.net and len(self.net.res_circ_pump_mass):
            self.net.res_circ_pump_mass.drop(
                index=s.discharge_element, inplace=True, errors="ignore")
        self.storages.remove(s)
        self._reset_initialization()
        return s

    # -- consumers & bypasses (SPEC §4.4 / §3.2) --------------------------------

    def add_consumer(
        self,
        node: str,
        profile: ConsumerProfile,
        treturn_c: float,
        name: str | None = None,
        t_supply_min_c: float = 60.0,
        recipe: dict | None = None,
    ) -> dict:
        """Place a heat_consumer at an existing trench node (qext+treturn).

        *profile* arrays may come at any resolution — they are staircase-
        resampled onto the engine tick grid. *recipe* (archetype/seed/... or
        constant params) goes into the consumer op log so scenario save/load
        replays the placement deterministically (SPEC §4.6).
        """
        from .consumers import _resample_staircase as _resample

        idx, p = self.index, self.profiles
        jr = idx.junction_return[node]  # KeyError -> unknown node (API: 400)
        js = idx.junction_supply[node]
        name = name or f"consumer_{node}_{len(idx.consumers)}"
        q_sh = _resample(profile.q_sh_w, p.steps)
        q_dhw = _resample(profile.q_dhw_w, p.steps)
        qext0 = max(float(q_sh[0] + q_dhw[0]), self.settings.min_qext_w)
        hc = pp.create_heat_consumer(
            self.net, from_junction=js, to_junction=jr,
            qext_w=qext0, treturn_k=float(treturn_c) + KELVIN, name=name)
        idx.consumers = np.append(idx.consumers, hc)
        idx.consumer_names.append(name)
        idx.consumer_nodes.append(node)
        idx.consumer_kinds.append("consumer")
        idx.treturn_mask = np.append(idx.treturn_mask, True)
        idx.deltat_mask = np.append(idx.deltat_mask, False)
        idx.mdot_mask = np.append(idx.mdot_mask, False)
        idx.q_design_w = np.append(idx.q_design_w, float(profile.q_design_w))
        idx.t_supply_min_c = np.append(
            idx.t_supply_min_c, float(t_supply_min_c))
        p.q_sh_w = np.vstack([p.q_sh_w, q_sh[None, :]])
        p.q_dhw_w = np.vstack([p.q_dhw_w, q_dhw[None, :]])
        p.qext_w = np.vstack([p.qext_w, np.maximum(
            q_sh + q_dhw, self.settings.min_qext_w)[None, :]])
        p.treturn_k = np.vstack([p.treturn_k, np.full(
            (1, p.steps), float(treturn_c) + KELVIN)])
        self.consumer_ops.append({
            "op": "add_consumer", "node": node, "name": name,
            "treturn_c": float(treturn_c),
            "t_supply_min_c": float(t_supply_min_c), **(recipe or {})})
        self._reset_initialization()  # topology CRUD → cold init (SPEC §3.4)
        return {"id": int(hc), "name": name, "node": node,
                "kind": "consumer", "q_design_w": float(profile.q_design_w)}

    def add_bypass(
        self,
        node: str,
        mdot_kg_per_s: float = 0.02,
        qext_w: float = 100.0,
        name: str | None = None,
    ) -> dict:
        """Place a Netzschluss-Bypass — the SPEC §3.2 canonical pair 3
        (tiny fixed mdot + small standby qext); also the zero-flow guard."""
        idx, p = self.index, self.profiles
        jr = idx.junction_return[node]  # KeyError -> unknown node (API: 400)
        js = idx.junction_supply[node]
        name = name or f"bypass_{node}"
        hc = pp.create_heat_consumer(
            self.net, from_junction=js, to_junction=jr,
            qext_w=float(qext_w),
            controlled_mdot_kg_per_s=float(mdot_kg_per_s), name=name)
        idx.consumers = np.append(idx.consumers, hc)
        idx.consumer_names.append(name)
        idx.consumer_nodes.append(node)
        idx.consumer_kinds.append("bypass")
        idx.treturn_mask = np.append(idx.treturn_mask, False)
        idx.deltat_mask = np.append(idx.deltat_mask, False)
        idx.mdot_mask = np.append(idx.mdot_mask, True)
        idx.q_design_w = np.append(idx.q_design_w, float(qext_w))
        idx.t_supply_min_c = np.append(idx.t_supply_min_c, 0.0)
        # standby heat sits in the DHW slot: never rescaled by the weather
        # override, and the mdot-row floor keeps it as configured (§3.2)
        p.q_sh_w = np.vstack([p.q_sh_w, np.zeros((1, p.steps))])
        p.q_dhw_w = np.vstack([p.q_dhw_w, np.full((1, p.steps), float(qext_w))])
        p.qext_w = np.vstack([p.qext_w, np.full((1, p.steps), float(qext_w))])
        p.treturn_k = np.vstack([p.treturn_k, np.full((1, p.steps), np.nan)])
        self.consumer_ops.append({
            "op": "add_bypass", "node": node, "name": name,
            "mdot_kg_per_s": float(mdot_kg_per_s), "qext_w": float(qext_w)})
        self._reset_initialization()
        return {"id": int(hc), "name": name, "node": node, "kind": "bypass",
                "q_design_w": float(qext_w)}

    def remove_consumer(self, element: int) -> dict:
        """Remove a consumer or bypass by heat_consumer element index.

        Raises ``KeyError`` for an unknown element and ``ValueError`` when it
        is the last consumer (a net without any consumer has no flow — the
        API maps that to a 409 conflict).
        """
        idx, p = self.index, self.profiles
        pos_arr = np.nonzero(idx.consumers == int(element))[0]
        if len(pos_arr) == 0:
            raise KeyError(f"no consumer with element index {element}")
        if len(idx.consumers) <= 1:
            raise ValueError(
                "cannot remove the last consumer — a net without consumers "
                "carries no flow (zero-flow rows are singular, SPEC §3.2)")
        pos = int(pos_arr[0])
        name = idx.consumer_names[pos]
        kind = idx.consumer_kinds[pos] if idx.consumer_kinds else "consumer"
        node = idx.consumer_nodes[pos]
        self.net.heat_consumer.drop(index=int(element), inplace=True)
        if "res_heat_consumer" in self.net and len(self.net.res_heat_consumer):
            self.net.res_heat_consumer.drop(
                index=int(element), inplace=True, errors="ignore")
        idx.consumers = np.delete(idx.consumers, pos)
        del idx.consumer_names[pos]
        del idx.consumer_nodes[pos]
        if idx.consumer_kinds:
            del idx.consumer_kinds[pos]
        idx.treturn_mask = np.delete(idx.treturn_mask, pos)
        idx.deltat_mask = np.delete(idx.deltat_mask, pos)
        idx.mdot_mask = np.delete(idx.mdot_mask, pos)
        idx.q_design_w = np.delete(idx.q_design_w, pos)
        idx.t_supply_min_c = np.delete(idx.t_supply_min_c, pos)
        p.q_sh_w = np.delete(p.q_sh_w, pos, axis=0)
        p.q_dhw_w = np.delete(p.q_dhw_w, pos, axis=0)
        p.qext_w = np.delete(p.qext_w, pos, axis=0)
        p.treturn_k = np.delete(p.treturn_k, pos, axis=0)
        self.consumer_ops.append({"op": "remove_consumer", "name": name})
        self._reset_initialization()
        return {"id": int(element), "name": name, "node": node, "kind": kind}
