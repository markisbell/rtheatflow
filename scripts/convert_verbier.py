"""Convert the OpenDHN Verbier dataset into ``data/networks/verbier``.

Real measured **meshed** district-heating network (Verbier, Swiss Alps):
2 heating plants (HS0/HS1) + 150 substations, with steady-state sensor
measurements (CASE1) for every plant and substation — the platform's
measured-data validation case and meshed-solver stress test.

Source: Boghetti & Kämpf (Idiap/EPFL), "A benchmark for the simulation of
meshed district heating networks based on anonymised monitoring data",
CISBAT 2023. Zenodo DOI 10.5281/zenodo.10793816, **CC BY 4.0** — vendored
verbatim under ``data/sources/verbier/`` (zip + extracted CSVs). The AGPL
pydhn code base was deliberately NOT consulted; everything here derives
from the Zenodo data + README alone.

Conversion (verified on the data, see DATASET.md):

* Supply/return sides are a perfect mirror (identical base-node sets,
  identical pipe lengths AND diameters per pair) → the single-sided trench
  contract is lossless. 676 trench nodes, 681 trenches, 6 independent
  loops (meshed), no zero-length pipes, no consumer-less leaves.
* Per-pipe U from the layered description: insulation annulus only
  (``ln((r1+t_ins)/r1)/(2*pi*lambda_ins)`` with ``r1 = d_int/2 + t_int``);
  steel wall and casing resistances are <1 % and neglected (documented).
  pandapipes per-area conversion on the inner diameter. ``k_mm`` from the
  ``roughness`` column (mm).
* Consumers: measured power (CASE1) as fixed loads + measured return
  temperatures (``qext_w`` + ``treturn_k`` pair), constant over one
  96 x 15-min day. Substation supply temperatures stay UNUSED as inputs —
  they are the validation target.
* HS0 = the pressure slack (measured supply temp 81.11 degC). HS1 = the
  second plant: ``pump_mass`` with measured mdot (5.03 kg/s) and supply
  temp (81.81 degC), **without p_flow_bar** -> the pressure-free
  ``type="t"`` variant per the single-slack rule.
* Coordinates are anonymised local metres (z uniformly 0) — projected onto
  WGS84 with the network CENTROID over Verbier village. The placement is
  synthetic (the source is anonymised; real routing is unknown), but an
  alpine network belongs on alpine terrain, not in a lake.
* Boundary temperature: not part of the dataset; constant 5 degC ground
  (alpine heating season) for all pipes — 55 aerial pipes see the same
  boundary (single-``text_k`` platform convention; documented).

Usage:  python scripts/convert_verbier.py
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "data" / "sources" / "verbier"
OUT = REPO / "data" / "networks" / "verbier"

STEPS = 96
RESOLUTION_MIN = 15
T_GROUND_C = 5.0            # alpine heating season (assumption, documented)
T_AMB_C = 0.0
# Map placement: network centroid over Verbier village (46.0961 N, 7.2286 E).
# Synthetic — source coordinates are anonymised local metres; see docstring.
CENTER_LAT = 46.0961
CENTER_LON = 7.2286
M_PER_DEG = 111_320.0

# filled by main() from the node extents (centroid-anchored projection)
_X_MID = 0.0
_Y_MID = 0.0


def set_projection_center(xs: list[float], ys: list[float]) -> None:
    global _X_MID, _Y_MID
    _X_MID = (min(xs) + max(xs)) / 2.0
    _Y_MID = (min(ys) + max(ys)) / 2.0


def to_wgs84(x: float, y: float) -> list[float]:
    lat = CENTER_LAT + (y - _Y_MID) / M_PER_DEG
    lon = CENTER_LON + (x - _X_MID) / (M_PER_DEG * math.cos(math.radians(CENTER_LAT)))
    return [round(lat, 7), round(lon, 7)]


def read(name: str) -> list[dict]:
    with open(SRC / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_case1(name: str) -> dict[str, float]:
    with open(SRC / "data" / name, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, case1 = rows[0], rows[1]
    assert case1[0] == "CASE1"
    return {h: float(v) for h, v in zip(header[1:], case1[1:])}


def u_w_per_m2k(d_int: float, t_int: float, t_ins: float, lambda_ins: float) -> float:
    r1 = d_int / 2.0 + t_int
    r_ins = math.log((r1 + t_ins) / r1) / (2.0 * math.pi * lambda_ins)
    return (1.0 / r_ins) / (math.pi * d_int)


def main() -> None:
    nodes = read("network/nodes.csv")
    pipes = read("network/pipes.csv")
    stations = read("network/heating_stations.csv")
    substations = read("network/substations.csv")
    power = read_case1("power.csv")
    t_return = read_case1("return_temperature.csv")
    t_supply = read_case1("supply_temperature.csv")
    mass_flow = read_case1("mass_flow.csv")

    sup_nodes = [n for n in nodes if n["is_supply"] == "True"]
    coords = {n["node_id"][:-1]: (float(n["x"]), float(n["y"])) for n in sup_nodes}
    set_projection_center([c[0] for c in coords.values()],
                          [c[1] for c in coords.values()])

    cons_base = {s["inlet_node"][:-1]: s["sub_id"] for s in substations}
    hs = {h["hs_id"]: h["outlet_node"][:-1] for h in stations}  # outlet = supply
    plant_base, second_base = hs["HS0"], hs["HS1"]

    junctions = []
    for base, (x, y) in sorted(coords.items(), key=lambda kv: int(kv[0][1:])):
        kind = ("plant" if base == plant_base
                else "consumer" if base in cons_base else "node")
        junctions.append({"name": base, "kind": kind,
                          "geo": to_wgs84(x, y), "pn_bar": 10.0})

    trenches = []
    n_aerial = 0
    for p in pipes:
        if p["is_supply"] != "True":
            continue
        a, b = p["inlet_node"][:-1], p["outlet_node"][:-1]
        d_int = float(p["d_int"])
        n_aerial += p["is_aerial"] == "True"
        trenches.append({
            "from_node": a, "to_node": b,
            "length_km": round(float(p["length"]) / 1000.0, 6),
            "inner_diameter_mm": round(d_int * 1000.0, 3),
            "u_w_per_m2k": round(u_w_per_m2k(
                d_int, float(p["t_int"]), float(p["t_ins"]),
                float(p["lambda_ins"])), 5),
            "k_mm": float(p["roughness"]),
            "geometry": [to_wgs84(*coords[a]), to_wgs84(*coords[b])],
        })

    consumers = []
    for s in substations:
        sid = s["sub_id"]
        base = s["inlet_node"][:-1]
        q = power[sid]
        consumers.append({
            "node": base,
            "name": sid,
            "q_sh_w": [round(q, 1)] * STEPS,
            "q_dhw_w": [0.0] * STEPS,
            "treturn_k": [round(273.15 + t_return[sid], 2)] * STEPS,
            "q_design_w": round(max(q, 300.0), 1),
            "t_supply_min_c": 60.0,
        })

    producers = [
        {"node": plant_base, "name": "HS0 (Slack, gemessen 81.1 °C)",
         "kind": "slack", "p_flow_bar": 10.0, "plift_bar": 5.0,
         "t_flow_k": round(273.15 + t_supply["HS0"], 2)},
        {"node": second_base, "name": "HS1 (Pumpe, gemessen 5.03 kg/s)",
         "kind": "pump_mass",
         "mdot_flow_kg_per_s": round(mass_flow["HS1"], 5),
         "t_flow_k": round(273.15 + t_supply["HS1"], 2)},
    ]

    docs = {
        "network_structure.json": {"name": "verbier", "junctions": junctions},
        "pipes.json": {"pipes": trenches},
        "consumers.json": {"resolution_minutes": RESOLUTION_MIN,
                           "steps": STEPS, "consumers": consumers},
        "producers.json": {"producers": producers},
        "weather.json": {"resolution_minutes": RESOLUTION_MIN, "steps": STEPS,
                         "t_amb_c": [T_AMB_C] * STEPS,
                         "t_ground_c": [T_GROUND_C] * STEPS},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for fn, doc in docs.items():
        (OUT / fn).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    total_km = sum(t["length_km"] for t in trenches)
    print(f"verbier: {len(junctions)} trench nodes, {len(trenches)} trenches "
          f"({total_km:.2f} km, {n_aerial} aerial), {len(consumers)} substations, "
          f"plants HS0@{plant_base} slack + HS1@{second_base} pump_mass(t), "
          f"demand {sum(power[s['sub_id']] for s in substations)/1e6:.2f} MW")


if __name__ == "__main__":
    main()
