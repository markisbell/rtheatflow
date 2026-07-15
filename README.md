# rtheatflow

Realtime pandapipes district-heating simulator — supply/return hydraulics + thermal state on a live map, three-layer observability (reality / measured / estimated). Sibling of [rtpowerflow](https://github.com/markisbell/rtpowerflow) (netzsim), translated from electricity to heat.

**Status: specification & validation phase.** No application code yet — the build follows [SPEC.md](SPEC.md) (binding build specification for a coding agent, milestones M1–M7).

## What exists today

| Asset | Purpose |
|---|---|
| [SPEC.md](SPEC.md) | The complete build specification: architecture cloned from netzsim, runtime-verified pandapipes 0.14.0 API reference, data contract, wire format, API surface, milestones |
| [scripts/validate_core.py](scripts/validate_core.py) | M1 pre-validation suite: known-answer fixture (converged, balance −0.04 %), solver retry-ladder validation, benchmarks (21–33 ms/solve), transient smoke test |
| [scripts/realistic_year.py](scripts/realistic_year.py) | Physics acceptance scenario: re-parametrized `schutterwald_heat`, 12-month quasi-static year → 9.1–19.1 % annual losses depending on linear heat density (matches German literature) |
| [scripts/generate_profiles.py](scripts/generate_profiles.py) | Archetype profile generator: demandlib VDI 4655 (space heating) + OpenDHW (stochastic DHW), 15-min, seed-salted variants |
| [data/profiles/](data/profiles/) | Generated archetype cache (`index.json` + one JSON per archetype, `q_sh_w`/`q_dhw_w` split per SPEC §5) |

## Quickstart (validation environment)

```
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\validate_core.py
.venv\Scripts\python scripts\generate_profiles.py --out data\profiles
```

Pinned core: `pandapipes==0.14.0` (pulls `pandapower==3.3.3`). Validated on Python 3.11/3.14, Windows 11.

## License

MIT — see [LICENSE](LICENSE). pandapipes is BSD-3 (Fraunhofer IEE / Uni Kassel); demandlib and OpenDHW are MIT.
