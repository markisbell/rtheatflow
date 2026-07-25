# Observability — the three-layer view and the forward-simulation observer

This is the deep dive into rtheatflow's flagship feature: the deliberate,
enforced gap between what a district-heating network (Wärmenetz) **does**, what
its operator can **measure**, and what the operator can **calculate** from those
measurements. It is the heat-side analogue of rtpowerflow's vertical
observability integration, and everything about the platform — the controllers,
strict mode, the estimation layer, the test suite — is arranged to keep the
three layers honest.

`docs/ARCHITECTURE.md` §1 introduces the three-layer idea at system level and §3.5
sketches the observability layers; this document is the mechanism. It owns the
*semantics* of the layers. For where these layers are *rendered* (the
Realität/Gemessen/Schätzung segmented control, the quality badge, the estimated
splice) see `UI_ARCHITECTURE.md`; for the Δp *controller algorithm* see
`PHYSICS_AND_CONTROLS.md`; for the datasets whose measured data validates the
observer see `REFERENCE_NETWORKS.md`.

---

## 1. The three layers as a teaching narrative

A real district-heating operator never sees the true state of the network. They
see a handful of heat meters and pressure/temperature sensors, plus the plant's
own instrumentation, and from that sparse picture they must decide how hard to
run the pump and how hot to make the supply. The gap between the true state and
the measured picture is where operational mistakes live — a starved consumer at
the far end (Schlechtpunkt/worst point) whom nobody has a sensor on, a low return
temperature (Rücklauf/return) that quietly wastes pump energy, a fault that never
trips an alarm because no instrument covers it.

rtheatflow makes that gap **first-class and visible**:

| Layer | German | What it contains | Where it comes from |
|---|---|---|---|
| Reality | Realität | the ground-truth hydraulic + thermal state of every pipe, junction and consumer | the pandapipes solve (`collect_physics`, `simulator.py`) |
| Measured | Gemessen | only what placed devices deliver: heat meters (Wärmemengenzähler) at substations, T/p sensors at nodes, always-on plant SCADA | a **projection** of the truth onto the sensor placement (`MeasurementSet.observe`, `sensors.py`) |
| Estimated | Schätzung | a reconstruction of the *unmeasured* state from the measurements | a forward-simulation observer — a second pandapipes net (`ForwardObserver`, `estimator.py`) |

The intellectual claim the platform defends in code is:

> **The measured and estimated layers may only ever contain information a real
> operator could obtain.** No layer above Reality is allowed to peek at the
> ground truth. This is not a convention that reviewers must remember — it is
> pinned by the honesty tripwire tests (§7) and enforceable at runtime by strict
> mode (§5).

Every field on the wire belongs to exactly one layer. The `StepResult` dataclass
(`simulator.py:46-69`) groups them explicitly:

```
ground-truth layer   junctions, pipes, consumers, summary   ← stripped in strict mode
equipment (visible)  producers, storages, weather, controls
observability        measurements, observed_summary, estimated
```

`producers`/`storages`/`controls` are **operator configuration**, not
measurement — the operator set those dispatch points themselves, so they stay
visible even when the truth is hidden. The rest of this document walks the three
observability keys from the sensor outward.

---

## 2. The measured layer — `MeasurementSet`

`src/rtheatflow/sensors.py` holds two things: the single JSON-safe rounding
helper `_r()` (`sensors.py:43-51` — round to 6 digits, NaN/±Inf/unconvertible →
`None`, imported everywhere a float reaches the wire) and the `MeasurementSet`
class (`sensors.py:76`), which answers one question — *which elements carry a
device* — and then projects the collected truth onto them.

The projection principle is stated at the top of the module and is the whole
point: the measured layer is **never a parallel computation**. `observe()`
selects sensored elements out of the already-collected truth payload; it does not
re-derive anything. That is what makes the layer trustworthy as "what a device
would show" and what makes strict mode meaningful (there is nothing to leak that
the projection did not already choose to expose).

### 2.1 Three device kinds

| Device | German | Placement | Channels (`_METER_CHANNELS` / `_NODE_CHANNELS`) |
|---|---|---|---|
| Heat meter | Wärmemengenzähler | a consumer substation (Übergabestation), keyed by consumer element id | `q_kw`, `mdot_kg_per_s`, `t_supply_c`, `t_return_c`, `dp_bar` (`sensors.py:57-58`) |
| T/p sensor | T/p-Sensor | a trench node (Trasse), keyed by node name — reads the node's supply+return junction pair | `p_supply_bar`, `p_return_bar`, `t_supply_c`, `t_return_c` (`sensors.py:61`) |
| Plant SCADA | Erzeuger-SCADA | the plant, **always** — never a placement | `q_feed_kw`, `t_flow_c`, `t_return_c`, `mdot_kg_per_s`, `pump_el_kw` |

