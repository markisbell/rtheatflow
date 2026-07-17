# destest_8 — IBPSA DESTEST, 8-building variant (catalog example)

Provenance, license (modified BSD-3, vendored at
`data/sources/destest/license.md`), demand profile, case rules (constant
70 °C source, ΔT = 30 K substations, 10 °C boundary at the outer insulation
surface, bypass floor) and geodata treatment (synthetic Lake-Constance anchor,
local metres, no CRS): **identical to
[`destest_16/DATASET.md`](../destest_16/DATASET.md)** — read that first.

Differences:

- Built from the repo CSVs `Node_data_8_buildings.csv` +
  `Pipe_data_8_buildings.csv` (13 nodes, 12 trenches, 216 m). **No published
  CE-style results exist for this variant** — it is a catalog example
  (load/solve/balance-tested), not a validation case.
- The CSV's empty "Total pressure loss" column is upstream design output —
  ignored (as is the hard-coded "U-value" column, see destest_16).
- Pipe wall geometry is not given in the CSV; the converter assumes
  ISO 15875-2 series S5 (SDR 11, OD = ID·11/9) as in the case description,
  then applies the same layered U′ formula (PE-X λ 0.35, insulation λ 0.026,
  thickness per CSV row). Documented assumption.
- All 8 buildings share the same SFH profile (source convention), first
  7 days, ΔT-pair consumers.
