# data/sources/destest — vendored IBPSA DESTEST CE_1 source data

Vendored 2026-07-17 from <https://github.com/ibpsa/project1-destest>
(master) so that `scripts/convert_destest.py` is offline-reproducible.
License: modified BSD-3 (IBPSA), vendored verbatim in [`license.md`](license.md).

| File | Origin | Used for |
|---|---|---|
| `Node data.csv` | `Networks/CE_1/input_data/Node%20data.csv` | destest_16 nodes |
| `Pipe_data.csv` | `Networks/CE_1/input_data/` | record only — superseded by Table 6 (see destest_16 DATASET.md "Which pipe table?") |
| `Pipe_data_16_case_description_table6.csv` | transcribed from the official case-description doc, Table 6 (Google Doc `1DJnhsGFzkdIKSy9IZ3BrnCXD3Ko-4JHfkC39-XdT9r8`, linked from `Networks/CE_1/README.md`) | destest_16 pipes |
| `Node_data_8_buildings.csv`, `Pipe_data_8_buildings.csv` | `Networks/CE_1/input_data/` | destest_8 |
| `Node_data_32_buildings.csv`, `Pipe_data_32_buildings.csv` | `Networks/CE_1/input_data/` | destest_32 |
| `heat_profile_1_building_SFH_Network_1.csv` | `Networks/CE_1/input_data/` | demand (600-s steps, 36 867 rows ≈ 256 days; identical profile for every building) |
| `results/*Network_0.csv` (6 tools) | `Networks/CE_1/Results/` | CE 0 published KPIs (steady state) |
| `results/{AixLib_Plug_Flow,Buildings_Library_Dynamic_Pipe,IBPSA_Library_Plug_Flow,DIMOSIM_Dynamic_Pipe}_Network_1.csv` | `Networks/CE_1/Results/` | CE 1 published 7-day series (DIMOSIM = documented outlier) |
| `results/parameters_DESTEST_Network_*.txt` | `Networks/CE_1/Results/` | column/units definitions for the result files |

Conversion decisions, physics, and the validation record live in
`data/networks/destest_16/DATASET.md` (+ the 8/32 variants' notes).
