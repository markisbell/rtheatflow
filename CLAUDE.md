# CLAUDE.md — rtheatflow development log & agent handoff

## What this repo is

Real-time district-heating simulation platform on **pandapipes 0.14.0**, structurally cloned from the blueprint **[markisbell/rtpowerflow](https://github.com/markisbell/rtpowerflow)** (project "netzsim"). **[SPEC.md](SPEC.md) is the binding build specification** — read it fully before writing code. When SPEC.md and your prior API knowledge disagree, SPEC.md wins: its pandapipes facts were verified at runtime against the pinned version.

## Binding rules

1. **⚠️ OPEN ITEM:** SPEC §0 mandates reading https://github.com/markisbell/claude-memory (project owner's rules for AI coding agents). The repo is private/unpublished (404 as of 2026-07-15). Until access is resolved: follow the blueprint repo's CLAUDE.md conventions plus SPEC §9/§11, and reconcile when the rules become available. Do not remove this notice until reconciled.
2. Mirror netzsim structure/naming/behavior (SPEC §9). When in doubt: do what rtpowerflow does.
3. Every milestone (SPEC §12) ends with working, tested, committed code before the next begins.

## Non-negotiable technical pins (evidence in SPEC Appendix A/B)

- `pandapipes==0.14.0` — do **not** pin pandapower separately (0.14.0 pins `pandapower==3.3.3` itself).
- Solve mode: `bidirectional` with the SPEC §3.3 retry ladder. Never `nonlinear_method="automatic"` in bidirectional (raises ValueError).
- Exactly **one** `circ_pump_const_pressure` per net (409 on second).
- Always pass `text_k` explicitly on every pipe (the default 0 means 0 K ambient → ~4× fake losses).
- Pipe-loss formula is **direction-aware** (inlet = `t_from` if mdot ≥ 0 else `t_to`); the naive formula overcounts ~20 % on meshed nets.
- Feed-in KPI = `mdot·cp̄·ΔT`, **not** the raw `res_circ_pump_pressure.qext_w` column (enthalpy form, ~+4.7 %).
- Platform warm start after every converged step (write `res_junction` back into `junction.pn_bar`/`tfluid_k`); reset to supply-temp init after swaps/failures.
- Zero-flow guard: consumers floored ≥ 200–500 W or switched out of service atomically; bypasses per SPEC §3.2.

## Development log

### 2026-07-15 — Project inception (pre-code)
- SPEC.md authored, grounded in multi-agent research on the blueprint repo and pandapipes 0.14.0 source; adversarially verified (18 findings fixed).
- Core validated on the target machine (Win 11, Python 3.14): known-answer fixture reproduced exactly (balance −0.04 %); solve benchmarks 21 ms (8 junctions) / 33 ms (`schutterwald_heat`, 488 junctions); retry ladder validated on the hard `treturn` case (`alpha=0.5` converges); transient smoke test passed.
- Realistic-year physics validation: re-parametrized schutterwald reproduces literature loss ratios endogenously (12.9 % at 1.5 MWh/m·a; 19.1 % rural; 9.1 % urban; winter ~7.5 % vs summer ~45 % instantaneous). Discovered: shipped example net has all pipes at 800 mm inner diameter — benchmark-only, re-parametrize as seed.
- Profile toolbox verified (SPEC §4.5): demandlib VDI 4655 (SH) + OpenDHW (stochastic DHW); LPG/pylpg viable for DHW but its space-heating output is degree-day-only.
- Repo scaffolded; `scripts/generate_profiles.py` generates the first 3 archetypes (EFH alt/saniert, MFH) into `data/profiles/` — 15-min, seed-salted DHW variants, annual sums verified against targets.
- Next: **M1 — headless core** (SPEC §12).
