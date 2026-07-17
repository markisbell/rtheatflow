# schutterwald — real town network (pandapipes example + gas-net loads)

Real trench geometry of Schutterwald (Baden, 48.459–48.463 N / 7.876–7.890 E)
— the flagship map demo: a 3G network with 44 substations on 2.63 km of
trench, loads synthesized from the town's real gas demand.

## Provenance

- **Sources** (vendored under [`data/sources/schutterwald/`](../../sources/schutterwald/),
  fetched 2026-07-17 from `e2nIEE/pandapipes` tag `v0.14.0`, BSD-3 — the
  pandapipes LICENSE is vendored next to the files):
  - `sw_heat.json` — the STANET-exported district-heating example net
    (`src/pandapipes/networks/network_files/`): 244 supply + 244 return
    junctions, 241 pipes per side, 44 heat consumers, one 70 °C pump.
  - `gas_net_schutterwald_1bar.json` — the gas example net of the same town:
    **1506 house sinks** with SLP `profile_id` and `demand_m3_per_a`.
- **Converter:** `scripts/convert_schutterwald.py` (deterministic, seed 20260717).
- Archetype day profiles from the committed `data/profiles/` cache
  (demandlib VDI 4655 + OpenDHW), as in `demo_dorf`.

## Verified source-data traps (all handled)

1. Junction/pipe `geo` columns store **[lat, lon] — reversed vs the GeoJSON
   spec**. That happens to be this platform's native order → taken as-is,
   range-asserted (48.4–48.5 N, 7.8–8.0 E). `junction_geodata` x/y is
   **EPSG:31467** (Gauß-Krüger zone 3) and is used only for the house→
   substation proximity matching (both nets share the frame).
2. Every pipe carries `inner_diameter_mm = 800` and `u_w_per_m2k = 10` —
   dead columns of the upstream export. Ignored; DNs are re-sized (below).
3. **34 zero-length pipes per side** — STANET connection stubs. Contracted
   away by merging their end nodes (union-find), likewise the two fully-open
   DN200 feeder valves at the plant. 244 supply junctions → **208 trench
   nodes**, 241 pipes → **207 trenches** (a tree, 2.626 km — the source's
   own trench length is preserved exactly; 3 junctions ship `geo = None`
   (plant + 2 aux) and take their coordinate from the incident trench
   polyline endpoint).
4. The heat net is a perfect supply/return name-mirror (`return_<name>`), so
   only the supply side is converted — the platform's builder recreates the
   mirrored return network.

## Load synthesis (documented deviation: 50 m service area)

Each gas house is assigned to its **nearest substation in EPSG:31467**;
houses farther than **50 m** (a typical house-connection length) stay off
the DH net. Rationale: the gas net covers the whole town (33.4 GWh/a), the
heat net only its northern streets — all-houses aggregation would put a
physically incoherent 12.7 MWh/(m·a) on this 2.6 km trench network. The
50 m service area keeps **155 houses ⇒ 3.43 GWh/a** plus 6 substations
without a house in reach, which keep the sw_heat shipped demand
(9.48 MWh/a each) → **3.52 GWh/a total, LHD 1.34 MWh/(m·a)** — squarely in
the viable-DH literature band.

- Gas → heat: `m³/a × 10 kWh/m³ × 0.9` (boiler efficiency).
- Archetype per substation by annual energy (≤15 MWh → EFH_SAN_4P,
  ≤35 MWh → EFH_ALT_4P, else MFH_ALT_10WE), year profile scaled to the
  target annual; result: 34× MFH-class, 6× EFH_SAN, 4× EFH_ALT, annual
  spread 9.5–214 MWh (real heterogeneity, unlike the source's 44 × 6.32 kW).
- One winter day (90th-percentile space-heating day of the archetype year,
  demo_dorf convention), 96 × 15 min; DHW variants rotate, seeded ±30 min
  shift; 3G return temperatures (55/45/50 °C by archetype).
- Weather re-derived by inverting the SPEC §4.5 degree-hour factor from the
  chosen day (override math self-consistent); ground ~6.5 °C (winter).

## Network parametrization

- **DN sizing:** aggregated downstream design load per trench on the tree,
  `ṁ = q_design/(cp·40 K)`, smallest `ISOPLUS_DRE*_STD` with v ≤ 1.2 m/s
  (`realistic_year.py` convention). Result: DN20×44 … DN125×24 (mains),
  design load 1904 kW. Source diameters were dead columns (trap 2).
- **Plant** at the original pump node K1289: 3G heating-curve preset
  (110/70 sliding), p_flow 6 bar, plift 3 bar (fixed; enable
  Schlechtpunktregelung live in the UI if desired).
- Two consumer-less leaf nodes carry the canonical SPEC §3.2 bypass pair
  (0.02 kg/s + 100 W).

## Physics record (2026-07-17, this machine, pandapipes 0.14.0)

`tests/test_schutterwald.py`, 15-min grid: all 96 steps of the winter day
solve at **retry-ladder tier 1** (`solver_status "ok"`, warm ~60–220 ms),
worst energy-balance error 0.075 %, peak velocity 0.98 m/s, worst-point Δp
1.58–2.64 bar. Day energy: feed 19.60 MWh, demand 18.15 MWh, **losses
1.47 MWh → 7.5 % of feed on the winter day**; losses are nearly
demand-independent (~61 kW), so the annualized estimate is
61 kW·8760 h ≈ 537 MWh vs 3.52 GWh demand ⇒ **≈13 % annual loss ratio** —
inside the plausible 8–20 % band for a 3G net at this heat density (the
instantaneous winter ratio is naturally lower; summer would run far higher).
