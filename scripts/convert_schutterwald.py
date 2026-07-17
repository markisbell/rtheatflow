"""Convert the pandapipes Schutterwald nets into ``data/networks/schutterwald``.

Real town (Schutterwald, Baden, 48.46 N / 7.88 E), real trench geometry —
the flagship map demo. Two vendored sources (BSD-3, e2nIEE/pandapipes
v0.14.0, ``data/sources/schutterwald/`` incl. LICENSE):

* ``sw_heat.json`` — the district-heating example net (STANET export):
  244 supply + 244 return junctions (name-paired ``return_<name>``),
  241 pipes per side, 44 heat consumers, one 70 degC pressure pump.
  Known data bugs (all verified, see DATASET.md): every pipe carries
  inner_diameter 800 mm and u = 10 W/m2K (dead columns), 34 zero-length
  pipes per side, junction/pipe ``geo`` columns store **[lat, lon]**
  (reversed vs the GeoJSON spec — which happens to be this platform's
  native order), ``junction_geodata`` x/y is EPSG:31467.
* ``gas_net_schutterwald_1bar.json`` — donor for heterogeneous loads:
  1506 house sinks with SLP ``profile_id`` + ``demand_m3_per_a``,
  same EPSG:31467 frame.

Conversion (deterministic, seeded):

1. **Supply side only** → the single-sided trench contract (the builder
   re-creates the mirrored return network — the original return side is an
   exact name-mirror, verified). Zero-length pipes and the two fully-open
   DN200 feeder valves are contracted away by merging their end nodes
   (union-find; the merged node keeps the consumer-bearing junction's name).
2. **Loads**: each gas house is assigned to its nearest heat substation in
   EPSG:31467; houses farther than ``SERVICE_CUTOFF_M`` (50 m — a typical
   house-connection length) stay off the DH net. Whole-town aggregation
   (33.4 GWh/a) would put 12.7 MWh/(m a) on a 2.6 km trench network that
   physically covers only the northern streets — the 50 m service area
   yields 3.3 GWh/a and a literature-plausible linear heat density of
   ~1.3 MWh/(m a). Documented deviation from a naive all-houses reading.
   Gas -> heat: m3/a x 10 kWh/m3 x 0.9 boiler efficiency. Substations
   without any house in reach keep the sw_heat shipped annual demand
   (9.48 MWh/a). Archetype per substation by annual energy
   (<=15 MWh EFH_SAN, <=35 MWh EFH_ALT, else MFH), profile scaled to the
   target annual, DHW variants rotating, seeded +-30 min shift.
3. **Day window**: the 90th-percentile space-heating day of the archetype
   year (demo_dorf convention), 96 x 15 min; weather re-derived by
   inverting the SPEC 4.5 degree-hour factor (override math consistent).
4. **Pipe DNs** re-sized from aggregated downstream design loads on the
   trench tree at dT = 40 K to <= 1.2 m/s, next ISOPLUS_DRE*_STD up
   (realistic_year.py convention) — the source diameters are dead columns.
5. **Plant**: 3G heating-curve preset (110/70) at the original pump node,
   p_flow 6 bar, plift 3 bar. Leaf nodes without a consumer get the
   canonical SPEC 3.2 bypass pair.

Usage:  python scripts/convert_schutterwald.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "data" / "sources" / "schutterwald"
PROFILES_DIR = REPO / "data" / "profiles"
OUT_DIR = REPO / "data" / "networks" / "schutterwald"

SEED = 20260717
STEPS = 96
RESOLUTION_MIN = 15
SERVICE_CUTOFF_M = 50.0     # house-connection reach (documented deviation)
KWH_PER_M3 = 10.0           # natural-gas heating value
BOILER_ETA = 0.9            # gas boiler -> useful heat
DT_DESIGN_K = 40.0          # sizing spread (3G 110/70 network, conservative)
V_MAX = 1.2                 # m/s sizing target (realistic_year convention)
CP = 4187.0
RHO = 970.0
T_RETURN_C = {"EFH_ALT_4P": 55.0, "EFH_SAN_4P": 45.0, "MFH_ALT_10WE": 50.0}
FALLBACK_ANNUAL_KWH = 9482.56   # sw_heat annual_consumption (Wh -> kWh)


def pick_day(q_sh_year: np.ndarray) -> int:
    days = q_sh_year[: 365 * STEPS].reshape(-1, STEPS).sum(axis=1)
    return int(np.argsort(days)[int(0.90 * (len(days) - 1))])


def isoplus_std_types() -> dict[str, float]:
    import pandapipes as pp
    net = pp.create_empty_network(fluid="water")
    return {name: float(p["inner_diameter_mm"])
            for name, p in net.std_types["pipe"].items()
            if name.startswith("ISOPLUS_DRE") and name.endswith("_STD")}


def pick_std_type(mdot: float, lib: dict[str, float]) -> str:
    last = None
    for name, d_mm in sorted(lib.items(), key=lambda kv: kv[1]):
        v = mdot / (RHO * math.pi * (d_mm / 1000.0) ** 2 / 4.0)
        if v <= V_MAX:
            return name
        last = name
    return last


class UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def geo_point(raw) -> tuple[float, float]:
    """junction.geo stores {'coordinates': [lat, lon]} — reversed vs GeoJSON,
    which is exactly this platform's (lat, lon) order. Sanity-checked."""
    obj = json.loads(raw) if isinstance(raw, str) else raw
    lat, lon = obj["coordinates"]
    assert 48.4 < lat < 48.5 and 7.8 < lon < 8.0, (lat, lon)
    return float(lat), float(lon)