The distinction between the plant and the two placeable devices is deliberate and
physical. A real plant is always instrumented — SCADA is live telemetry, not
metering — so plant quantities appear in every frame regardless of preset and
**never** degrade to the 15-minute raster. Placeable devices (`consumer_meters:
set[int]`, `node_sensors: set[str]`) are what the operator chose to install, and
they carry the fidelity semantics.

`observe(truth, tick)` (`sensors.py:245-357`) builds the payload:

- a `consumers[]` list, one entry per element in `consumer_meters`, each with the
  five meter channels (`sensors.py:284-293`);
- a `nodes[]` list, one entry per node in `node_sensors`, reading the supply (`s`)
  and return (`r`) junctions of that node's pair — a node that vanished in a swap
  is silently skipped (`sensors.py:295-315`);
- a `plant{}` dict pulled straight from the truth `summary` (`sensors.py:317-324`).

If the truth `summary` is empty (no converged physics yet), `observe` returns
`({}, None)` — an honest empty view rather than fabricated zeros
(`sensors.py:261-262`).

### 2.2 Fidelity: `full` vs `standard`, and the honest cold start

One bulk fidelity mode governs every placed device (SPEC §7 defines a single
endpoint; the blueprint's per-device TAF overrides were deliberately not ported).
`METER_MODES = ("full", "standard")` (`sensors.py:70`):

- **`full`** — every channel, every simulation step. `channel()` simply returns
  `_r(value)` (`sensors.py:277-278`).
- **`standard`** — 15-minute-window means (`WINDOW_MINUTES = 15`,
  `sensors.py:73`), aligned to *simulated* time, mimicking a German Lastgang
  meter that only emits interval readings.

The window is expressed in engine ticks, computed steps-per-day-aware in the
Simulator (`simulator.py:372-373`):

```
window_steps = max(1, round(WINDOW_MINUTES * steps_per_day / 1440.0))
```

so at the default 1440 steps/day one window is 15 ticks. Windows are keyed by the
**global** tick (`w = tick // window_steps`, `sensors.py:266`), so they survive
day wraps and seeks.

The accumulator is the blueprint pattern (`_win`/`_acc`/`_held`): while a window
is open, `channel()` accumulates `(sum, n)` per `(kind, id, channel)` key; on a
window boundary the completed means become `_held` and the accumulator resets
(`sensors.py:266-282`). Crucially, a channel reads the **last completed
window's** mean, and returns `None` until the first boundary after placement:

```python
def channel(key, value):
    if not standard:
        return _r(value)
    if value is not None:                    # keep accumulating this window
        s_, n_ = self._acc.get(key, (0.0, 0))
        self._acc[key] = (s_ + float(value), n_ + 1)
    held = self._held.get(key)               # read the last CLOSED window
    return _r(held) if held is not None else None
```

This is the **honest cold start**: a freshly placed standard-mode meter reads
`None` — not zero, not the instantaneous truth — until its first 15-minute window
closes. A device placed mid-window publishes its first (partial-window) mean at
the next boundary, exactly like a real interval meter. `set_mode()` resets all
window state (`sensors.py:162-170`) so a switch never emits a fake instant
reading from a stale accumulator, and removing a device drops its window state
(`_drop_keys`, `sensors.py:146-152`) so a re-placed meter starts cold again.

The cold start has a real downstream consequence: while every meter is `None`,
the observed worst-point Δp is `None`, so the Δp controller is blind and holds
(see §8). The cold-start behaviour is pinned tick-exact by the M5 acceptance test
(ticks 0..14 all `None`, tick 15 publishes the mean over 0..14, mode switches
reset both ways — see the M5 log in `CLAUDE.md`).

### 2.3 Presets — bulk placement, not increments

`PRESETS = ("all_consumers", "plant_only", "key_points", "clear")`
(`sensors.py:65`). A preset **replaces** the whole placement (`apply_preset`,
`sensors.py:174-206`) — these are the bulk actions of the Messungen panel, not
additive edits. Placement mutators (`add_consumer_meter` etc.) flip `preset` to
`"custom"` so the preset name on the wire always describes how the current
placement came to be.

| Preset | Effect |
|---|---|
| `all_consumers` | a heat meter at every substation (the default, `DEFAULT_PRESET`); full coverage |
| `plant_only` | only the plant's T/p sensor pair at the plant node (SCADA is always on regardless) |
| `key_points` | plant T/p + T/p at the net-end leaves + a heat meter at the *currently known* worst point (from the last converged frame; before the first solve none is known and none is placed — honest) |
| `clear` | no placed devices at all — the operator flies blind on plant SCADA only |

The preset context (`consumer_ids`, `plant_node`, `end_nodes`,
`worst_consumer_id`) is supplied by the Simulator through a callback
(`_measurement_context`, `simulator.py:406-427`). The `key_points` worst-point id
comes from the last converged frame's `summary.worst_consumer` — the operator
places that meter where the *known* worst point is, which is not necessarily the
current one. That gap is exactly the moving-worst-point lesson the platform
teaches (§8).

### 2.4 `observed_summary` — the operator's arithmetic on the operator's readings

Alongside the per-device `measurements` payload, `observe()` returns
`observed_summary` (`sensors.py:343-356`): aggregates computed over **metered
elements only**, never over the ground truth.

```
q_feed_kw, q_demand_metered_kw, n_metered, n_consumers,
n_node_sensors, n_nodes, dp_worst_bar, worst_consumer,
t_flow_plant_c, t_return_plant_c, mdot_plant_kg_per_s, pump_el_kw
```

Two subtleties matter for observability:

- `q_demand_metered_kw` sums only the meters that currently report a value
  (`sensors.py:339, 345`). An unmetered consumer contributes nothing; a
  standard-mode meter still inside its cold-start window contributes nothing. So
  this number is honestly *less than* the true demand whenever coverage is
  partial — the operator's view is incomplete by construction.
- `dp_worst_bar` is the **minimum Δp over metered consumers** (`min(dp_metered,
  default=(None, None))`, `sensors.py:340-342`), with `worst_consumer` the name of
  that metered consumer. This is precisely the value the Δp controller consumes
  (§8), and it is `None` when no consumer meter reports a usable Δp.

`GET /measurements` returns the static placement + coverage fractions without a
solve (`placement()`, `sensors.py:210-241`; the coverage block gives
`consumer_fraction`/`node_fraction` per element class), so the UI panel and map
markers can render before the first frame.

---

## 3. Where the observed layer is wired into the frame

Every converged frame carries the observed layer. `Simulator._collect`
(`simulator.py:682-773`) builds the truth payload via the shared `collect_physics`
and then, in one line, projects it (`simulator.py:757-758`):

```python
payload["measurements"], payload["observed_summary"] = \
    self.measurements.observe(payload, tick)
```

Because `observe` reads the same `payload` dict that carries the truth, the
measured layer is provably a subset-projection of the truth for that exact tick —
there is no second solve, no second set of formulas, no opportunity to drift.

The controllers then act on the observed layer only. In `run_step`
(`simulator.py:567-576`), after warm-starting from the converged results, the Δp
controller reads `payload["observed_summary"]["dp_worst_bar"]` — never the truth
`summary.dp_worst_bar`. This is the blindness principle in code: the platform's
own controller is as blind as the operator (§8).

---

## 4. Strict mode — the single projection path

Strict mode (`RTHEATFLOW_EXPOSE_GROUND_TRUTH=false`) is the runtime switch that
turns the three-layer teaching model into an enforced contract: the reality layer
becomes genuinely unavailable to any client, so the UI, the recorder, the
InfluxDB collector and any API consumer see only what an operator would.

The strip happens at exactly **one** point — `StateStore._project`
(`state.py:55-63`):

```python
_TRUTH_KEYS = ("junctions", "pipes", "consumers", "summary")   # state.py:36

def _project(self, payload):
    if not self.expose_ground_truth:
        for key in _TRUTH_KEYS:
            payload.pop(key, None)
        payload["error"] = None      # solver internals are ground truth too
    return payload
```

`frame()` is the sole wire path — `asdict(result)` then `_project` — and REST
`/state`, `/history`, every WebSocket frame and (since M6) the recorder sink all
go through it (`state.py:65-89`). There are no parallel serialization paths, so
strict mode cannot be bypassed by a forgotten endpoint. This is the "one
projection path" principle `ARCHITECTURE.md` §1 states; it is why the recorder
CSV packs are strict-safe (a truth-stripped frame cannot record truth) and why
the bulk exporter projects its replayed frames through a fresh `StateStore.frame`
too (see `ARCHITECTURE.md` §5).

What strict mode strips and what it keeps:

| Stripped | Kept |
|---|---|
| `junctions`, `pipes`, `consumers`, `summary` (the four truth keys) | `measurements`, `observed_summary` — the operator's own data |
| the free-text `error` detail (solver residuals name real elements — ground truth by another name) | `estimated` — derived from measurements, not truth (§6.4) |
| | `producers`, `storages`, `weather`, `controls` — operator configuration, including `blind_spot` |

The last row is the important, non-obvious one. **`estimated` survives strict
mode.** It is not in `_TRUTH_KEYS`, and by design it must not be: the estimate is
reconstructed from measurements alone, so it is legitimately the operator's to
see. Its `error` block (an innovation against measurements, §6.3) is likewise
measurement-derived and stays visible — this is exactly why the observer computes
its error against measurements rather than truth. The strict-mode test
(`tests/test_estimation_m7.py:281-320`) pins both halves: the four truth keys are
absent from `/state`, while `estimated` (including `estimated.error` with
`n_points > 0`) is present.

The `blind_spot` flag also survives, deliberately (§8) — it names no physics
value, only the adequacy of the sensor layout, which is meta-information the
operator is entitled to.

---

## 5. The estimated layer — `ForwardObserver`

pandapipes has **no state estimator** — there is nothing like pandapower's
weighted-least-squares. So the `estimated` layer is not a WLS reconstruction but
a *forward-simulation observer* (a "digital twin estimator"): a second pandapipes
net, driven only by what the operator can know, solved with the same physics, and
compared against the measurements. `src/rtheatflow/estimator.py` owns it.

The design has one governing rule, stated in the module docstring and enforced by
the priors: the twin is driven **only by operator knowledge**, and the priors for
unmetered consumers read the immutable planning contract (`sim.inputs`), the
archetype cache, and the runtime placement *recipes* — **never** the mutable
runtime arrays (`sim.profiles`) or net tables that carry the stochastic per-tick
truth. A runtime anomaly injected into `sim.profiles` therefore cannot leak into
the prior basis. That single discipline is what the honesty tripwires (§7) verify.

### 5.1 What drives the twin (operator knowledge only)

| Source | On the twin | Never |
|---|---|---|
| plant SCADA | slack `t_flow_k` from `measurements.plant.t_flow_c`; pump lift `plift_bar` from `controls.dp_control` | — |
| metered consumers | their measured channels, fidelity-respecting (a standard-mode cold-start `None` → the prior for that channel) | the truth channel |
| unmetered consumers | **pseudo-priors** — the expected profile from planning data | the live per-tick truth |
| secondary producers / storages | the operator's own dispatch setpoints, copied from the live *input* columns (config, always visible per SPEC §6) | realized results |
| weather | the true ambient (incl. the operator's override) + ground temperature — the plant has a weather station and the override is the operator's own knob (documented decision) | — |

### 5.2 The twin: deep copy + signature rebuild

`ForwardObserver` (`estimator.py:294`) is created lazily on the first converged
step and holds the twin net, the `PriorBook`, and a topology/policy signature.
`_ensure_twin` (`estimator.py:326-346`) rebuilds the twin whenever the signature
changes:

```python
self.twin = copy.deepcopy(sim.net)          # a full second pandapipes net
t_flow_now = self.twin.circ_pump_pressure.at[sim.index.slack, "t_flow_k"]
self.twin.junction["tfluid_k"] = t_flow_now  # honest supply-temp init...
self.twin.junction["pn_bar"]  = sim.index.init_pn_bar  # ...never the truth's warm state
self.book = build_prior_book(sim, self.config.prior_basis)
```

The twin **never inherits the truth's warm junction state** — it initializes cold
to supply temperature like any fresh net (§3.4 of the solve model), and after each
converged estimate it warm-starts from **itself** (`estimator.py:526-527`). It is
a genuinely separate solver, not a copy of the answer.

The signature (`_current_signature`, `estimator.py:311-324`) captures consumer
ids + names, heat-exchanger/pump_mass/storage element ids, junction/pipe counts,
the prior basis, and the weather-coupling design points (`t_room_c`,
`t_design_c`). Any topology CRUD or policy change rebuilds the twin with fresh
priors — pinned by `test_topology_crud_rebuilds_the_twin`
(`tests/test_estimation_m7.py:264-274`): adding a bypass makes `bypass_n3` appear
in the estimate at its configured prior pair.

### 5.3 Boundary application (`_apply`)

Each estimate, `_apply` (`estimator.py:350-412`) writes the operator's knowledge
onto the twin for this tick, in four blocks:

1. **Consumer rows** — start from the priors (`book.qext_w(...)`, which runs the
   same §4.5 degree-hour override scaling as the truth), then **overlay measured
   channels where a meter delivers** (`estimator.py:362-377`). A treturn-row
   consumer takes measured `q` + `t_return`; a deltat row takes `q` + measured
   `(t_supply − t_return)`; an mdot row takes `q` + `mdot`. A `None` (cold start
   or unmetered) leaves the prior in place — this is the fidelity-respecting
   fallback that makes the cold start propagate correctly into the estimate.
2. **Plant boundary** — SCADA flow temperature and the operator's `plift`
   (`estimator.py:385-392`).
3. **Weather** — ground temperature onto every pipe (`estimator.py:394-396`).
4. **Operator equipment dispatch** — heat-exchanger `qext`, pump_mass
   flow/temperature/in-service, and storage-branch rows copied from the **live
   input columns** (`estimator.py:398-412`). These are configuration the operator
   set, so copying them is legitimate; they are the `heat_consumer` rows that are
   *not* consumers, identified by index difference.

### 5.4 The prior book — the operator's expectation for the unmeasured

`build_prior_book` (`estimator.py:149-287`) assembles dense prior arrays aligned
with the current consumer row order. Everything derives from planning data. The
per-consumer hierarchy, matched by consumer **name** (the same convention
scenario replay uses):

1. **Runtime placement recipe** (`sim.consumer_ops`): an archetype recipe → the
   expected archetype profile at the recipe's `day_percentile` (scale honored, but
   no jitter/seed/variant knowledge — the operator knows the plan, not the
   realization); a `q_kw` recipe → the flat plan; a bypass → the configured pair.
2. **Planning contract** (`sim.inputs.consumers`): a `building`-tagged consumer →
   the archetype's *expected* profile, day-matched to the weather (a degree-hour
   load forecast: expected day energy `q_design · mean-daily f(T_amb) · 24 h` →
   the closest archetype-year day, `_match_day`, `estimator.py:139-146`) with mean
   DHW across the stochastic variants (`_dhw_expected_shape`,
   `estimator.py:129-136`); an untagged consumer → the hand-authored plan curves
   (teaching nets carry no stochastic realization worth hiding).
