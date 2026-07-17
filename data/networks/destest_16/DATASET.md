# destest_16 — IBPSA DESTEST CE1, 16 buildings (validated reference network)

## Provenance

- **Source:** IBPSA Project 1 "DESTEST" common exercises, network CE_1 —
  <https://github.com/ibpsa/project1-destest> (fetched 2026-07-17) and the
  official case-description document linked from `Networks/CE_1/README.md`
  (Google Doc `1DJnhsGFzkdIKSy9IZ3BrnCXD3Ko-4JHfkC39-XdT9r8`).
- **License:** modified BSD-3 (IBPSA), vendored verbatim at
  [`data/sources/destest/license.md`](../../sources/destest/license.md).
  Attribution: IBPSA Project 1 — DESTEST (Design and Simulation Exercises for
  Simulation Tool Evaluation), <https://ibpsa.github.io/project1/>.
- **Vendored inputs** (`data/sources/destest/`): `Node data.csv` (coordinates +
  substation peak power), `Pipe_data_16_case_description_table6.csv`
  (transcription of case-description Table 6 — see below),
  `heat_profile_1_building_SFH_Network_1.csv` (demand), `license.md`.
- **Converter:** `scripts/convert_destest.py` (deterministic, offline).

## Which pipe table? (documented source discrepancy)

The repo file `Pipe_data.csv` and the case-description **Table 6 disagree**
(different inner diameters, insulation thicknesses, and main-line lengths —
e.g. SD1–e: ID 0.025 m / ins 42.5 mm in the CSV vs 25×2.3 (ID 0.0204 m) /
ins 34 mm in the doc; h–i: 36 m vs 26.83 m). Every published CE 0 / CE 1
result models the **Table 6** network:

- Published CE 0 pressure drop i→e is 22.4–25.4 kPa; Table 6 dimensioning
  computes to ~19–21 kPa (Colebrook estimate), the CSV dimensioning to ~7 kPa.
- Published CE 0 supply-line loss i–h is 314–326 W (4 of 6 tools); the layered
  U′ of Table 6's 50×4.6 + 31 mm insulation gives 0.199 W/(m K) × 26.83 m ×
  60 K ≈ 320 W. The CSV's dimensions cannot reproduce this.

This network is therefore built from the vendored **Table 6 transcription**;
the repo `Pipe_data.csv` is kept in `data/sources/destest/` for the record but
not used. (The 8/32-building variants have no published results and use the
repo CSVs — see their DATASET.md.)

## Pipe heat-loss model

The CSVs' "U-value [W/mK]" column (uniformly 0.035) is **ignored**: upstream
`Pipedimensioning.py` hard-codes it, it equals neither insulation conductivity
(0.026 W/(m K), Table 5) nor any per-metre U consistent with published losses
(a per-metre U of 0.035 would cap total losses at ~1.7 kW; published CE 1
losses average 3.2 kW). Instead, per-pipe U′ follows the case description:

- PE-X wall λ = 0.35 W/(m K) (Table 4), geometry from Table 6's "OD × wall"
  (ISO 15875-2 series S5, SDR 11); insulation λ = 0.026 W/(m K) (Table 5),
  thickness per pipe; boundary = constant 10 °C at the **outer insulation
  surface**, no soil resistance (case rule) →
  `U′ = 1 / (ln(OD/ID)/(2πλ_PEX) + ln((OD/2+t_ins)/(OD/2))/(2πλ_ins))`
- Resulting U′ [W/(m K)]: 25×2.3+34 → 0.123 · 32×2.9+30 → 0.152 ·
  40×3.7+26 → 0.193 · 50×4.6+31 → 0.199.
- pandapipes conversion: without `outer_diameter_mm` the solver's
  heat-transfer surface is π·ID·L, so `u_w_per_m2k = U′/(π·ID)` reproduces
  the per-metre value exactly (verified against pandapipes 0.14.0 source:
  `branch_w_internals_models.py` DO-fallback + `derivative_toolbox.py`).
- Roughness k = 0.007 mm (PE-X, Table 4). "Total pressure loss [Pa/m]"
  columns are upstream design output — ignored.

## Case rules encoded

- Supply **constant 70 °C** at source `i`: slack without `heating_curve`
  (the platform then never touches `t_flow_k`). Pressure side (6 bar,
  plift 1 bar) is ours — DESTEST prescribes no pump model.
- Substations hold primary **ΔT = 30 K** → `heat_consumer(qext_w, deltat_k=30)`
  (pandapipes pair 4; exact match to the case).
