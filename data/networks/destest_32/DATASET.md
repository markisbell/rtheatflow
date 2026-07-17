# destest_32 — IBPSA DESTEST, 32-building variant (catalog example)

Provenance, license (modified BSD-3, vendored at
`data/sources/destest/license.md`), demand profile, case rules (constant
70 °C source, ΔT = 30 K substations, 10 °C boundary at the outer insulation
surface, bypass floor) and geodata treatment (synthetic Lake-Constance anchor,
local metres, no CRS): **identical to
[`destest_16/DATASET.md`](../destest_16/DATASET.md)** — read that first.

Differences:

- Built from the repo CSVs `Node_data_32_buildings.csv` +
  `Pipe_data_32_buildings.csv` (49 nodes, 48 trenches, 792 m). **No published
  CE-style results exist for this variant** — it is a catalog example
  (load/solve/balance-tested), not a validation case.
- The 32-building file re-dimensions its 16-building sub-block (larger
  diameters than both the 16-building CSV and the case-description Table 6)
  — files are never mixed, exactly as the source warns.
- The two connector rows `m,a` and `q,e` carry empty "Peak Load" /
  pressure-loss fields; they are kept as ordinary mains (they tie the second
  16-building block to the first — the network stays a tree: 49 nodes,
  48 trenches). The single source `i` supplies all 32 buildings.
- Pipe wall geometry is not given in the CSV; the converter assumes
  ISO 15875-2 series S5 (SDR 11, OD = ID·11/9) as in the case description,
  then applies the same layered U′ formula (PE-X λ 0.35, insulation λ 0.026,
  thickness per CSV row). Documented assumption.
- All 32 buildings share the same SFH profile (source convention), first
  7 days, ΔT-pair consumers.
