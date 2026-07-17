# verbier — OpenDHN measured meshed network (validation vs real monitoring data)

Real **meshed** district-heating network of Verbier (alpine resort, Swiss
Alps): 2 heating plants + 150 substations with steady-state sensor
measurements — the platform's measured-data validation case and its meshed-
solver stress test.

## Provenance & license

- **Source:** OpenDHN data set, Zenodo **DOI 10.5281/zenodo.10793816**
  (`opendhn-data.zip`, 184.9 kB), vendored verbatim (zip + extracted CSVs)
  under [`data/sources/verbier/`](../../sources/verbier/).
- **License: CC BY 4.0.** Attribution: R. Boghetti & J. H. Kämpf
  (Idiap Research Institute / EPFL), "A benchmark for the simulation of
  meshed district heating networks based on anonymised monitoring data",
  CISBAT 2023. Data provided by Altis.
- ⚠️ The authors' pydhn simulation code (GitHub/Idiap GitLab) is **AGPLv3
  and was deliberately not consulted or copied** — this conversion derives
  from the Zenodo data + its README alone.
- **Converter:** `scripts/convert_verbier.py` (deterministic, offline).

## Source data (verified)

- `network/nodes.csv` — 676 supply + 676 return nodes (`V<k>s`/`V<k>r`),
  anonymised local metres (~6.8 × 7.2 km extent), z uniformly 0.
- `network/pipes.csv` — 681 pipes per side with full layered physics:
  `d_int, t_int` (wall), `t_ins, lambda_ins` (insulation), `t_ext` (casing),
  `is_aerial` (55 pipes), `roughness` [mm]. Supply and return sides are a
  **perfect mirror** (identical base pairs, lengths AND diameters — verified)
  → the platform's single-sided trench contract is lossless here.
- `network/heating_stations.csv` — HS0 (V0), HS1 (V306);
  `network/substations.csv` — S0–S149.
- `data/{power,mass_flow,return_temperature,supply_temperature}.csv` — one
  steady-state case (**CASE1**) for both plants and all 150 substations.

## Conversion decisions

1. **Topology:** supply side → 676 trench nodes, 681 trenches (12.05 km),
   **6 independent loops** (meshed), no zero-length pipes, every leaf is a
   substation (no bypasses needed).
2. **Pipe U:** insulation annulus only —
   `U′ = 2π·λ_ins / ln((r₁+t_ins)/r₁)` with `r₁ = d_int/2 + t_int`;
   the steel wall and PE casing resistances are < 1 % of the insulation's
   and are neglected (documented simplification). pandapipes per-area
   conversion on the inner diameter (`u_w_per_m2k = U′/(π·d_int)`),
   `k_mm` from the source `roughness` column.
3. **Consumers:** measured CASE1 power as fixed loads + measured return
   temperatures (`qext_w` + `treturn_k` — the classic substation pair),
   constant over one 96 × 15-min day. The measured substation **supply**
   temperatures are never used as inputs — they are the validation target.
4. **Two plants, single-slack rule:** HS0 = pressure slack (measured supply
   temp 81.11 °C; pressure side ours: 10 bar / plift 5 bar — the dataset
   carries no pressures). HS1 = `pump_mass` with measured mdot 5.03 kg/s +
   measured supply temp 81.81 °C and **no `p_flow_bar`** → the pressure-free
   `type="t"` pump (M4 discovery: a second pressure-fixing node
   over-determines the loop). This required a documented contract extension:
   `p_flow_bar` is now optional for file-defined `pump_mass` producers,
   mirroring the live-API rule.
5. **Boundary:** not part of the dataset — constant 5 °C ground (alpine
   heating season, assumption) for **all** pipes; the 55 aerial pipes see
   the same boundary (single-`text_k` platform convention). Documented
   uncertainty: ±5 K on the boundary ⇒ roughly ∓7 % on losses.
6. **Geodata:** anonymised local metres, no CRS → projected onto a
   clearly-synthetic WGS84 anchor over **Lake Geneva** (open water,
   46.42 N / 6.55 E) — nobody can mistake it for the real (undisclosed)
   layout. Only relative geometry is meaningful.

## Validation record (2026-07-17, this machine, pandapipes 0.14.0)

`tests/test_verbier.py`, 15-min grid, `min_qext_w = 100` (the smallest
measured substation load is 299 W — below the 500 W app default; the
validation floor sits under it).

**Solver (the meshed stress test):** retry-ladder **tier 2** (bidirectional,
α = 0.5) converges — tier 1 does not on this 1352-junction meshed net with
the type-t second pump; cold init ≈ 28 s, warm ≈ 5 s per solve
(`solver_status "ok"` on the wire). Energy balance closes to 0.06 %.

**Plant level (simulated vs measured CASE1):**

| KPI | rtheatflow | measured |
|---|---|---|
| total feed HS0+HS1 | 3567 kW | 3590 kW (−0.6 %) |
| HS0 mass flow | 17.83 kg/s | 17.76 kg/s (+0.4 %) |
| HS0 return temperature | 44.10 °C | 43.52 °C (+0.58 K) |
| network losses | 297 kW | ≈317 kW implied (−6 %) |

**Substation supply temperatures** (150 points, simulated vs measured):
median \|ΔT\| **0.83 K**, mean 1.29 K, p90 2.68 K, max 12.6 K.
Substation mass flows (where measured > 0.02 kg/s): median error 1.9 %,
p90 5.9 %.

Outliers (honest record): S96 (+12.6 K), S132 (−10.0 K), S148 (−9.4 K),
S90 (+9.1 K), S11 (+8.6 K). Plausible causes, in this order: real-world
sensor placement/averaging in the monitoring data, the uniform 5 °C
boundary (aerial vs buried), and local flow-split sensitivity in the mesh;
S11 is the 1.4 kW micro-load whose tiny flow makes its supply temperature
loss-dominated. Real measured data — generous tolerances by design
(assertions: median ≤ 2 K, p90 ≤ 5 K, max ≤ 20 K).
