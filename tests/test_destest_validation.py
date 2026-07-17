"""DESTEST CE 0 / CE 1 validation of destest_16 against published results.

The published reference values are **recomputed at test time from the
vendored result files** (``data/sources/destest/results/``, modified BSD-3,
IBPSA) — six tools for the CE 0 steady state, three for the CE 1 week
(AixLib plug-flow, Buildings dynamic-pipe, IBPSA plug-flow; DIMOSIM is a
documented outlier in the DESTEST material and excluded).

Tolerance bands = published inter-tool min/max widened by *argued* model
differences (never tuned to pass — the margins are written down first,
the measured values are recorded in DATASET.md):

* **Fluid properties** (CE 0 mass flow / heat): DESTEST fixes water at 50 °C
  (rho 988, cp 4180); pandapipes water is temperature-dependent
  (cp 4179–4187 over the 40–70 °C loop). A cp shift of 0.4 % moves the
  ΔT-controlled mass flow and the enthalpy KPIs by the same 0.4 % →
  ±0.5 % margin on mass flow, ±1 % on heat (loss-model spread of the
  published tools is itself ~2 % of total heat).
* **Return temperature**: the published tools disagree by 0.39 K; fluid
  properties + junction mixing justify ±0.3 K beyond that envelope.
* **Quasi-static vs dynamic pipes** (CE 1): at near-zero night flow the
  published tools' pipes cool down; a quasi-static steady state keeps the
  pipe-entry region at supply temperature. The excess is bounded above by
  the enthalpy flux of the DESTEST minimum bypass flow (16 x 1.77 kg/h x
  cp x 60 K ~= 2.0 kW) over the observed 401 near-zero-demand ticks
  ⇒ <= 134 kWh on top of the published loss maximum. Injection carries the
  same excess (+<=1 %) plus the floor's standby demand (+0.46 %, see below).
* **Zero-flow floor**: the platform floors consumer demand at
  ``min_qext_w``; the suite runs with **62 W ⇔ 1.78 kg/h at ΔT 30 K** — the
  DESTEST minimum bypass expressed as heat. Effect on the week: +66 kWh
  demand (measured; 401 near-zero ticks x 16 buildings x ~62 W).

Engine grid: 144 steps/day (10-min ticks) — identical to the source data
resolution; quasi-static physics permits coarse ticks (SPEC §3.5).
Runtime ~40 s (1008 solves), well inside the suite budget.
"""
from __future__ import annotations

import csv

import pytest

from conftest import REPO_ROOT, make_settings

from rtheatflow.data_loader import load_network
from rtheatflow.simulator import Simulator

RESULTS = REPO_ROOT / "data" / "sources" / "destest" / "results"
NET_DIR = REPO_ROOT / "data" / "networks" / "destest_16"

CE0_TOOLS = [
    "Modelica_Buildings_AAU_Alessandro_Network_0.csv",
    "Modelica_Buildings_AAU_JL_Network_0.csv",
    "Modelica_Buildings_AAU_Martin_Network_0.csv",
    "SIM_VICUS_Network_0.csv",
    "TRNSYS_TUD_2021_Network_0.csv",
    "TRNSYS_TUD_2022_Network_0.csv",
]
CE1_TOOLS = [  # DIMOSIM excluded (documented outlier: 11.5 MWh injection)
    "AixLib_Plug_Flow_Network_1.csv",
    "Buildings_Library_Dynamic_Pipe_Network_1.csv",
    "IBPSA_Library_Plug_Flow_Network_1.csv",
]

CE0_PEAK_W = 19347.2793          # exact CE 0 substation load (case description)
FLOOR_W = 62.0                   # 1.78 kg/h at dT 30 K — DESTEST minimum bypass

