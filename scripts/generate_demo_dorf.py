"""Generate ``data/networks/demo_dorf`` — the M3 demo village (SPEC §12 M3).

A hand-crafted rural village network on real WGS84 geography (~49.004 N,
8.397 E, north of Karlsruhe): one 3G heating plant, 11 consumers (6 EFH
Bestand, 3 EFH saniert, 2 MFH) along a main street with two side lanes.
Unlike the ``appendix_a`` known-answer fixture (abstract coordinates), this
network exists so the Leaflet map has something real to draw.

Design decisions (all deterministic, seeded):

* **Demands** come from the committed archetype cache ``data/profiles/``
  (demandlib VDI 4655 + OpenDHW, 15-min): one representative cold winter
  day is cut out of the year (the 90th-percentile space-heating day — cold
  but not the extreme), per-consumer DHW variants rotate, and a seeded
  time-shift + amplitude jitter desynchronizes identical buildings
  (SPEC §4.5). Staircase to the 1-min tick happens in the builder.
* **Weather** is derived from the chosen day's space-heating level by
  inverting the §4.5 degree-hour factor, so the live override math is
  self-consistent, with a small diurnal sinusoid on top.
* **Pipe DNs** are sized from aggregated downstream design loads at
  ΔT = 30 K to ≤ ~1 m/s (SPEC Appendix B item 14: size from design flows),
  using the ISOPLUS pre-insulated std types shipped with pandapipes 0.14.
* **Return temperatures** differ by archetype (radiators return hotter):
  EFH Bestand 55 °C, MFH 50 °C, EFH saniert 45 °C — the return-temperature
  map layer has something to show.

Run from the repo root:  python scripts/generate_demo_dorf.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO / "data" / "profiles"
OUT_DIR = REPO / "data" / "networks" / "demo_dorf"

SEED = 20260716
STEPS = 96                  # one day at 15-min resolution
RESOLUTION_MIN = 15
DT_DESIGN_K = 30.0          # design spread for pipe sizing (85/55 network)
CP = 4187.0                 # J/(kg K), sizing only
RHO = 970.0                 # kg/m3 at ~80 °C, sizing only
V_MAX = 1.0                 # m/s sizing target (SPEC: ~1 m/s)
T_RETURN_C = {"EFH_ALT_4P": 55.0, "EFH_SAN_4P": 45.0, "MFH_ALT_10WE": 50.0}

# --- heating curve (3G village: 85/55 sliding) ------------------------------
CURVE = {
    "t_amb_design_c": -12.0,
    "t_flow_design_c": 85.0,
    "t_flow_min_c": 70.0,
    "t_room_c": 20.0,
    "n": 1.3,
}

# ---------------------------------------------------------------------------
# Layout: (name, kind, lat, lon[, archetype]) — trench nodes.
# Main street (Hauptstrasse) west→east, Kirchweg north, Gartenweg south.
# ---------------------------------------------------------------------------
NODES = [
    ("plant", "plant", 49.00320, 8.39560, None),
    ("t1",    "node",  49.00340, 8.39720, None),
    ("c10",   "consumer", 49.00290, 8.39735, "EFH_ALT_4P"),
    ("c1",    "consumer", 49.00352, 8.39800, "EFH_ALT_4P"),
    ("c11",   "consumer", 49.00358, 8.39840, "EFH_ALT_4P"),
    ("t2",    "node",  49.00360, 8.39880, None),
    ("c2",    "consumer", 49.00420, 8.39890, "MFH_ALT_10WE"),
    ("c6",    "consumer", 49.00480, 8.39905, "EFH_SAN_4P"),
    ("c7",    "consumer", 49.00540, 8.39925, "MFH_ALT_10WE"),
    ("c3",    "consumer", 49.00350, 8.39960, "EFH_SAN_4P"),
    ("t3",    "node",  49.00335, 8.40040, None),
    ("c8",    "consumer", 49.00270, 8.40025, "EFH_SAN_4P"),
    ("c9",    "consumer", 49.00205, 8.40010, "EFH_ALT_4P"),
    ("c4",    "consumer", 49.00345, 8.40120, "EFH_ALT_4P"),
    ("c5",    "consumer", 49.00350, 8.40200, "EFH_ALT_4P"),
]

# Trenches: (from, to, [intermediate geometry points (lat, lon)]) — the
# street-like bends; endpoints are appended automatically.
TRENCHES = [
    ("plant", "t1", [(49.00330, 8.39640)]),
    ("t1", "c10", []),
    ("t1", "c1", [(49.00348, 8.39760)]),
    ("c1", "c11", []),
    ("c11", "t2", []),
    ("t2", "c2", []),
    ("c2", "c6", []),
    ("c6", "c7", [(49.00510, 8.39915)]),
    ("t2", "c3", [(49.00356, 8.39920)]),
    ("c3", "t3", [(49.00342, 8.40000)]),
    ("t3", "c8", [(49.00300, 8.40035)]),
    ("c8", "c9", [(49.00235, 8.40020)]),
    ("t3", "c4", [(49.00340, 8.40080)]),
    ("c4", "c5", [(49.00348, 8.40160)]),
]

STREET_FACTOR = 1.08  # trench length vs. straight polyline (bends, house leads)


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def polyline_km(pts: list[tuple[float, float]]) -> float:
    m = sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    return max(0.02, m * STREET_FACTOR / 1000.0)


def isoplus_std_types() -> dict[str, float]:
    """ISOPLUS_DRE*_STD name -> inner diameter [mm] from pandapipes 0.14."""
    import pandapipes as pp
    net = pp.create_empty_network(fluid="water")
    types = net.std_types["pipe"]
    out = {}
    for name, params in types.items():
        if name.startswith("ISOPLUS_DRE") and name.endswith("_STD"):
            out[name] = float(params["inner_diameter_mm"])
    if not out:
        raise RuntimeError("no ISOPLUS std types found in pandapipes library")
    return out


def pick_std_type(mdot_design: float, lib: dict[str, float]) -> str:
    """Smallest ISOPLUS STD type with v <= V_MAX at the design mass flow."""
    best_name, best_d = None, None
    for name, d_mm in sorted(lib.items(), key=lambda kv: kv[1]):
        area = math.pi * (d_mm / 1000.0) ** 2 / 4.0
        v = mdot_design / (RHO * area)
        if v <= V_MAX:
            return name
        best_name, best_d = name, d_mm  # largest so far
    return best_name  # fall back to the largest available


def pick_demo_day(q_sh_year: np.ndarray) -> int:
    """90th-percentile space-heating day: properly cold, not the extreme."""
    days = q_sh_year[: 365 * STEPS].reshape(-1, STEPS).sum(axis=1)
    order = np.argsort(days)
    return int(order[int(0.90 * (len(order) - 1))])


def main() -> None:
    rng = np.random.default_rng(SEED)
    index = json.loads((PROFILES_DIR / "index.json").read_text("utf-8"))
    arch = {
        a["id"]: json.loads((PROFILES_DIR / a["file"]).read_text("utf-8"))
        for a in index["archetypes"]
    }

    # --- demo day (from the biggest archetype's SH shape) --------------------
    day = pick_demo_day(np.asarray(arch["EFH_ALT_4P"]["q_sh_w"], dtype=float))
    sl = slice(day * STEPS, (day + 1) * STEPS)
    print(f"demo day: {day} (of the archetype year)")

    # --- consumers ------------------------------------------------------------
    consumers = []
    node_geo = {n[0]: (n[2], n[3]) for n in NODES}
    q_design_by_node: dict[str, float] = {}
    dhw_counter: dict[str, int] = {}
    for name, kind, _lat, _lon, arch_id in NODES:
        if kind != "consumer":
            continue
        a = arch[arch_id]
        amp = float(rng.uniform(0.92, 1.08))
        shift = int(rng.integers(-2, 3))  # +-30 min desynchronization
        variant = dhw_counter.get(arch_id, 0) % len(a["q_dhw_w_variants"])
        dhw_counter[arch_id] = variant + 1

        q_sh = np.roll(np.asarray(a["q_sh_w"], dtype=float)[sl], shift) * amp
        q_dhw = np.roll(
            np.asarray(a["q_dhw_w_variants"][variant], dtype=float)[sl], shift)
        q_design = float(a["q_design_w"]) * amp
        q_design_by_node[name] = q_design
        treturn_k = [round(273.15 + T_RETURN_C[arch_id], 2)] * STEPS

        consumers.append({
            "node": name,
            "name": name.upper() + " " + a["house_type"],
            "q_sh_w": [round(float(x), 1) for x in q_sh],
            "q_dhw_w": [round(float(x), 1) for x in q_dhw],
            "treturn_k": treturn_k,
            "annual_kwh": round(
                (a["annual_kwh"]["space_heating"]
                 + a["annual_kwh"]["dhw_mean"]) * amp, 1),
            "q_design_w": round(q_design, 1),
            "t_supply_min_c": 60.0,
            "building": arch_id,
        })

    # --- weather: invert the degree-hour factor from the day's SH level ------
    # f = (t_room - T) / (t_room - t_design); day-mean f from the profile
    q_sh_day = np.asarray(arch["EFH_ALT_4P"]["q_sh_w"], dtype=float)[sl]
    f_mean = float(q_sh_day.mean() / arch["EFH_ALT_4P"]["q_design_w"])
    t_room, t_design = CURVE["t_room_c"], CURVE["t_amb_design_c"]
    t_mean = t_room - f_mean * (t_room - t_design)
    hours = np.arange(STEPS) * RESOLUTION_MIN / 60.0
    t_amb = t_mean - 2.5 * np.cos((hours - 14.0) / 24.0 * 2 * np.pi)
    t_ground = 6.5 + 0.2 * np.sin((hours - 15.0) / 24.0 * 2 * np.pi)
    print(f"weather: mean {t_mean:.1f} degC "
          f"({t_amb.min():.1f} .. {t_amb.max():.1f})")

    # --- pipes: aggregate downstream design load, size DNs -------------------
    children: dict[str, list[str]] = {}
    for f, t, _g in TRENCHES:
        children.setdefault(f, []).append(t)

    def downstream_q(node: str) -> float:
        return (q_design_by_node.get(node, 0.0)
                + sum(downstream_q(c) for c in children.get(node, [])))

    lib = isoplus_std_types()
    pipes = []
    for f, t, mids in TRENCHES:
        geometry = [list(node_geo[f])] + [list(p) for p in mids] + [list(node_geo[t])]
        q_w = downstream_q(t)
        mdot = q_w / (CP * DT_DESIGN_K)
        std = pick_std_type(mdot, lib)
        pipes.append({
            "from_node": f,
            "to_node": t,
            "std_type": std,
            "length_km": round(polyline_km([tuple(p) for p in geometry]), 4),
            "sections": 3,
            "geometry": geometry,
        })
        d = lib[std]
        v = mdot / (RHO * math.pi * (d / 1000.0) ** 2 / 4.0)
        print(f"  {f:>5} -> {t:<5} {q_w/1000.0:7.1f} kW  {mdot:5.2f} kg/s"
              f"  {std:<22} v_design {v:4.2f} m/s")

    # --- five files -----------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    structure = {
        "name": "demo_dorf",
        "junctions": [
            {"name": n, "kind": k, "geo": [lat, lon], "pn_bar": 6}
            for n, k, lat, lon, _a in NODES
        ],
    }
    producers = {
        "producers": [{
            "node": "plant",
            "name": "Heizzentrale",
            "kind": "slack",
            "p_flow_bar": 6.0,
            "plift_bar": 2.5,
            "t_flow_k": 273.15 + CURVE["t_flow_design_c"],
            "heating_curve": CURVE,
        }],
    }
    weather = {
        "resolution_minutes": RESOLUTION_MIN,
        "steps": STEPS,
        "t_amb_c": [round(float(x), 2) for x in t_amb],
        "t_ground_c": [round(float(x), 2) for x in t_ground],
    }
    consumers_file = {
        "resolution_minutes": RESOLUTION_MIN,
        "steps": STEPS,
        "consumers": consumers,
    }
    files = {
        "network_structure.json": structure,
        "pipes.json": {"pipes": pipes},
        "consumers.json": consumers_file,
        "producers.json": producers,
        "weather.json": weather,
    }
    for fname, doc in files.items():
        (OUT_DIR / fname).write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
        print(f"wrote {OUT_DIR / fname}")

    total_q = sum(q_design_by_node.values())
    total_len = sum(p["length_km"] for p in pipes)
    print(f"village: {len(consumers)} consumers, {total_q/1000.0:.1f} kW design, "
          f"{total_len:.2f} km trench")


if __name__ == "__main__":
    main()
