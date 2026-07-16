"""Build-once network construction (SPEC §3.4, §5, §9.1).

``build_network(inputs, ...)`` constructs the pandapipes net **once** per
scenario load and returns ``(net, ProfileArrays)``. Each simulation tick only
overwrites element values from the dense profile arrays, solves, and reads
results — the net is never rebuilt per step.

Conventions (all binding, see SPEC):

* Every trench node from ``network_structure.json`` expands into a
  supply/return junction **pair** (``<name>_s`` / ``<name>_r``); every trench
  from ``pipes.json`` expands into a supply pipe (from→to) and a return pipe
  (to→from, direction reversed toward the plant).
* All junctions are initialized with the **supply temperature** (slack
  ``t_flow_k``) — materially helps thermal convergence (SPEC §3.1).
* ``text_k`` is passed **explicitly on every pipe** (the signature default 0
  means 0 K ambient → ~4x fake losses; SPEC Appendix B item 4).
* Element creation order == input row order, so profile row order *is* the
  element index (blueprint "profiles-as-definitions"); the explicit index
  records in :class:`NetIndex` guard the coupling anyway.
* Zero-flow guard: every consumer's total demand is floored at
  ``min_qext_w`` (SPEC §3.2 — zero-flow branches are singular and the
  near-zero-load regime breaks temperature control).

Profile resampling: file resolution → engine tick resolution as a
**staircase** (piecewise-constant repeat). Documented choice per SPEC §4.5 —
it preserves energy sums and DHW peak magnitudes, at the cost of 15-min
stair-steps in temperature curves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandapipes as pp

from .net_inputs import NetInputs

log = logging.getLogger(__name__)

KELVIN = 273.15


@dataclass
class NetIndex:
    """Element index records + static per-element metadata.

    Row order of every array equals the input-file row order (which equals
    the pandapipes element index by construction — guarded, not assumed).
    """

    # consumers (row order = consumers.json order = heat_consumer index)
    consumers: np.ndarray
    consumer_names: list[str]
    consumer_nodes: list[str]
    treturn_mask: np.ndarray   # bool per consumer: qext+treturn pair
    deltat_mask: np.ndarray    # bool: qext+deltat pair
    mdot_mask: np.ndarray      # bool: qext+mdot pair (also bypass shape)
    q_design_w: np.ndarray
    t_supply_min_c: np.ndarray
    # pipes (row order = pipes.json trench order)
    pipes_supply: np.ndarray
    pipes_return: np.ndarray
    pipes_all: np.ndarray      # supply+return concatenated, order = pipe table
    # junctions
    junction_supply: dict[str, int]
    junction_return: dict[str, int]
    junction_names: list[str]  # pandapipes junction index -> trench-node name
    junction_sides: list[str]  # pandapipes junction index -> "s" | "r"
    init_pn_bar: np.ndarray    # build-time pn_bar per junction (failure reset)
    # producers. producer_meta rows carry a platform-unique "pid" (the wire
    # id — element indices live in per-component tables and collide across
    # kinds, e.g. slack 0 vs heat_exchanger 0; added M2) plus the pandapipes
    # "element" index within the kind's own table.
    slack: int                 # circ_pump_pressure table index
    slack_node: str
    heat_exchangers: np.ndarray
    pump_mass: np.ndarray
    producer_meta: list[dict] = field(default_factory=list)
    # per-consumer kind: "consumer" | "bypass" (runtime bypasses, M4)
    consumer_kinds: list[str] = field(default_factory=list)

    def next_pid(self) -> int:
        return 1 + max((int(m["pid"]) for m in self.producer_meta), default=-1)


@dataclass
class ProfileArrays:
    """Dense ``[n_elements, ticks]`` profile arrays at engine tick resolution."""

    steps: int                 # total ticks over the whole horizon
    steps_per_day: int
    n_days: int
    # consumers
    qext_w: np.ndarray         # floored q_sh + q_dhw   [n_cons, T]
    q_sh_w: np.ndarray         # raw space heating      [n_cons, T]
    q_dhw_w: np.ndarray        # raw domestic hot water [n_cons, T]
    treturn_k: np.ndarray      # [n_cons, T]; NaN rows for non-treturn consumers
    # weather
    t_amb_c: np.ndarray        # [T]
    t_ground_c: np.ndarray     # [T]
    # secondary producers
    producer_qext_w: np.ndarray    # positive dispatch [n_hx, T]
    pump_mass_mdot: np.ndarray     # [n_pm, T]
    index: NetIndex


def _resample_staircase(values, n_ticks: int) -> np.ndarray:
    """Piecewise-constant resample of a profile array onto the tick grid."""
    src = np.asarray(values, dtype=float)
    idx = (np.arange(n_ticks, dtype=np.int64) * len(src)) // n_ticks
    return src[idx]


def build_network(
    inputs: NetInputs,
    steps_per_day: int = 1440,
    min_qext_w: float = 500.0,
) -> tuple[pp.pandapipesNet, ProfileArrays]:
    """Construct the pandapipes net + dense profiles from validated inputs."""
    slack = next(p for p in inputs.producers.producers if p.kind == "slack")
    t_init_k = float(slack.t_flow_k)

    n_ticks = inputs.n_days * steps_per_day

    # --- weather at tick resolution (drives text_k below) ---
    t_amb_c = _resample_staircase(inputs.weather.t_amb_c, n_ticks)
    t_ground_c = _resample_staircase(inputs.weather.t_ground_c, n_ticks)

    net = pp.create_empty_network(fluid="water", name=inputs.name)

    # --- junction pairs: one supply + one return per trench node ---
    junction_supply: dict[str, int] = {}
    junction_return: dict[str, int] = {}
    junction_names: list[str] = []
    junction_sides: list[str] = []
    for j in inputs.structure.junctions:
        lat, lon = j.geo
        # pandapipes geodata is (x, y) = (lon, lat) (SPEC §5)
        js = pp.create_junction(net, pn_bar=j.pn_bar, tfluid_k=t_init_k,
                                name=f"{j.name}_s", geodata=(lon, lat))
        jr = pp.create_junction(net, pn_bar=j.pn_bar, tfluid_k=t_init_k,
                                name=f"{j.name}_r", geodata=(lon, lat))
        junction_supply[j.name] = js
        junction_return[j.name] = jr
        junction_names += [j.name, j.name]
        junction_sides += ["s", "r"]

    # --- pipes: supply (from->to) + return (to->from) per trench ---
    text_k0 = float(t_ground_c[0]) + KELVIN
    pipes_supply: list[int] = []
    pipes_return: list[int] = []
    for i, p in enumerate(inputs.pipes.pipes):
        common = dict(length_km=p.length_km, sections=p.sections, text_k=text_k0)
        if p.std_type is not None:
            ps = pp.create_pipe(net, junction_supply[p.from_node],
                                junction_supply[p.to_node], std_type=p.std_type,
                                name=f"trench{i}_s", **common)
            pr = pp.create_pipe(net, junction_return[p.to_node],
                                junction_return[p.from_node], std_type=p.std_type,
                                name=f"trench{i}_r", **common)
        else:
            params = dict(inner_diameter_mm=p.inner_diameter_mm,
                          u_w_per_m2k=p.u_w_per_m2k, **common)
            if p.k_mm is not None:
                params["k_mm"] = p.k_mm
            ps = pp.create_pipe_from_parameters(
                net, junction_supply[p.from_node], junction_supply[p.to_node],
                name=f"trench{i}_s", **params)
            pr = pp.create_pipe_from_parameters(
                net, junction_return[p.to_node], junction_return[p.from_node],
                name=f"trench{i}_r", **params)
        pipes_supply.append(ps)
        pipes_return.append(pr)

    # --- consumer profiles (tick resolution) + heat_consumer elements ---
    consumers = inputs.consumers.consumers
    n_cons = len(consumers)
    q_sh = np.empty((n_cons, n_ticks))
    q_dhw = np.empty((n_cons, n_ticks))
    treturn = np.full((n_cons, n_ticks), np.nan)
    treturn_mask = np.zeros(n_cons, dtype=bool)
    deltat_mask = np.zeros(n_cons, dtype=bool)
    mdot_mask = np.zeros(n_cons, dtype=bool)
    consumer_idx: list[int] = []
    for i, c in enumerate(consumers):
        q_sh[i] = _resample_staircase(c.q_sh_w, n_ticks)
        q_dhw[i] = _resample_staircase(c.q_dhw_w, n_ticks)
        name = c.name or f"consumer_{c.node}"
        kwargs: dict = {}
        if c.treturn_k is not None:
            treturn[i] = _resample_staircase(c.treturn_k, n_ticks)
            treturn_mask[i] = True
            kwargs["treturn_k"] = float(treturn[i, 0])
        elif c.deltat_k is not None:
            deltat_mask[i] = True
            kwargs["deltat_k"] = float(c.deltat_k)
        else:
            mdot_mask[i] = True
            kwargs["controlled_mdot_kg_per_s"] = float(c.controlled_mdot_kg_per_s)
        qext0 = max(float(q_sh[i, 0] + q_dhw[i, 0]), min_qext_w)
        hc = pp.create_heat_consumer(
            net, from_junction=junction_supply[c.node],
            to_junction=junction_return[c.node],
            qext_w=qext0, name=name, **kwargs)
        consumer_idx.append(hc)

    # zero-flow guard: floor the total demand (SPEC §3.2). mdot-pair rows
    # (bypasses, mdot-mode consumers) have a fixed flow — no singularity
    # risk — and keep their configured standby qext (e.g. the 100 W bypass).
    qext = q_sh + q_dhw
    floor = np.where(mdot_mask, 0.0, min_qext_w)[:, None]
    n_floored = int(np.count_nonzero(qext < floor))
    if n_floored:
        log.warning(
            "zero-flow guard: flooring %d consumer profile values below %.0f W "
            "(SPEC §3.2 minimum-flow policy)", n_floored, min_qext_w)
    qext = np.maximum(qext, floor)

    # --- producers ---
    slack_idx = -1
    hx_idx: list[int] = []
    pm_idx: list[int] = []
    hx_qext_rows: list[np.ndarray] = []
    pm_mdot_rows: list[np.ndarray] = []
    producer_meta: list[dict] = []
    for p in inputs.producers.producers:
        name = p.name or f"{p.kind}_{p.node}"
        if p.kind == "slack":
            slack_idx = pp.create_circ_pump_const_pressure(
                net, return_junction=junction_return[p.node],
                flow_junction=junction_supply[p.node],
                p_flow_bar=float(p.p_flow_bar), plift_bar=float(p.plift_bar),
                t_flow_k=float(p.t_flow_k), name=name)
            producer_meta.append({"pid": len(producer_meta), "kind": "slack",
                                  "element": slack_idx,
                                  "node": p.node, "name": name})
        elif p.kind == "heat_exchanger":
            dispatch = _resample_staircase(p.qext_w, n_ticks)
            # feed-in = negative qext_w by convention (SPEC §3.1/§3.4)
            hx = pp.create_heat_exchanger(
                net, from_junction=junction_return[p.node],
                to_junction=junction_supply[p.node],
                qext_w=-float(dispatch[0]),
                inner_diameter_mm=float(p.inner_diameter_mm), name=name)
            hx_idx.append(hx)
            hx_qext_rows.append(dispatch)
            producer_meta.append({"pid": len(producer_meta),
                                  "kind": "heat_exchanger", "element": hx,
                                  "node": p.node, "name": name})
        else:  # pump_mass
            mdot = (_resample_staircase(p.mdot_flow_kg_per_s, n_ticks)
                    if isinstance(p.mdot_flow_kg_per_s, list)
                    else np.full(n_ticks, float(p.mdot_flow_kg_per_s)))
            pm = pp.create_circ_pump_const_mass_flow(
                net, return_junction=junction_return[p.node],
                flow_junction=junction_supply[p.node],
                p_flow_bar=float(p.p_flow_bar),
                mdot_flow_kg_per_s=float(mdot[0]),
                t_flow_k=float(p.t_flow_k), name=name)
            pm_idx.append(pm)
            pm_mdot_rows.append(mdot)
            producer_meta.append({"pid": len(producer_meta),
                                  "kind": "pump_mass", "element": pm,
                                  "node": p.node, "name": name})

    index = NetIndex(
        consumers=np.asarray(consumer_idx, dtype=np.int64),
        consumer_names=[c.name or f"consumer_{c.node}" for c in consumers],
        consumer_nodes=[c.node for c in consumers],
        consumer_kinds=["consumer"] * len(consumers),
        treturn_mask=treturn_mask,
        deltat_mask=deltat_mask,
        mdot_mask=mdot_mask,
        q_design_w=np.asarray([c.q_design_w for c in consumers], dtype=float),
        t_supply_min_c=np.asarray([c.t_supply_min_c for c in consumers], dtype=float),
        pipes_supply=np.asarray(pipes_supply, dtype=np.int64),
        pipes_return=np.asarray(pipes_return, dtype=np.int64),
        pipes_all=np.asarray(sorted(pipes_supply + pipes_return), dtype=np.int64),
        junction_supply=junction_supply,
        junction_return=junction_return,
        junction_names=junction_names,
        junction_sides=junction_sides,
        init_pn_bar=net.junction["pn_bar"].to_numpy(copy=True),
        slack=slack_idx,
        slack_node=slack.node,
        heat_exchangers=np.asarray(hx_idx, dtype=np.int64),
        pump_mass=np.asarray(pm_idx, dtype=np.int64),
        producer_meta=producer_meta,
    )
    profiles = ProfileArrays(
        steps=n_ticks,
        steps_per_day=steps_per_day,
        n_days=inputs.n_days,
        qext_w=qext,
        q_sh_w=q_sh,
        q_dhw_w=q_dhw,
        treturn_k=treturn,
        t_amb_c=t_amb_c,
        t_ground_c=t_ground_c,
        producer_qext_w=(np.vstack(hx_qext_rows) if hx_qext_rows
                         else np.zeros((0, n_ticks))),
        pump_mass_mdot=(np.vstack(pm_mdot_rows) if pm_mdot_rows
                        else np.zeros((0, n_ticks))),
        index=index,
    )
    return net, profiles
