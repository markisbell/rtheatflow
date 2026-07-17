"""Convert the IBPSA DESTEST CE_1 district-heating networks to the five-file contract.

Source data (vendored under ``data/sources/destest/``, modified BSD-3, IBPSA
Project 1 — see the DATASET.md files this script writes next to its output):

* ``Node data.csv`` / ``Node_data_8_buildings.csv`` / ``Node_data_32_buildings.csv``
  — node coordinates (LOCAL Cartesian metres, no CRS) + substation peak power.
* ``Pipe_data_16_case_description_table6.csv`` — the 16-building pipe table
  transcribed from the official case-description document (Table 6). This is
  what every published CE 0 / CE 1 result models; the repo's ``Pipe_data.csv``
  is a later re-dimensioning that cannot reproduce the published pressure or
  loss KPIs (see DATASET.md "Which pipe table?" for the evidence).
* ``Pipe_data_8_buildings.csv`` / ``Pipe_data_32_buildings.csv`` — the repo
  variants (no published aggregate results exist for these).
* ``heat_profile_1_building_SFH_Network_1.csv`` — one SFH heat demand profile,
  600-s steps, identical for every building; the converter cuts the first
  7 days (the CE 1 window, starting 2018-01-01).

Physics per the case-description document (verified against published results):

* Pipe wall: PE-X, lambda = 0.35 W/(m K), ISO 15875-2 series S5 (SDR 11).
* Insulation: lambda = 0.026 W/(m K); thickness per pipe from the table.
* Boundary: constant 10 degC at the OUTER insulation surface (no soil model),
  so U' per metre is the pure cylindrical wall+insulation series resistance:
      R' = ln(OD/ID)/(2 pi l_pex) + ln((OD/2 + t_ins)/(OD/2))/(2 pi l_ins)
* pandapipes' per-area convention: for ``create_pipe_from_parameters`` without
  ``outer_diameter_mm`` the heat-transfer surface is pi * d_inner * L
  (DO falls back to the inner diameter), hence
      u_w_per_m2k = U' / (pi * d_inner).
* Roughness k = 0.007 mm (PE-X, Table 4).
* The source CSVs' "U-value [W/mK]" column (uniformly 0.035) is a design-tool
  constant hard-coded by the upstream ``Pipedimensioning.py``; it matches
  neither the material tables nor any published loss KPI and is IGNORED.
  The empty/partial "Total pressure loss" columns are design output — ignored.

Geodata: DESTEST coordinates are local metres without a CRS. For the Leaflet
map the converter projects them onto a synthetic WGS84 anchor on OPEN LAND
(fields north-east of the demo-network region) — the DESTEST grid is an
abstract benchmark, so the placement is arbitrary, but it must not sit in
water on the map.

Usage:  python scripts/convert_destest.py
Writes: data/networks/destest_16 | destest_8 | destest_32 (five files each).
Deterministic; catalog entries in data/network_library.json are maintained
by hand (repo convention).
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "data" / "sources" / "destest"
OUT = REPO / "data" / "networks"

KELVIN = 273.15

# --- case-description constants (Tables 4/5/7 + network sections) ---
LAMBDA_PEX = 0.35        # W/(m K) pipe wall
LAMBDA_INS = 0.026       # W/(m K) insulation
K_MM = 0.007             # PE-X internal roughness
SDR = 11.0               # ISO 15875-2 series S5: wall = OD/11 -> OD = ID*11/9
T_SUPPLY_K = 70.0 + KELVIN
T_BOUNDARY_C = 10.0      # constant at outer insulation surface, no soil
DELTAT_K = 30.0          # substation primary-side temperature difference
PROFILE_STEP_S = 600
WINDOW_DAYS = 7          # CE 1 window (starts 2018-01-01)

# Synthetic map anchor on open land (fields NE of the demo-network region,
# near Karlsruhe) — the DESTEST grid is an abstract benchmark with
# local-metre coordinates and no CRS; placement is arbitrary but on land.
ANCHOR_LAT = 49.0850
ANCHOR_LON = 8.4300
M_PER_DEG = 111_320.0


def local_to_wgs84(x_m: float, y_m: float) -> tuple[float, float]:
    lat = ANCHOR_LAT + y_m / M_PER_DEG
    lon = ANCHOR_LON + x_m / (M_PER_DEG * math.cos(math.radians(ANCHOR_LAT)))
    return round(lat, 7), round(lon, 7)


def u_per_metre(id_m: float, od_m: float, t_ins_m: float) -> float:
    """Per-metre heat-loss coefficient U' [W/(m K)] of wall + insulation."""
    r_wall = math.log(od_m / id_m) / (2 * math.pi * LAMBDA_PEX)
    r_ins = math.log((od_m / 2 + t_ins_m) / (od_m / 2)) / (2 * math.pi * LAMBDA_INS)
    return 1.0 / (r_wall + r_ins)