# --- argued margins (see module docstring) ---
MDOT_MARGIN = 0.005              # fluid cp
HEAT_MARGIN = 0.01               # fluid cp + loss-model spread
TRET_MARGIN_K = 0.3              # fluid + mixing beyond the published envelope
LOSS_FLOOR_MARGIN = 0.03         # published min - 3 % (fluid properties)
LOSS_BYPASS_CEILING_KWH = 134.0  # quasi-static night excess, derived bound
INJ_MARGIN = 0.01                # quasi-static + floor, see docstring


def _rows(fname: str) -> list[list[float]]:
    with open(RESULTS / fname, newline="", encoding="utf-8-sig") as f:
        r = csv.reader(f)
        next(r)
        return [[float(x) for x in row] for row in r if row]


def _published_ce0() -> dict[str, tuple[float, float]]:
    """Column order is fixed across tools (parameters_DESTEST_Network_0.txt):
    1 = plant mass flow [kg/h], 11 = return temp at i [C], 18 = total heat [W].
    """
    mdot, tret, heat = [], [], []
    for f in CE0_TOOLS:
        row = _rows(f)[0]
        mdot.append(row[1])
        tret.append(row[11])
        heat.append(row[18])
    return {"mdot_kg_h": (min(mdot), max(mdot)),
            "t_return_c": (min(tret), max(tret)),
            "heat_w": (min(heat), max(heat))}


def _published_ce1() -> dict[str, tuple[float, float]]:
    """Trapezoid integrals over the exact 7-day series (900-s sampling)."""
    inj, loss = [], []
    for f in CE1_TOOLS:
        rows = _rows(f)
        dt = (rows[-1][0] - rows[0][0]) / (len(rows) - 1)
        assert dt == pytest.approx(900.0)
        inj.append(sum((a[1] + b[1]) / 2 for a, b in zip(rows, rows[1:]))
                   * dt / 3.6e9)     # MWh
        loss.append(sum((a[2] + b[2]) / 2 for a, b in zip(rows, rows[1:]))
                    * dt / 3.6e6)    # kWh
    return {"injection_mwh": (min(inj), max(inj)),
            "losses_kwh": (min(loss), max(loss))}


@pytest.fixture(scope="module")
def inputs():
    return load_network(NET_DIR)


@pytest.fixture()
def sim(inputs):
    return Simulator(inputs, make_settings(steps_per_day=144,
                                           min_qext_w=FLOOR_W))


def test_published_reference_bands_are_what_dataset_md_documents():
    """Pin the vendored reference data itself (guards silent file drift)."""
    ce0, ce1 = _published_ce0(), _published_ce1()
    assert ce0["mdot_kg_h"] == (pytest.approx(8847.94, abs=0.1),
                                pytest.approx(8870.4, abs=0.1))
    assert ce0["t_return_c"][0] == pytest.approx(39.46, abs=0.02)
    assert ce0["t_return_c"][1] == pytest.approx(39.85, abs=0.02)
    assert ce0["heat_w"] == (pytest.approx(308203, abs=10),
                             pytest.approx(314334, abs=10))
    assert ce1["injection_mwh"][0] == pytest.approx(14.324, abs=0.005)
    assert ce1["injection_mwh"][1] == pytest.approx(14.452, abs=0.005)
    assert ce1["losses_kwh"][0] == pytest.approx(535.2, abs=0.5)
    assert ce1["losses_kwh"][1] == pytest.approx(543.8, abs=0.5)