- **Bypass minimum 1.77 kg/h** per substation at zero demand: with the
  ΔT-pair, the platform's zero-flow floor `RTHEATFLOW_MIN_QEXT_W` acts as the
  bypass — at ΔT = 30 K, 62 W ⇔ 1.78 kg/h (= the DESTEST minimum). The
  validation suite runs with `min_qext_w = 62`; the app default (500 W ⇔
  14.4 kg/h) is a deliberately generous solver guard and adds ~0.5 kW
  standby demand per idle tick network-wide.
- Boundary 10 °C: `weather.json` carries constant 10 °C in both channels
  (`t_ground_c` drives every pipe's `text_k = 283.15 K`; `t_amb_c` is inert
  here — no heating curve, demand comes from the file profiles).
- Demand: `heat_profile_1_building_SFH_Network_1.csv`, 600-s steps,
  **identical for all 16 buildings** (source convention), first 7 days
  (CE 1 window starting 2018-01-01), max 19 347.28 W. The full source file
  covers ~256 days — not a year; `annual_kwh` is therefore omitted.
- Return network mirrors the supply topology (standard DESTEST assumption;
  the builder's supply/return pair expansion does exactly this), identical
  insulation, no supply↔return heat exchange.

## Geodata (synthetic — read this)

DESTEST coordinates are **local Cartesian metres without a CRS**. For the map
the converter anchors them at **49.085 N / 8.43 E — open fields north-east of
the demo-network region** (arbitrary but on land; the earlier lake anchor
rendered confusingly in open water). The DESTEST grid is an abstract
benchmark: the placement is synthetic and only relative geometry is
meaningful.

## Accepted modelling differences vs the published tools

1. **Fluid:** DESTEST fixes water at 50 °C (ρ 988, cp 4180, ν 5.5e-7, λ 0.64);
   rtheatflow uses pandapipes' temperature-dependent water. Effect: ±0.3–0.5 %
   on mass flow and energy KPIs.
2. **Quasi-static thermal model:** the published tools use dynamic pipe models
   (plug-flow / dynamic pipe). Consequence: at near-zero night flow their pipes
   cool down; a quasi-static steady state keeps the pipe-entry region at supply
   temperature, overestimating night losses. Ceiling of the excess = enthalpy
   flux of the minimum bypass flow (16 × 1.77 kg/h · cp · 60 K ≈ 2.0 kW over
   401 near-zero ticks ≈ ≤ 134 kWh in the CE 1 week).
3. `sections = 1` per pipe (short pipes; steady-state discretization error at
   CE-1 flows ≪ 0.1 %).

## Validation record (2026-07-17, this machine, pandapipes 0.14.0)

`tests/test_destest_validation.py`; engine grid 144 steps/day = the source
10-min resolution (quasi-static permits coarse ticks), `min_qext_w = 62`.

**CE 0** (all consumers at exactly 19 347.28 W, ΔT 30 K — steady state):

| KPI | rtheatflow | published (6 tools) |
|---|---|---|
| plant mass flow | 8876.5 kg/h | 8847.9–8870.4 |
| plant return temp | 39.48 °C | 39.46–39.85 |
| total heat supplied | 314.8 kW | 308.2–314.3 |
| supply temp at SD1 | 69.454 °C | 69.43–69.48 |

mdot/heat sit 0.07 %/0.16 % above the published max — consistent with the
fluid-property difference; the SD1 supply temperature (pure loss physics)
lands inside the published band.

**CE 1** (7-day window, quasi-static, trapezoid-equivalent staircase sums):

| KPI | rtheatflow | published (AixLib / Buildings / IBPSA) |
|---|---|---|
| heat injection | 14.506 MWh | 14.452 / 14.324 / 14.357 MWh |
| network losses | 606.6 kWh | 535.2 / 543.8 / 535.7 kWh |

Loss decomposition: ticks with demand > 5 kW contribute **534 kWh — inside
the published band**; the +72.5 kWh excess comes entirely from the 401
near-zero-demand ticks (mean 1.08 kW ≤ the 2.0 kW bypass ceiling above) —
the documented quasi-static night effect, not a data error. DIMOSIM
(11.5 MWh) is a published outlier and excluded, as in the DESTEST papers.

Published aggregates were recomputed from the vendored result CSVs
(`Networks/CE_1/Results/*Network_0.csv`, `*Network_1.csv`, trapezoid rule,
exactly 7.000 days) — not taken from secondary literature.
