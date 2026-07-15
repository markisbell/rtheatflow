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

### 2026-07-15 — M1: headless core (branch `m1-headless-core`)

**Built** (SPEC §12 M1; module map per §9.1): `config.py` (pydantic-settings, `RTHEATFLOW_`), `models.py`/`data_loader.py`/`net_inputs.py` (five-file contract with pydantic v2 + cross-validation: node refs, array lengths, horizon alignment, exactly-one-slack, reachability from slack, dead-end zero-flow rejection), `network_builder.py` (build-once supply/return pair expansion, explicit `text_k` everywhere, ISOPLUS std types or 0.14 custom params, dense `[n_elements, ticks]` `ProfileArrays` + explicit `NetIndex` records, qext floor), `weather.py` (§4.5 degree-hour override scaling with ε-guarded summer regime), `heating_curve.py` (§4.2 + 3G/4G presets), `simulator.py` (§3.3 retry ladder with `SOLVER_ITER` semantics + catch-all arm, §3.4 platform warm start with supply-temp reset on failure, §3.6 derived quantities: direction-aware losses, `q_feed = mdot·c̄p·ΔT`, worst-point Δp+argmin, pump P_el, balance_err; §6 `StepResult`, °C on the wire via `_r()`), `engine.py` (asyncio tick loop, `to_thread` solve, Event pause/resume, seek/seek_day/interval floor 0.01 s, off-thread `reconfigure`; `HeadlessStore` stands in for M2's `StateStore`), `sensors.py` (`_r()` only; `MeasurementSet` default-preset stub deferred to M2 per §8a). Fixture: SPEC Appendix A as five files under `data/networks/appendix_a/`.
- **Tests: 51 passed** (~14 s). Known-answer test reproduces every §11 pin exactly on this machine (A mdot 0.674357/t_outlet 328.150; B Δt 30.0000/mdot 0.397762; C mdot 0.2500; pump 1.322118 kg/s; return 324.355 K; end supply 351.837 K; losses 27.258 kW; q_feed 187.182 kW vs raw enthalpy column 195.965 kW; balance −0.04 %). Bidirectional-necessity, retry-ladder hard case (tier 2 α=0.5), forced non-convergence (+ recovery), zero-flow guard, day wrap, override scaling, pandapipes pins all green.
- **Benchmarks** (`docs/BENCHMARKS.md`): warm medians ~27 ms (fixture) / ~21 ms (`schutterwald_heat`), first solve incl. numba JIT ~7 s; significant run-to-run variance on this laptop-class CPU (20–35 ms) — indicative, not thresholds.
- **Deviations from SPEC (documented, deliberate):**
  1. `consumers.json` accepts `controlled_mdot_kg_per_s` as a third partner to `qext_w` (pandapipes pair 3) beyond the §5 sketch's `treturn_k|deltat_k` — required to express Appendix A consumer C and the §3.2 canonical bypass in the contract.
  2. New setting `RTHEATFLOW_MIN_QEXT_W` (default 500 W) — §6's config list has no knob for the §3.2 floor, but §3.2 mandates a validated, generous floor; documented in `.env.example`.
  3. `pump_mass` producers require `p_flow_bar` + `t_flow_k` in addition to §5's `mdot_flow_kg_per_s` (the `create_circ_pump_const_mass_flow` call needs them).
  4. Profile resampling choice (§4.5 "pick one"): staircase, documented in `network_builder.py`.
- **Spec refinement discovered (not an error):** Appendix B item 2's "`nonlinear_method="automatic"` raises `ValueError` under bidirectional" holds only when the damping adaptation *engages* (errors increase mid-iteration; unfixed source TODO in `pipeflow.finalize_iteration` → broadcast shape error). Easy nets can pass without raising; the hard `schutterwald_heat(70, treturn=45)` case reproduces it. Conclusion unchanged (ladder never uses it); pinned in `tests/test_pandapipes_pins.py` on the hard case.
- Next: **M2 — API** (FastAPI + WS, StateStore, `MeasurementSet` default-preset stub, API-surface pinning test).

### 2026-07-15 — M2: API (branch `m2-api`)

**Built** (SPEC §12 M2; §6/§7/§8a/§9.1/§9.3):
- `state.py` — `StateStore`: `latest` + `deque(maxlen=history_size)` (public attrs, M1-test-compatible), WS subscriber set (`asyncio.Lock` for subscriber mutation only), optional recorder `sink` hook (M6), `reset()` keeps subscribers (grid swap). **One** `asdict()`+`_project()` path serves REST `/state`, `/history` and every WS frame; strict mode (`RTHEATFLOW_EXPOSE_GROUND_TRUTH=false`) strips `_TRUTH_KEYS` (junctions/pipes/consumers/summary) and blanks the free-text `error` detail. Dead sockets discarded on send failure. Engine's M1 `HeadlessStore` removed.
- `sensors.py` — §8a interim `MeasurementSet`: default preset `all_consumers` + plant SCADA; `observe(truth_payload) -> (measurements, observed_summary)` is a *projection* of collected truth (heat-meter channels only: q/mdot/T_s/T_r/dp), aggregates over metered elements only. Wired into `Simulator._collect` — every frame carries the observed layer, so strict mode is meaningful and the M4 Δp controller reads observed from day one. Presets `all_consumers|plant_only|clear`; `key_points` + placement CRUD + fidelity modes stay M5.
- `simulator.py` — minimal M2 runtime CRUD: `add_heat_exchanger`/`remove_heat_exchanger` (direct net mutation, dispatch-profile row append/delete, init reset per §3.4, racing solves self-heal per §3.3; verified: 20 kW feed-in → plant backs off 187.2→173.8 kW, balance 0.07 %).
- `api/` — `runtime.py` (App singleton, `API_VERSION = "0.2.0"`, shared `status_payload`, `/network` topology builder with trench-grouped supply/return pipe ids + geometry fallback), `__init__.py` (`create_app(settings, network_dir)`, lifespan loads `data/networks/appendix_a` via the five-file loader → Simulator+StateStore+RealtimeEngine, `RTHEATFLOW_AUTOSTART`, CORS from settings, Swagger at /docs), `core.py` (GET `/` built-in WS-fed HTML monitor, `/health`, `/status`, `/network`, `/state` **404 pre-solve only**, `/history?limit=` 1..10000 default 96, `WS /ws` latest-on-connect then one frame per solved step), `control.py` (start/pause/resume/seek/seekday/interval, each returns fresh status), `weather.py` (GET + PUT/DELETE override, 422 outside −30…45 °C), `plant.py` (GET/POST `/heatingcurve` + 3G/4G presets; `/dpcontrol` deliberately absent until M4 — no stub routes), `producers.py` (GET `/producers`, POST `/producer`: **409 second slack**, 400 missing kind-specific fields/unknown node/kind, functional `heat_exchanger` placement; DELETE `/producer/{id}`: hx removed, slack 409, pump_mass 400-M4, unknown 404). `main.py` uvicorn entry (§9.3). `scripts/gen_api_doc.py` → generated `docs/API.md` (21 routes), staleness-checked by the surface test.
- **Tests: 76 passed** (51 M1 kept green + 25 M2, ~15 s): route-inventory pin, `/state` 404→§6-exact shape, WS latest+stream with frames identical to `/state`, control verbs actually affect the engine (pause freezes production), history-limit 422s, 409/400/422 conventions, functional hx add/remove over the API, override rescales SH ×1.6, 4G preset drops plant t_flow in the physics, strict-mode gating over REST+WS, non-convergence-is-data over the API (sabotaged solver → `converged=false` frames, loop alive, never 500). Verified against a real uvicorn instance too (health/state/history/monitor/WS).
- **Discoveries (folded into SPEC §3.6/§6/§10.1):**
  1. **`res_heat_exchanger` has NO `qext_w` column at runtime 0.14.0** (8 branch cols only) — §3.6's total-feed-in formula referenced it; a latent M1 `_collect` bug (unexercised: the fixture had no hx) surfaced on the first placement. Fixed: dispatch read from the component table (authoritative anyway — it's a fixed input setpoint).
  2. **Producer wire ids are now platform-unique `pid`s** — pandapipes element indices are per-component-table and collide across kinds (slack 0 vs heat_exchanger 0 made `DELETE /producer/0` ambiguous). `producer_meta` carries `pid` + internal `element`.
  3. FastAPI ≥0.139 nests included routers lazily (`_IncludedRouter`); route enumeration for gen_api_doc/surface test goes through the OpenAPI schema + a WS-route walk, not `app.routes` isinstance checks.
- **Deviations (documented, deliberate):** weather-override plausible band set to −30…45 °C (§7 names no band); `POST /producer` scoped to `heat_exchanger` (pump_mass placement → 400 with M4 pointer; full CRUD incl. `POST /producer/{id}/config` is M4); default network is the `DEFAULT_NETWORK_ID = "appendix_a"` constant until the M4 catalog; `httpx2` added to dev extras (starlette ≥1.3 TestClient deprecation). M5-area routes (`/measurements*`), storage/consumer/bypass, networks/scenarios/recording are absent by design — the surface test pins only what exists.
- Next: **M3 — UI map** (Leaflet trench rendering, live color layers, time controls, DE/EN).