def test_ce0_steady_state(sim):
    """All 16 substations at exactly the CE 0 peak, ΔT 30 K, one solve.

    Measured 2026-07-17 (recorded in DATASET.md): mdot 8876.5 kg/h,
    t_return 39.48 °C, heat 314.8 kW, supply at SD1 69.454 °C — mdot/heat
    0.07 %/0.16 % above the published max (fluid-property difference),
    return temp and SD1 supply temp inside the published envelope.
    """
    pub = _published_ce0()
    sim.profiles.q_sh_w[:] = CE0_PEAK_W
    r = sim.run_step(0, 0)
    assert r.converged and r.solver_status == "ok"
    s = r.summary

    mdot_kg_h = s["mdot_plant_kg_per_s"] * 3600.0
    lo, hi = pub["mdot_kg_h"]
    assert lo * (1 - MDOT_MARGIN) <= mdot_kg_h <= hi * (1 + MDOT_MARGIN), \
        (mdot_kg_h, pub["mdot_kg_h"])

    lo, hi = pub["t_return_c"]
    assert lo - TRET_MARGIN_K <= s["t_return_plant_c"] <= hi + TRET_MARGIN_K, \
        (s["t_return_plant_c"], pub["t_return_c"])

    lo, hi = pub["heat_w"]
    assert lo / 1000 * (1 - HEAT_MARGIN) <= s["q_feed_kw"] \
        <= hi / 1000 * (1 + HEAT_MARGIN), (s["q_feed_kw"], pub["heat_w"])

    # loss physics probe: supply temperature at SD1 (published 69.43-69.48)
    i_sd1 = sim.profiles.index.consumer_names.index("SFH 1")
    t_sd1 = r.consumers[i_sd1]["t_supply_c"]
    assert 69.3 <= t_sd1 <= 69.6, t_sd1

    assert abs(s["balance_err_kw"]) <= 0.01 * s["q_feed_kw"]


def test_ce1_seven_day_quasi_static(sim):
    """The CE 1 week: 1008 solves on the 10-min grid, energy sums.

    Measured 2026-07-17 (recorded in DATASET.md): injection 14.506 MWh,
    losses 606.6 kWh, demand 13.905 MWh, all 1008 steps tier-1 converged.
    Loaded ticks (demand > 5 kW) contribute 534 kWh of loss — inside the
    published band; the excess sits entirely in the 401 near-zero-demand
    ticks (quasi-static night effect, see module docstring).
    """
    pub = _published_ce1()
    inj_kwh = loss_kwh = dem_kwh = loss_loaded_kwh = 0.0
    for day in range(7):
        for step in range(144):
            r = sim.run_step(step, day)
            assert r.converged and r.solver_status == "ok", (day, step)
            s = r.summary
            assert abs(s["balance_err_kw"]) <= 0.01 * max(s["q_feed_kw"], 1.0)
            inj_kwh += s["q_feed_kw"] / 6.0
            loss_kwh += s["q_loss_kw"] / 6.0
            dem_kwh += s["q_demand_kw"] / 6.0
            if s["q_demand_kw"] > 5.0:
                loss_loaded_kwh += s["q_loss_kw"] / 6.0

    # injection: published band +- 1 % (fluid + floor + quasi-static night)
    lo, hi = pub["injection_mwh"]
    assert lo * (1 - INJ_MARGIN) <= inj_kwh / 1000.0 <= hi * (1 + INJ_MARGIN), \
        (inj_kwh / 1000.0, pub["injection_mwh"])

    # losses: published min - 3 % ... published max + bypass ceiling
    lo, hi = pub["losses_kwh"]
    assert lo * (1 - LOSS_FLOOR_MARGIN) <= loss_kwh \
        <= hi + LOSS_BYPASS_CEILING_KWH, (loss_kwh, pub["losses_kwh"])

    # the strong claim: loss physics under load matches the published tools
    # (their totals INCLUDE night losses, so their band is an upper envelope
    # for our loaded-tick subtotal; lower bound = published min - 5 %)
    assert lo * 0.95 <= loss_loaded_kwh <= hi, (loss_loaded_kwh, pub)

    # demand = source profile x 16 (13.839 MWh) + the documented floor effect
    assert dem_kwh / 1000.0 == pytest.approx(13.905, abs=0.05)

    # regression pin on the measured totals (drift alarm, not a claim of
    # higher accuracy than the bands above)
    assert inj_kwh / 1000.0 == pytest.approx(14.506, rel=0.01)
    assert loss_kwh == pytest.approx(606.6, rel=0.02)