3. **Fallback / `prior_basis="design"`** — the crude, honest teaching prior
   `q_design · f(T_amb)` degree-hour space heating, no DHW (`design_prior`,
   `estimator.py:191-195`).

`PriorBook.qext_w` (`estimator.py:118-126`) computes the prior demand at a tick by
scaling the space-heating prior with the live degree-hour factor (override-aware,
the same math as the truth) plus expected DHW, floored like the truth. Setpoint
partners (`treturn_k`/`deltat_k`/`mdot`) always come from planning values.

The two policy choices are exposed as `EstimationConfig.prior_basis`
(`PRIOR_BASES = ("archetype", "design")`, `estimator.py:68`); `archetype` is the
default and the more faithful, `design` is the crude teaching prior.

### 5.5 Solving the twin and building the payload

`_estimate` (`estimator.py:516-541`) runs `_ensure_twin` → `_apply` →
`solve_with_retry(self.twin, ...)` using the **same retry ladder** as the truth
(see `PHYSICS_AND_CONTROLS.md`). If the twin does not converge, `_estimate`
returns `None` and the last estimate stays attached (stale) — twin
non-convergence is data, never a crash. On success the twin warm-starts from its
own results and the payload is built by the **shared** `collect_physics`
(`simulator.py:154-300`), so the estimated `junctions`/`pipes`/`consumers`/
`summary` mirror the truth shape *exactly* — one direction-aware loss formula, one
`mdot·c̄p·ΔT` feed-in, for both truth and twin.