def u_w_per_m2k(id_m: float, od_m: float, t_ins_m: float) -> float:
    """pandapipes per-area U on the inner-diameter surface (no outer given)."""
    return u_per_metre(id_m, od_m, t_ins_m) / (math.pi * id_m)


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_profile_window() -> list[float]:
    rows = read_csv(SRC / "heat_profile_1_building_SFH_Network_1.csv")
    n = WINDOW_DAYS * 24 * 3600 // PROFILE_STEP_S
    vals = [round(float(r["Building heat demand [W]"]), 2) for r in rows[:n]]
    if len(vals) != n:
        raise SystemExit(f"profile too short: {len(vals)} < {n}")
    return vals


def convert(net_id: str, node_file: str, pipe_rows: list[dict], name: str) -> None:
    nodes = read_csv(SRC / node_file)
    profile = read_profile_window()
    steps = len(profile)

    node_names = [r["Node"] for r in nodes]
    coords = {r["Node"]: (float(r["X-Position [m]"]), float(r["Y-Position [m]"]))
              for r in nodes}
    peak_w = {r["Node"]: round(float(r["Peak power [kW]"]) * 1000.0, 2)
              for r in nodes if r["Node"].startswith("SimpleDistrict")}

    junctions = []
    for n in node_names:
        lat, lon = local_to_wgs84(*coords[n])
        kind = ("plant" if n == "i"
                else "consumer" if n.startswith("SimpleDistrict") else "node")
        junctions.append({"name": n, "kind": kind, "geo": [lat, lon], "pn_bar": 6.0})

    pipes = []
    trench_m = 0.0
    for r in pipe_rows:
        id_m = float(r["Inner Diameter [m]"])
        od_m = float(r["OD_m"]) if "OD_m" in r else id_m * SDR / (SDR - 2.0)
        t_ins = float(r["Insulation Thickness [m]"])
        length_m = float(r["Length [m]"])
        trench_m += length_m
        a, b = r["Beginning Node"], r["Ending Node"]
        pipes.append({
            "from_node": a, "to_node": b,
            "length_km": round(length_m / 1000.0, 6),
            "inner_diameter_mm": round(id_m * 1000.0, 3),
            "u_w_per_m2k": round(u_w_per_m2k(id_m, od_m, t_ins), 5),
            "k_mm": K_MM,
            "geometry": [list(local_to_wgs84(*coords[a])),
                         list(local_to_wgs84(*coords[b]))],
        })

    consumers = [{
        "node": n,
        "name": n.replace("SimpleDistrict_", "SFH "),
        "q_sh_w": profile,
        "q_dhw_w": [0.0] * steps,
        "deltat_k": DELTAT_K,
        "q_design_w": peak_w[n],
    } for n in node_names if n.startswith("SimpleDistrict")]

    producers = [{
        "node": "i", "name": "Ideale Quelle (70 °C konstant)", "kind": "slack",
        "p_flow_bar": 6.0, "plift_bar": 1.0, "t_flow_k": T_SUPPLY_K,
    }]

    weather = {
        "resolution_minutes": PROFILE_STEP_S // 60,
        "steps": steps,
        "t_amb_c": [T_BOUNDARY_C] * steps,
        "t_ground_c": [T_BOUNDARY_C] * steps,
    }

    out = OUT / net_id
    out.mkdir(parents=True, exist_ok=True)
    docs = {
        "network_structure.json": {"name": name, "junctions": junctions},
        "pipes.json": {"pipes": pipes},
        "consumers.json": {"resolution_minutes": PROFILE_STEP_S // 60,
                           "steps": steps, "consumers": consumers},
        "producers.json": {"producers": producers},
        "weather.json": weather,
    }
    for fn, doc in docs.items():
        (out / fn).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(f"{net_id}: {len(junctions)} nodes, {len(pipes)} trenches "
          f"({trench_m:.0f} m), {len(consumers)} consumers, {steps} steps")


def pipes_16() -> list[dict]:
    """Case-description Table 6 (the published-results dimensioning)."""
    rows = read_csv(SRC / "Pipe_data_16_case_description_table6.csv")
    for r in rows:
        od_mm = float(r["Pipe size OD x wall [mm]"].split("x")[0])
        r["OD_m"] = str(od_mm / 1000.0)
    return rows


def pipes_repo(fname: str) -> list[dict]:
    """Repo pipe CSVs (8/32 variants): S5 wall assumed (OD = ID*11/9)."""
    return read_csv(SRC / fname)


def main() -> None:
    convert("destest_16", "Node data.csv", pipes_16(),
            "DESTEST CE1 (16 Gebäude)")
    convert("destest_8", "Node_data_8_buildings.csv",
            pipes_repo("Pipe_data_8_buildings.csv"),
            "DESTEST (8 Gebäude)")
    convert("destest_32", "Node_data_32_buildings.csv",
            pipes_repo("Pipe_data_32_buildings.csv"),
            "DESTEST (32 Gebäude)")


if __name__ == "__main__":
    main()