def geo_line(raw) -> list[list[float]]:
    obj = json.loads(raw) if isinstance(raw, str) else raw
    pts = [[float(a), float(b)] for a, b in obj["coordinates"]]
    for lat, lon in pts:
        assert 48.4 < lat < 48.5 and 7.8 < lon < 8.0, (lat, lon)
    return pts


def main() -> None:
    import pandapipes as pp

    rng = np.random.default_rng(SEED)
    heat = pp.from_json(SRC / "sw_heat.json")
    gas = pp.from_json(SRC / "gas_net_schutterwald_1bar.json")

    j = heat.junction
    is_ret = j.name.astype(str).str.startswith("return")
    sup_idx = set(j[~is_ret].index)
    plant_j = int(heat.circ_pump_pressure.at[0, "flow_junction"])

    pipes_all = heat.pipe
    p_sup = pipes_all[pipes_all.from_junction.isin(sup_idx)
                      & pipes_all.to_junction.isin(sup_idx)]

    # --- merge zero-length pipes + fully-open supply feeder valves ---
    uf = UnionFind()
    n_zero = 0
    for _, r in p_sup.iterrows():
        if r.length_km <= 0:
            uf.union(int(r.from_junction), int(r.to_junction))
            n_zero += 1
    n_valve = 0
    for _, v in heat.valve.iterrows():
        if int(v.junction) in sup_idx and int(v.element) in sup_idx:
            assert bool(v.opened) and v.et == "ju"
            uf.union(int(v.junction), int(v.element))
            n_valve += 1
    for i in sup_idx:
        uf.find(i)

    cons_junctions = set(heat.heat_consumer.from_junction.astype(int))
    # representative per root: prefer the plant junction, then a consumer-
    # bearing junction, else the lowest index (stable)
    members: dict[int, list[int]] = {}
    for i in sup_idx:
        members.setdefault(uf.find(i), []).append(i)
    rep: dict[int, int] = {}
    for root, ms in members.items():
        best = sorted(ms, key=lambda m: (m != plant_j, m not in cons_junctions, m))[0]
        for m in ms:
            rep[m] = best
    node_of = {i: str(j.at[rep[i], "name"]) for i in sup_idx}
    trench_nodes = sorted({rep[i] for i in sup_idx})
    print(f"merged {n_zero} zero-length pipes + {n_valve} feeder valves "
          f"-> {len(trench_nodes)} trench nodes")

    # --- loads: gas houses -> nearest substation within the service area ---
    cons = heat.heat_consumer.sort_index()
    sub_j = cons.from_junction.astype(int).values
    sx = heat.junction_geodata.loc[sub_j, "x"].values
    sy = heat.junction_geodata.loc[sub_j, "y"].values
    hj = gas.sink.junction.astype(int).values
    hx = gas.junction_geodata.loc[hj, "x"].values
    hy = gas.junction_geodata.loc[hj, "y"].values
    d2 = (hx[:, None] - sx[None, :]) ** 2 + (hy[:, None] - sy[None, :]) ** 2
    nearest = np.argmin(d2, axis=1)
    dist = np.sqrt(d2[np.arange(len(hj)), nearest])
    served = dist <= SERVICE_CUTOFF_M
    heat_kwh = gas.sink.demand_m3_per_a.values * KWH_PER_M3 * BOILER_ETA

    annual_kwh = np.zeros(len(cons))
    houses = np.zeros(len(cons), dtype=int)
    for i_house in np.where(served)[0]:
        annual_kwh[nearest[i_house]] += heat_kwh[i_house]
        houses[nearest[i_house]] += 1
    n_fallback = int((annual_kwh <= 0).sum())
    annual_kwh[annual_kwh <= 0] = FALLBACK_ANNUAL_KWH
    print(f"served houses: {int(served.sum())}/{len(hj)} "
          f"(cutoff {SERVICE_CUTOFF_M} m), total {annual_kwh.sum()/1e3:.2f} MWh/a, "
          f"{n_fallback} substations on the sw_heat fallback demand")

    # --- archetype assignment + day window ---
    index = json.loads((PROFILES_DIR / "index.json").read_text("utf-8"))
    arch = {a["id"]: json.loads((PROFILES_DIR / a["file"]).read_text("utf-8"))
            for a in index["archetypes"]}
    arch_annual = {a["id"]: a["annual_kwh"]["space_heating"] + a["annual_kwh"]["dhw_mean"]
                   for a in index["archetypes"]}
    arch_qdes = {a["id"]: a["q_design_w"] for a in index["archetypes"]}

    day = pick_day(np.asarray(arch["EFH_ALT_4P"]["q_sh_w"], dtype=float))
    sl = slice(day * STEPS, (day + 1) * STEPS)
    print(f"winter day: {day} of the archetype year")

    consumers = []
    q_design_by_node: dict[str, float] = {}
    dhw_counter: dict[str, int] = {}
    for k, (ci, row) in enumerate(cons.iterrows()):
        target = float(annual_kwh[k])
        aid = ("EFH_SAN_4P" if target <= 15000.0
               else "EFH_ALT_4P" if target <= 35000.0 else "MFH_ALT_10WE")
        a = arch[aid]
        scale = target / arch_annual[aid]
        shift = int(rng.integers(-2, 3))
        variant = dhw_counter.get(aid, 0) % len(a["q_dhw_w_variants"])
        dhw_counter[aid] = variant + 1
        q_sh = np.roll(np.asarray(a["q_sh_w"], float)[sl], shift) * scale
        q_dhw = np.roll(np.asarray(a["q_dhw_w_variants"][variant], float)[sl],
                        shift) * scale
        node = node_of[int(row.from_junction)]
        q_design = arch_qdes[aid] * scale
        q_design_by_node[node] = q_design_by_node.get(node, 0.0) + q_design
        consumers.append({
            "node": node,
            "name": f"UST {node} · {max(int(houses[k]), 1)} Häuser",
            "q_sh_w": [round(float(x), 1) for x in q_sh],
            "q_dhw_w": [round(float(x), 1) for x in q_dhw],
            "treturn_k": [round(273.15 + T_RETURN_C[aid], 2)] * STEPS,
            "annual_kwh": round(target, 1),
            "q_design_w": round(q_design, 1),
            "t_supply_min_c": 60.0,
            "building": aid,
        })

    # --- weather from the day's SH level (SPEC 4.5 inversion) ---
    q_sh_day = np.asarray(arch["EFH_ALT_4P"]["q_sh_w"], float)[sl]
    f_mean = float(q_sh_day.mean() / arch["EFH_ALT_4P"]["q_design_w"])
    t_room, t_design = 20.0, -12.0
    t_mean = t_room - f_mean * (t_room - t_design)
    hours = np.arange(STEPS) * RESOLUTION_MIN / 60.0
    t_amb = t_mean + 2.5 * np.cos((hours - 14.0) / 24.0 * 2 * np.pi)
    t_ground = 6.5 + 0.2 * np.cos((hours - 15.0) / 24.0 * 2 * np.pi)

    # --- trench graph + DN sizing on the tree ---
    trenches = []   # (from_node, to_node, length_km, geometry)
    for _, r in p_sup.iterrows():
        if r.length_km <= 0:
            continue
        a, b = node_of[int(r.from_junction)], node_of[int(r.to_junction)]
        assert a != b, (a, b)
        trenches.append((a, b, float(r.length_km), geo_line(r.geo)))

    plant_node = node_of[plant_j]
    adj: dict[str, list[tuple[str, int]]] = {}
    for t_i, (a, b, _l, _g) in enumerate(trenches):
        adj.setdefault(a, []).append((b, t_i))
        adj.setdefault(b, []).append((a, t_i))

    # downstream design load per trench via DFS from the plant (tree)
    downstream_q = [0.0] * len(trenches)
    leaf_nodes: list[str] = []

    def dfs(node: str, via: int | None) -> float:
        q = q_design_by_node.get(node, 0.0)
        children = [(n, t) for n, t in adj.get(node, []) if t != via]
        if not children and via is not None and q == 0.0:
            leaf_nodes.append(node)
        for n, t in children:
            downstream_q[t] = dfs(n, t)
            q += downstream_q[t]
        return q

    total_q = dfs(plant_node, None)

    lib = isoplus_std_types()
    pipes = []
    dn_hist: dict[str, int] = {}
    for (a, b, length_km, geometry), q_w in zip(trenches, downstream_q):
        mdot = max(q_w, 500.0) / (CP * DT_DESIGN_K)
        std = pick_std_type(mdot, lib)
        dn_hist[std.split("_")[1]] = dn_hist.get(std.split("_")[1], 0) + 1
        pipes.append({
            "from_node": a, "to_node": b, "std_type": std,
            "length_km": round(length_km, 5),
            "sections": 1,
            "geometry": geometry,
        })

    # --- canonical bypasses on consumer-less leaves (SPEC 3.2) ---
    for node in sorted(set(leaf_nodes)):
        consumers.append({
            "node": node,
            "name": f"Bypass {node}",
            "q_sh_w": [100.0] * STEPS,
            "q_dhw_w": [0.0] * STEPS,
            "controlled_mdot_kg_per_s": 0.02,
            "q_design_w": 100.0,
            "t_supply_min_c": 0.0,
        })
    print(f"trenches: {len(pipes)} ({sum(p['length_km'] for p in pipes):.3f} km), "
          f"DN mix {dict(sorted(dn_hist.items()))}, "
          f"design {total_q/1e3:.0f} kW, {len(leaf_nodes)} leaf bypasses")

    # --- node coordinates: junction.geo, else incident-trench endpoint ---
    # (3 supply junctions ship geo=None: the plant K1289 + its two aux
    #  junctions, which merge into the plant node anyway)
    node_geo: dict[str, tuple[float, float]] = {}
    for root in trench_nodes:
        raw = j.at[root, "geo"]
        if raw is not None and not (isinstance(raw, float) and math.isnan(raw)):
            node_geo[str(j.at[root, "name"])] = geo_point(raw)
    for a, b, _l, geometry in trenches:
        for node, own, other in ((a, geometry[0], b), (b, geometry[-1], a)):
            if node in node_geo:
                continue
            # orientation guard: "our" endpoint must be the one farther from
            # the other node's known coordinate
            if other in node_geo:
                o = node_geo[other]
                d_own = (own[0] - o[0]) ** 2 + (own[1] - o[1]) ** 2
                d_opp = (geometry[-1 if own is geometry[0] else 0][0] - o[0]) ** 2 \
                    + (geometry[-1 if own is geometry[0] else 0][1] - o[1]) ** 2
                if d_own < d_opp:
                    own = geometry[-1 if own is geometry[0] else 0]
            node_geo[node] = (float(own[0]), float(own[1]))

    # --- five files ---
    junctions = []
    for root in trench_nodes:
        name = str(j.at[root, "name"])
        kind = ("plant" if root == rep[plant_j]
                else "consumer" if name in q_design_by_node else "node")
        lat, lon = node_geo[name]
        junctions.append({"name": name, "kind": kind,
                          "geo": [round(lat, 7), round(lon, 7)], "pn_bar": 6.0})

    docs = {
        "network_structure.json": {"name": "schutterwald",
                                   "junctions": junctions},
        "pipes.json": {"pipes": pipes},
        "consumers.json": {"resolution_minutes": RESOLUTION_MIN,
                           "steps": STEPS, "consumers": consumers},
        "producers.json": {"producers": [{
            "node": plant_node, "name": "Heizzentrale Schutterwald",
            "kind": "slack", "p_flow_bar": 6.0, "plift_bar": 3.0,
            "t_flow_k": 383.15,
            "heating_curve": {"preset": "3G"},
        }]},
        "weather.json": {"resolution_minutes": RESOLUTION_MIN, "steps": STEPS,
                         "t_amb_c": [round(float(x), 2) for x in t_amb],
                         "t_ground_c": [round(float(x), 2) for x in t_ground]},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fn, doc in docs.items():
        (OUT_DIR / fn).write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(f"wrote {OUT_DIR} — {len(junctions)} nodes, "
          f"{len(consumers)} consumer rows, weather mean {t_mean:.1f} °C")


if __name__ == "__main__":
    main()