The `estimated` payload keys (`estimator.py:509-540`):

```
junctions, pipes, consumers, summary   ← identical shapes to the truth layer
solver_status, solve_ms                ← the twin's own solve
step, day, seq                         ← staleness stamps
error                                  ← the innovation block (§6.3)
```

### 5.6 The innovation error metric

`error` is the observer's **innovation**: `|twin − measurement|` at every sensored
point, computed against **measurements**, not truth (`_error`,
`estimator.py:416-476`). Computing against measurements is a deliberate deviation
from the blueprint (whose error is truth-based) for two reasons: it is the honest
statement of what the operator can verify ("how far is my reconstruction from my
instruments?"), and it stays valid and visible in strict mode where truth does
not exist. Points contributing:

- metered consumers → return temperature, mass flow, differential pressure;
- T/p node sensors → return temperature and the pair Δp (`p_supply − p_return`);
- plant SCADA → return temperature and mass flow.

Aggregated into buckets (`agg` returns `(max, mean)` via `_r`):

```
max_dt_return_k, mean_dt_return_k,
max_dmdot_kg_per_s, mean_dmdot_kg_per_s,
max_ddp_bar, mean_ddp_bar,
n_points
```

With full coverage the twin is pinned by measured boundary conditions everywhere,
so the innovation goes to ~0 (exact reconstruction); as coverage shrinks, priors
substitute for sensors and the innovation grows. That monotone relationship is
the quantitative face of "coverage buys certainty" (§7).

### 5.7 Throttling and stale attachment

The observer is expensive (~2× a truth solve — it is a second full net; see
`BENCHMARKS.md`), so `maybe_estimate` (`estimator.py:480-514`) gates a refresh
behind **two ANDed gates**:

- **the metering raster** — when any device runs in `standard` mode, refresh only
  at 15-minute window boundaries (`raster = ms.window_steps`); no new information
  exists between boundaries, so re-estimating would be wasted work
  (`estimator.py:493-495`);
- **the wall-clock self-throttle** — a new estimate only after
  `throttle_factor × last_solve_ms` has elapsed (default `throttle_factor = 2.0`,
  `estimator.py:496-498`).

When either gate blocks, `maybe_estimate` returns `self.last` — the previous
estimate rides along, honestly stamped with the `step`/`day`/`seq` of the tick it
was actually computed for, so the UI can show its age. `_wall` is stamped even on
failure (`estimator.py:507`) to prevent a retry storm against a broken twin. The
estimate attaches to failed truth frames too (`run_step`, `simulator.py:586-589`)
— consistent with the platform's reuse-last-converged failure policy.

`test_standard_mode_estimates_on_the_metering_raster`
(`tests/test_estimation_m7.py:227-244`) pins the raster: with standard fidelity,
`seq` is `1` at ticks 0..14 and becomes `2` at tick 15 — one refresh per window.
`test_wall_clock_throttle_attaches_stale_estimate` pins the wall-clock gate.
`test_estimation_failure_is_data` pins that a sabotaged `_apply` leaves the truth
loop untouched and the estimate stale (`seq` unchanged), never a crash.

---

## 6. Configuration and lifecycle

`EstimationConfig` (`estimator.py:74-96`): `enabled` (default `True`),
`prior_basis` (`archetype`|`design`), `throttle_factor` (default `2.0`). It is
exposed through `GET/POST /estimation/config` (`api/measurements.py:130-178`) —
partial update, 422 on out-of-bounds (`throttle_factor` 0–20, `prior_basis` a
`Literal`). The response also carries the observer's live `seq` and
`last_solve_ms` (`_estimation_payload`, `api/measurements.py:145-152`).

Two lifecycle facts:

- **The policy is engine-held.** It survives grid swaps and scenario loads
  (`engine.set_est_config` re-applies it across `reconfigure`), so changing the
  network does not silently reset the observer policy —
  `test_estimation_config_survives_network_swap`
  (`tests/test_estimation_m7.py:323-329`).
- **Changing the policy drops the twin.** `Simulator.set_est_config`
  (`simulator.py:388-392`) clears `_observer`; the next converged step rebuilds it
  lazily with fresh priors. Disabling estimation (`enabled=False`) makes
  `estimated` vanish from fresh frames entirely (`_maybe_estimate`,
  `simulator.py:394-402`).

---

## 7. Honesty tripwires — what the estimate cannot know

The tripwires are the tests that make the observability contract falsifiable.
They live in `tests/test_estimation_m7.py` and encode the claim from §1 as
executable assertions. Each one injects a situation where a naive estimator would
cheat, and asserts it does not.

### 7.1 An unmetered anomaly stays invisible

`test_tripwire_unmetered_anomaly_stays_invisible`
(`tests/test_estimation_m7.py:148-170`). Meter only consumer B; inject a runtime
anomaly on consumer A (unmetered) directly into the mutable truth:

```python
sim.profiles.q_dhw_w[0, :] += 80_000.0    # doubled demand
sim.profiles.treturn_k[0, :] += 15.0      # +15 K return anomaly
```

The assertions:

```
truth A:  q_kw > 155.0,  t_return_c > 68.0        # the anomaly IS in the truth
est   A:  q_kw < 90.0  (≈ 80 kW plan),  t_return_c < 57.0   # ... NOT in the estimate
```

The estimate stays at the **prior** because no sensor covers A and the priors read
the immutable contract, not `sim.profiles`. This is the platform's central
honesty claim, live.

### 7.2 A metered anomaly propagates

`test_tripwire_metered_anomaly_propagates`
(`tests/test_estimation_m7.py:173-187`). The *same* anomaly, but the meter is on
A this time. Now the estimate must show it (`est A q_kw > 155.0`, `t_return_c >
68.0`) — the meter delivers the information, so the observer is allowed to know
it. Together with 7.1 this proves the estimate carries exactly the information the
sensor layout provides, no more and no less.

### 7.3 With `clear`, the estimate equals the priors

`test_tripwire_clear_preset_estimate_equals_priors`
(`tests/test_estimation_m7.py:190-207`). Under the `clear` preset (plant SCADA
only), every estimated per-consumer value equals the prior exactly (±1e-3 kW /
±0.05 K) — the twin is *driven* by the priors and nothing else. The same test
asserts the priors are provably **not** the per-tick truth (stochastic DHW +
jitter make them differ), so this is not a trivial pass.

### 7.4 Full coverage ⇒ exact reconstruction; error degrades monotonically

`test_full_coverage_estimate_matches_truth`
(`tests/test_estimation_m7.py:101-114`): with `all_consumers` + full fidelity the
estimated summary matches truth within 0.1 kW (feed), 0.01 kW (demand), 1e-3 kg/s
(mdot), 1e-3 bar (dp_worst), and `error.max_dt_return_k < 0.01`,
`max_dmdot_kg_per_s < 1e-4`, `max_ddp_bar < 1e-4`. Full observability = exact
reconstruction, to the wire's six digits.

`test_error_metric_degrades_as_coverage_shrinks`
(`tests/test_estimation_m7.py:117-141`): plant-mdot innovation is
`full < half-coverage < clear` (strictly), and the reported
`error.max_dmdot_kg_per_s` carries the same signal (`> 0.01` at `clear`, `< 1e-4`
at full) — the degradation is visible in the number the UI shows, and the reported
plant deviation *is* the real deviation at that sensor.

| Tripwire | Claim it defends | Test |
|---|---|---|
| unmetered anomaly invisible | the estimate cannot see past the sensors | `:148` |
| metered anomaly propagates | the estimate uses everything the sensors give | `:173` |
| `clear` ⇒ priors | with no meters, only operator expectation drives the twin | `:190` |
| full ⇒ exact | full coverage buys certainty | `:101` |
| error monotone in coverage | uncertainty is honestly reported | `:117` |
| strict mode | truth hidden, estimate visible | `:281` |

---

## 8. The Δp blind-spot as an observability concept

The worst-point Δp controller (Schlechtpunktregelung) is where observability and
control meet. `PHYSICS_AND_CONTROLS.md` owns the *controller algorithm*
(`dp_control.py` — one clamped proportional step per tick, `controlled` vs `fixed`
mode). This document owns the *blindness semantics*: why the controller sees a
different worst point than reality does, and how the platform surfaces that gap.

The controller consumes **only** `observed_summary.dp_worst_bar` — the minimum Δp
over *metered* consumers (§2.4) — never the truth. `DpController.step`
(`dp_control.py:63-75`) holds `plift` unchanged when that reading is `None`:

```python
if self.mode != "controlled" or dp_worst_observed_bar is None:
    return float(plift_bar)         # blind → hold
```

The degradation ladder (documented in `dp_control.py:17-33`):

1. **No usable Δp reading** — presets `clear`/`plant_only`, or every meter still
   in its standard-mode cold-start window → `dp_worst_bar` is `None` → the
   controller **holds** the lift. Holding *is* "controlling on plant Δp": a
   constant-Δp pump with no remote sensor.
2. **Metered, but the true worst point carries no meter** — the controller
   regulates on the best *measured* Δp, which is exactly what a real
   Schlechtpunkt controller does; it cannot know better. But the true worst point
   may be starved while the measured one sits at setpoint.

The platform — unlike the operator — knows the truth, so it can flag case 2. The
`blind_spot` flag is computed in `_collect` (`simulator.py:767-772`):

```python
obs = payload["observed_summary"] or {}
worst_el = int(idx.consumers[worst_pos])      # truth's argmin worst point
self._blind_spot = (
    obs.get("dp_worst_bar") is None            # no metered Δp at all, OR
    or worst_el not in self.measurements.consumer_meters)  # true worst unmetered
```

It is written into `controls.dp_control.blind_spot` and surfaces on every frame
(the WorstPointSection cockpit renders it — see `UI_ARCHITECTURE.md`). Three
properties make it a clean observability concept rather than a physics leak:

- it names **no physics value** — only a boolean about whether the sensor layout
  covers the true worst point;
- it is deliberate meta-information about **sensor-layout adequacy** (SPEC §8a
  asks the UI to show *why* blindness is worse), i.e. it teaches the operator to
  move a meter, not to distrust the number;
- it therefore stays **visible in strict mode** — `controls` is not a truth key,
  and the flag exposes nothing the operator shouldn't have.

The M5 acceptance evidence (`CLAUDE.md`) shows this live on demo_dorf: with
`key_points`, as DHW jitter oscillated the true worst point between two consumers
(C9↔C5), `blind_spot` went true exactly while the unmetered C9 was critical — the
observed layer honestly reporting the *metered* C5 at a *higher* Δp. That is the
moving-worst-point lesson the whole three-layer model exists to make visible.

---

## 9. End-to-end: one frame's observability journey

```
                  pandapipes solve (truth)
                          │
              collect_physics()  ── junctions/pipes/consumers/summary  ── REALITÄT
                          │
        ┌─────────────────┼──────────────────────────┐
        │                 │                          │
  MeasurementSet.observe(payload, tick)      ForwardObserver.maybe_estimate(payload, ...)
        │                 │                          │
   pick sensored     observed_summary          twin (deep copy) ← priors ⊕ measured
   elements only     (metered aggregates)      solve (same ladder) → collect_physics
        │                 │                          │
  measurements{}    dp_worst_bar → Δp controller     estimated{ ..., error }  ── SCHÄTZUNG
   (GEMESSEN)        (blind → hold; blind_spot flag)  (innovation vs measurements)
        │                 │                          │
        └─────────────────┴──────────────────────────┘
                          │
                 StepResult (asdict)
                          │
             StateStore._project()  ── strict mode: drop junctions/pipes/consumers/summary,
                          │            blank error; keep measurements/observed_summary/estimated
                          ▼
              REST /state · /history · WS /ws · recorder sink   (one path)
```

The single most important structural fact: there is **one** truth solve, **one**
projection to the measured layer, **one** twin solve for the estimate, and **one**
serialization path with **one** strict-mode strip. Every layer above Reality is a
transformation *of* the frame, computed from what an operator could know, and
provably unable to reach around the projection to the ground truth. That is the
intellectual heart of rtheatflow, and it is enforced, not merely intended.

---

## 10. Cross-references

- `docs/ARCHITECTURE.md` §1 (three-layer view), §3.4 (controllers on the measured
  layer), §3.5 (observability layers) — the system-level statement this document
  details.
- `PHYSICS_AND_CONTROLS.md` — the Δp controller algorithm, the retry ladder shared
  by truth and twin, the derived quantities in `collect_physics`.
- `UI_ARCHITECTURE.md` — the Realität/Gemessen/Schätzung segmented control, the
  estimated splice pattern, the quality badge (innovation + estimate age), and the
  WorstPointSection blind-spot note.
- `REFERENCE_NETWORKS.md` — Verbier's 150 measured substations, the real
  measured-data validation the observer's fidelity claims lean on.
- `BENCHMARKS.md` — the observer's ~2× cost and the self-throttle's headroom.
- Wire schemas: `StepResult` (`simulator.py:46-69`), `measurements`
  (`sensors.py:326-332`), `observed_summary` (`sensors.py:343-356`), `estimated`
  (`estimator.py:509-540`), `controls.dp_control` (`simulator.py:630-646`).
