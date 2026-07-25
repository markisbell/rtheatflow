# Physics and Controls

This document is the deep dive into rtheatflow's simulation core: the
district-heating (Fernwärme) physics model that pandapipes solves, and the
operator controls layered on top of it. It is the companion to
[ARCHITECTURE.md](ARCHITECTURE.md) — read that first for the
Engine · Simulator · StateStore separation (§3.1), the five-file data contract
(§3.2), and the high-level statement of quasi-static fidelity (§4). This doc
owns the *mechanisms*: how the net is built, how the solve is driven, how the
derived quantities are computed, and exactly what each control knob does.

Every formula, function name, and line anchor below was read from the code at
master. When this doc says "the naive loss formula overcounts ~20 %" or "the
enthalpy column reads ~+4.7 % high", those are numbers the code and its
known-answer fixture actually produce, not rules of thumb.

Related docs: [OBSERVABILITY.md](OBSERVABILITY.md) owns why the *measured*
layer differs from truth and the Δp blind-spot semantics;
[REFERENCE_NETWORKS.md](REFERENCE_NETWORKS.md) owns the five-file contract and
per-network validation; [BENCHMARKS.md](BENCHMARKS.md) owns solve timings.

---

## 1. The pandapipes supply/return hot-water model

pandapipes 0.14.0 is a stationary pipe-flow solver. rtheatflow models a
two-pipe district-heating network — a hot supply (Vorlauf) and a cool return
(Rücklauf) — by expanding every abstract trench node and every trench into a
**pair** of pandapipes elements. `build_network(inputs, steps_per_day,
min_qext_w)` (`src/rtheatflow/network_builder.py:116`) does this **once** per
configuration and returns `(net, ProfileArrays)`; each simulation tick only
overwrites element values and re-solves. The net is never rebuilt per step.

### 1.1 Element expansion

| Input document | pandapipes element | Expansion rule |
|---|---|---|
| `network_structure.json` trench node | two `junction`s | `<name>_s` (supply) + `<name>_r` (return), same `geodata=(lon, lat)`, same `pn_bar`, both initialized `tfluid_k = slack.t_flow_k` (`network_builder.py:138-148`) |
| `pipes.json` trench | two `pipe`s | supply pipe `from→to`; return pipe `to→from` (reversed toward the plant), shared geometry (`:154-175`) |
| `consumers.json` row | one `heat_consumer` | supply junction → return junction, `qext_w` + one partner setpoint (`:187-207`) |
| `producers.json` slack | one `circ_pump_const_pressure` | return junction → supply junction; exactly one per net (`:230-238`) |
| `producers.json` heat_exchanger | one `heat_exchanger` | return → supply, feed-in = negative `qext_w` (`:239-251`) |
| `producers.json` pump_mass | one `circ_pump_const_mass_flow` | return → supply, `type="t"` or `"pt"` (`:252-271`) |

A minimal network with one plant, one pipe and one consumer becomes:

```
              supply pipe (from→to)
  plant_s ●──────────────────────────▶● house_s
     ▲                                    │  heat_consumer
     │ circ_pump_const_pressure           │  (supply→return,
     │ (slack: p_flow, plift, t_flow)     ▼   draws qext_w)
  plant_r ●◀──────────────────────────● house_r
              return pipe (to→from)
```

The slack pump sits between the plant's return and supply junctions: it fixes
the supply pressure (`p_flow_bar`), adds the pump lift (`plift_bar`) and sets
the flow temperature (`t_flow_k`). Every consumer draws its `qext_w` between
its node's supply and return junction, so mass flows from `plant_s` out to the
houses and back through the return to `plant_r`.

### 1.2 Consumer control pairs

Every `heat_consumer` always carries `qext_w` (the heat drawn) plus exactly
**one** partner setpoint that closes the thermal-hydraulic control
(`ConsumerSpec` in `models.py:103`, applied at `network_builder.py:191-201`):

- **`treturn_k`** (pandapipes pair 5, the platform default): a return-
  temperature profile. The solver finds the mass flow that delivers `qext_w`
  while returning at `treturn_k`.
- **`deltat_k`** (pair 4): a fixed supply−return spread.
- **`controlled_mdot_kg_per_s`** (pair 3): a fixed mass flow — also the shape
  of the canonical bypass (see §7.4).

`NetIndex` (`network_builder.py:45`) records a boolean mask per consumer
(`treturn_mask` / `deltat_mask` / `mdot_mask`) so the per-tick input
application (`Simulator._apply_step`) knows which rows to update and which
carry a fixed flow.

### 1.3 Units and two build-once invariants

- **Kelvin inside, °C on the wire.** `KELVIN = 273.15` (`network_builder.py:42`).
  All pandapipes temperatures are Kelvin; every temperature in a `StepResult`
  is converted to °C in `collect_physics` and rounded by `_r()` (six digits,
  NaN/±Inf → `null`).
- **`text_k` is passed explicitly on every pipe** (`network_builder.py:151,155`):
  `text_k0 = t_ground_c[0] + KELVIN`. The `create_pipe` signature default is
  `text_k=0`, i.e. 0 K ambient — which would produce roughly 4× fabricated
  heat loss. Every tick then refreshes `net.pipe["text_k"]` from the live
  ground temperature (`simulator.py:481`).
- **Element creation order == input row order == pandapipes element index.**
  The dense `[n_elements, ticks]` `ProfileArrays` rows line up with the element
  table by construction; `NetIndex` guards the coupling with explicit index
  records rather than assuming it.

Pipe geometry uses either an ISOPLUS `std_type` or
`create_pipe_from_parameters` with `inner_diameter_mm`, `u_w_per_m2k` and
optional `k_mm` roughness (`network_builder.py:157-173`). The `u_w_per_m2k`
value is applied on the inner diameter (pandapipes' verified DO fallback).

---

## 2. The bidirectional solve and the retry ladder

### 2.1 Why `bidirectional`

Temperature-controlled consumers make mass flow depend on the *arriving*
temperature: a `treturn_k` consumer must pull more flow when the supply is
cooler to still deliver `qext_w`. That couples the hydraulic and thermal
solutions, so the platform default is `mode="bidirectional"`, which iterates
hydraulics and heat transfer together. `mode="sequential"` solves them once
each and silently misses the set points — it is the platform's *degraded*
fallback, never the intended path.

### 2.2 The four-tier ladder

`retry_attempts(iter_base)` (`simulator.py:76-89`) returns the ladder;
`solve_with_retry` (`:102-145`) executes it top to bottom, returning on the
first tier that converges:

| Tier | Call | Purpose | Reported status |
|---|---|---|---|
| 1 | `bidirectional, iter=N` | normal solve | `ok` |
| 2 | `bidirectional, iter=N, alpha=0.5` | half-step damping for a hard case | `ok` |
| 3 | `bidirectional, iter=2N, alpha=0.2` | heavy damping, double the iterations | `ok` |
| 4 | `sequential, iter=N` | decoupled last resort — set points **not** honored | `degraded` |

`N` is `RTHEATFLOW_SOLVER_ITER` (default 100, `config.py:38`). It is the single
iteration knob: tiers 1, 2 and 4 use `N`; tier 3 uses `2·N` by definition.

```python
def retry_attempts(iter_base: int) -> list[dict]:
    n = int(iter_base)
    return [
        dict(mode="bidirectional", iter=n),
        dict(mode="bidirectional", iter=n, alpha=0.5),
        dict(mode="bidirectional", iter=2 * n, alpha=0.2),
        dict(mode="sequential", iter=n),  # degraded: set points not honored
    ]
```

The ladder was validated on the hard `schutterwald_heat(70, treturn=45)` case,
where tier 2 (`alpha=0.5`) is what converges; the meshed Verbier net needs tier
2 as its normal path (tier 1 fails — the expected meshed stress case).

### 2.3 Why never `nonlinear_method="automatic"`

The ladder never passes `nonlinear_method="automatic"`. Under
`mode="bidirectional"` pandapipes 0.14.0 raises `ValueError` from the automatic
damping adaptation: when iteration errors increase mid-solve,
`pipeflow.finalize_iteration` hits an unfixed broadcast-shape bug. Easy nets
can pass without triggering it, but the hard `treturn` case reproduces it
deterministically, so it is pinned out in `tests/test_pandapipes_pins.py`. The
ladder's fixed `alpha` damping (tiers 2–3) is the deliberate, safe substitute.

### 2.4 Non-convergence is data, never a crash

`solve_with_retry` catches `PipeflowNotConverged` **and** a deliberate catch-all
`Exception` arm (`simulator.py:129-134`). The catch-all matters because there
are no locks by design (SPEC §3.3): a racing runtime mutation can poison one
step, and the platform self-heals on the next tick rather than serializing the
loop. If every tier fails, `solve_with_retry` returns
`SolveOutcome(converged=False, status="failed")` — it never re-raises.

`run_step` then reuses the last converged physics and publishes a frame with
`converged=false` / `solver_status="failed"` (`simulator.py:577-581`,
`_reused_payload` at `:611`). The asyncio loop stays alive; a solver problem
surfaces as a frame field and a UI badge, never an HTTP 500. `_apply_step` is
itself wrapped in a try/except (`:534-539`) so even a poisoned input application
degrades the frame to `failed` instead of killing the loop.

### 2.5 Platform warm start

After every converged step the solved junction state becomes the next
initialization (`simulator.py:564-566`):

```python
self.net.junction["pn_bar"]  = self.net.res_junction.p_bar.values
self.net.junction["tfluid_k"] = self.net.res_junction.t_k.values
```

Because consecutive ticks are physically close (a one-minute step at the
default 1440 steps/day), starting from the previous solution is what keeps warm
solves at ~20–35 ms. After a grid swap, any topology CRUD, or a failed step,
`_reset_initialization()` (`:604-609`) instead resets to a cold supply-
temperature init (`tfluid_k = t_flow_k`, `pn_bar = init_pn_bar`) — the
validated continuation strategy, since the warm state may no longer match the
changed net. Every equipment-CRUD method calls `_reset_initialization()`
(the "topology CRUD → cold init" comments throughout `simulator.py`).

### 2.6 The per-tick sequence

`run_step(step, day)` (`simulator.py:523`) drives one tick in this fixed order:

```
_apply_step(tick)                       # profiles → weather → curve → ground → equipment
      │                                 #   (simulator.py:454)
      ▼
solve_with_retry(net, SOLVER_ITER)      # the retry ladder (:542)
      │
      ├─ converged ─▶ _integrate_storages()   # SoC advances from SOLVED results (:553)
      │               _collect(tick)          # derived quantities + wire payload (:554)
      │               warm start write-back    # res_junction → junction init (:564)
      │               dp_control.step(...)     # ONE Δp step, post-solve (:570)
      │               _maybe_estimate(...)     # forward observer (throttled) (:587)
      │
      └─ failed ────▶ _reset_initialization()  # cold init (:580)
                      _reused_payload(tick)     # reuse last converged (:581)
```

Note the ordering: storage SoC is integrated **before** the frame is collected,
so the frame carries this tick's SoC; the Δp controller runs **after** the
solve and collection, so its correction lands on the *next* solve (§6).

---

## 3. Derived quantities (§3.6)

pandapipes returns raw result tables; the platform computes every reported KPI
itself in `collect_physics(net, idx, fluid, pipe_trench, pump_eta, storages)`
(`simulator.py:154`). This module-level function is shared verbatim by the
truth payload and the M7 forward observer's twin
([OBSERVABILITY.md](OBSERVABILITY.md)) — one set of formulas, never two.

### 3.1 Direction-aware per-pipe heat loss

The loss on a pipe is `q_loss = |ṁ| · c̄p · (T_inlet − T_outlet)`. The subtlety
is *which end is the inlet*: on a meshed net (Verbier's six loops) a pipe can
carry flow in either direction, and the return pipes always flow "backwards"
relative to their `from→to` definition. The formula reads the inlet from the
sign of the mass flow (`simulator.py:167-175`):

```python
mdot_pipe = rp.mdot_from_kg_per_s.values
fwd   = mdot_pipe >= 0
t_in  = np.where(fwd, rp.t_from_k.values, rp.t_to_k.values)
cp_pipe = fluid.get_heat_capacity((t_in + rp.t_outlet_k.values) / 2)
q_loss_w = np.abs(mdot_pipe) * cp_pipe * (t_in - rp.t_outlet_k.values)
```

Two details make this exact:

1. **Inlet follows direction:** `t_in = t_from_k` when `ṁ ≥ 0`, else `t_to_k`.
   A naive formula that always treats `t_from` as the inlet gets the sign
   wrong on every reverse-flowing branch and **overcounts total loss by ~20 %**
   on meshed nets.
2. **Outlet is the branch's own `t_outlet_k`, never `t_to_k`.** `t_outlet_k` is
   the pipe's outlet temperature *before* junction mixing; `t_to_k` is the
   mixed node temperature. Using `t_to_k` folds other branches' contributions
   into this pipe's loss.

`cp` is evaluated by the fluid model at the branch's *mean* temperature
`(t_in + t_outlet)/2`, so it tracks water's temperature-dependent heat
capacity rather than a constant.

### 3.2 Feed-in: `ṁ · c̄p · ΔT`, not the enthalpy column

The plant feed-in is computed the same way, from the slack pump's result row
(`simulator.py:177-183`):

```python
mdot_plant = float(abs(rc.mdot_from_kg_per_s))
t_mean  = (float(rc.t_outlet_k) + float(rc.t_from_k)) / 2
cp_plant = float(fluid.get_heat_capacity(np.array([t_mean]))[0])
q_feed_plant_w = mdot_plant * cp_plant * (float(rc.t_outlet_k) - float(rc.t_from_k))
```

This deliberately does **not** use `res_circ_pump_pressure.qext_w`. That
column is an enthalpy-based form that reads **~+4.7 %** high and does not close
the energy balance: on the Appendix A known-answer fixture the `ṁ·c̄p·ΔT` feed
is **187.182 kW** against the raw column's **195.965 kW**
(195.965 / 187.182 = 1.047). The platform reports the balance-closing value.

Secondary feed-ins are added consistently (`simulator.py:185-223`):

- **heat_exchanger** — feed-in is stored as a negative `qext_w` input setpoint.
  Note `res_heat_exchanger` has **no** `qext_w` column at runtime in 0.14.0, so
  the value is read from the component table (`net.heat_exchanger[..., "qext_w"]`),
  which is authoritative anyway since it is a fixed input.
- **storage discharge** — an in-service discharge pump feeds like a producer,
  `ṁ·c̄p·(t_out − t_from)` from `res_circ_pump_mass`.
- **pump_mass** — same `ṁ·c̄p·ΔT` form, realized from the result table.

### 3.3 Energy balance check

`collect_physics` closes the balance every tick (`simulator.py:222-231`):

```
q_feed_in = q_feed_plant + q_secondary + q_discharge + Σ q_pump_mass
balance_err = q_feed_in − (q_demand + q_loss_total + q_charge)
```

Storage *charge* branches are `heat_consumer` rows too, but they belong to the
storage bucket (`q_charge`), never to the demand KPI — `q_demand` sums only the
true consumer rows (`rhc_c` = `rhc.loc[idx.consumers]`). `summary.balance_err_kw`
is a per-step honesty tripwire; on the fixture it is −0.04 %, on demo_dorf
≤ 0.03 %.

### 3.4 Worst point and pump electrical power

The worst point (Schlechtpunkt) is the consumer with the smallest available
differential pressure (`simulator.py:233-237`):

```python
dp_cons  = (rhc_c.p_from_bar - rhc_c.p_to_bar).to_numpy()
worst_pos    = int(np.argmin(dp_cons))
dp_worst_bar = float(dp_cons[worst_pos])
worst_consumer = idx.consumer_names[worst_pos]
```

`worst_pos` (the argmin) is passed back through `aux` so the caller can flag the
Δp blind spot — whether *that* consumer carries a meter
([OBSERVABILITY.md](OBSERVABILITY.md)).

Pump electrical power is hydraulic power over efficiency (`:239-242`):

```python
dp_pump_pa = (float(rc.p_to_bar) - float(rc.p_from_bar)) * 1e5
p_hyd_w    = abs(float(rc.vdot_m3_per_s)) * abs(dp_pump_pa)   # P_hyd = V̇·Δp
pump_el_w  = p_hyd_w / pump_eta                               # η default 0.7
```

`pump_eta` is `RTHEATFLOW_PUMP_ETA` (default 0.7, `config.py:42`). Pump P_el is
the number that makes the low-ΔT syndrome visible (§5.3): halving the supply
temperature roughly doubles the mass flow and quadruples the hydraulic power.

The `summary{}` block assembled from these (`simulator.py:275-288`):
`q_feed_kw`, `q_demand_kw`, `q_loss_kw`, `loss_pct`, `pump_el_kw`,
`dp_worst_bar`, `worst_consumer`, `t_flow_plant_c`, `t_return_plant_c`,
`mdot_plant_kg_per_s`, `q_storage_kw`, `balance_err_kw`.

---

## 4. The heating curve (Heizkurve)

The heating curve is the plant's supply-temperature control: colder outside →
hotter supply. `HeatingCurve` (`heating_curve.py:20`) implements a sliding
curve with a shape exponent:

```
t_flow(t_amb) = clamp( t_flow_min
                       + (t_flow_design − t_flow_min) · ratio^(1/n),
                       t_flow_min, t_flow_design )
     ratio = max(0, (t_room − t_amb) / (t_room − t_amb_design))
```

```python
def t_flow_c(self, t_amb_c: float) -> float:
    ratio = (self.t_room_c - t_amb_c) / (self.t_room_c - self.t_amb_design_c)
    ratio = max(0.0, ratio)
    t = self.t_flow_min_c + (self.t_flow_design_c - self.t_flow_min_c) * ratio ** (1.0 / self.n)
    return min(max(t, self.t_flow_min_c), self.t_flow_design_c)
```

The parameters (`heating_curve.py:22-26`, defaults):

| Field | Default | Meaning (German) |
|---|---|---|
| `t_amb_design_c` | −12.0 | design outdoor temperature (Auslegungstemperatur) |
| `t_flow_design_c` | 110.0 | supply temperature at design (Auslegungsvorlauf) |
| `t_flow_min_c` | 70.0 | minimum / summer supply floor |
| `t_room_c` | 20.0 | reference room temperature (Raumtemperatur) |
| `n` | 1.0 | curve exponent: 1 = linear, ~1.3 radiator characteristic |

### 4.1 The 3G / 4G presets

`PRESETS` (`heating_curve.py:50-53`) make the 3rd-vs-4th-generation
temperature-lowering story one click:

- **`"3G"`** — `t_flow_design=110`, `t_flow_min=70` (a classic 110/70 system).
- **`"4G"`** — `t_flow_design=70`, `t_flow_min=65` (a low-temperature 70/40-era
  system).

`from_config` (`:56`) selects a preset when `heating_curve.preset` is set,
otherwise builds the explicit five-parameter curve. The curve lives on the
slack producer; a network with no `heating_curve` block runs at constant supply
temperature (`heating_curve` is `null` in the frame — several reference nets do
this, e.g. DESTEST).

### 4.2 Coupling to the solve and to the weather

Each tick, `_apply_step` evaluates the curve at the live ambient temperature and
writes the result straight onto the slack pump (`simulator.py:475-478`):

```python
t_amb = self.weather.t_amb(tick)
if self.heating_curve is not None:
    net.circ_pump_pressure.at[idx.slack, "t_flow_k"] = self.heating_curve.t_flow_k(t_amb)
```

The curve's `t_room_c` and `t_amb_design_c` are also copied into the weather
model's degree-hour parameters (`simulator.py:341-344`), so the same design
points anchor both the supply-temperature curve and the demand-scaling factor
(§8) — the physics stays self-consistent when the operator drags the weather
knob.

### 4.3 What the curve teaches: the loss / mass-flow trade-off

Lowering the supply temperature is not free. For a fixed heat demand
`Q = ṁ · cp · ΔT`, dropping the supply temperature at a fixed return shrinks
`ΔT`, so the mass flow `ṁ` must rise to compensate. Higher `ṁ` means higher
velocity, higher pressure drop, and higher pump power (`P_hyd = V̇·Δp` scales
with the square of flow). Against that, a cooler supply pipe loses less heat to
the ground. The M4 acceptance run makes both effects quantitative on the
Appendix A net at `t_amb = 0 °C`:

| Preset | `t_flow` | Losses | Plant mass flow | Pump P_el |
|---|---|---|---|---|
| 3G (110/70) | 95.0 °C | 31.10 kW (16.3 %) | 1.152 kg/s | 0.34 kW |
| 4G (70/65) | 68.1 °C | 21.35 kW (11.8 %) | 2.199 kg/s | 0.64 kW |

Losses drop 31 %, but mass flow rises ×1.91 and pump power nearly doubles —
the low-ΔT syndrome (Niedertemperatur-Syndrom), visible directly in the map's
velocity layer and the Overview KPIs.

---

## 5. Worst-point Δp control (Schlechtpunktregelung)

The plant pump's lift (`plift_bar`) is regulated to keep the *critical*
consumer supplied. `DpController` (`dp_control.py:50`) does this with **one
clamped proportional step per tick, after the solve** — deliberately no inner
re-solve loop.

### 5.1 The control law

```python
def step(self, plift_bar, dp_worst_observed_bar):
    self.last_dp_observed_bar = dp_worst_observed_bar
    if self.mode != "controlled" or dp_worst_observed_bar is None:
        return float(plift_bar)              # fixed mode, or blind → hold
    delta = self.k * (self.setpoint_bar - float(dp_worst_observed_bar))
    delta = max(-self.max_step_bar, min(self.max_step_bar, delta))
    return max(self.plift_min_bar, min(self.plift_max_bar, float(plift_bar) + delta))
```

That is: `plift += clamp(K·(dp_set − dp_worst), ±max_step)`, then clamp the
absolute lift. Defaults (`dp_control.py:54-59`):

| Parameter | Value | Role |
|---|---|---|
| `k` | 0.5 | proportional gain [1/tick] |
| `max_step_bar` | 0.05 | rate clamp per tick |
| `setpoint_bar` | 0.7 | Δp target (user band 0.3–2.0 bar, `SETPOINT_MIN/MAX_BAR`) |
| `plift_min/max_bar` | 0.1 / 10.0 | absolute pump-lift clamp (`PLIFT_MIN/MAX_BAR`) |

### 5.2 One step per tick — why no inner loop

The controller is invoked in `run_step` *after* the solve, warm-start and
collection (`simulator.py:569-576`):

```python
obs   = payload.get("observed_summary") or {}
plift = float(self.net.circ_pump_pressure.at[self.index.slack, "plift_bar"])
new_plift = self.dp_control.step(plift, obs.get("dp_worst_bar"))
if abs(new_plift - plift) > 1e-12:
    self.net.circ_pump_pressure.at[self.index.slack, "plift_bar"] = new_plift
```

The updated `plift` takes effect on the **next** tick's solve, so the pump
*reacts over ticks* like a real speed-controlled pump, and the engine stays at
one solve per tick — `O(1)` cost, not an iterate-to-fixed-point inner loop. The
M4 acceptance run drives the observed worst point from 1.953 bar to a 0.7 bar
setpoint in a strictly monotone 0.05 bar/tick descent, settling to 0.703 bar
after ~26 ticks, with pump P_el falling 0.39 → 0.14 kW.

The `fixed` mode ("ungeregelte Pumpe" / unregulated pump) holds `plift_bar`
wherever the user set it — the teaching contrast that shows the oversizing
penalty of a constant-lift pump. Direct pump setting in fixed mode goes through
`Simulator.set_plift` (`simulator.py:648-653`), clamped to the same
`plift_min/max` band.

### 5.3 Reads the observed layer only

The controller consumes **only** `observed_summary.dp_worst_bar` — the minimum
Δp over *metered* consumers — never ground truth. When no consumer is metered
(or every meter is still in its cold-start window in standard fidelity),
`dp_worst_bar` is `None` and the controller **holds** `plift`: holding *is*
"controlling on plant Δp", the behavior of a constant-Δp pump with no remote
sensor. When the true worst point is unmetered, the controller regulates on the
best *measured* Δp and the frame sets `controls.dp_control.blind_spot = true`.

This is a shared boundary: **this doc owns the controller algorithm and its
tuning; [OBSERVABILITY.md](OBSERVABILITY.md) owns why the measured worst point
can differ from truth and the meaning of the `blind_spot` flag as sensor-layout
meta-information.**

---

## 6. Equipment and dispatch models

### 6.1 Plant dispatch (Erzeuger)

pandapipes knows nothing about boilers or heat pumps — the slack is just a
pressure/temperature source. `PlantModel` (`producers.py:29`) is a *platform-
level dispatch model on top of the slack*: given the solved heat feed-in and
the live temperatures, `metrics(q_kw, t_hot_c, t_amb_c, t_ground_c)`
(`:39-53`) reports the electric/fuel side each tick.

| Kind | Formula | Notes |
|---|---|---|
| **boiler** (Kessel) | `p_fuel_kw = q / η` | `eta` default 0.95 (condensing modeled as constant η) |
| **chp** (BHKW / KWK) | `p_el_kw = pq_ratio · q` | heat-led, `pq_ratio` default 0.5 (P/Q 0.4–0.6) |
| **heat_pump** (Wärmepumpe) | `COP = η_g · T_hot / (T_hot − T_cold)`; `p_el_kw = q / COP` | see below |

The heat pump is the interesting one — a live-COP Carnot model in Kelvin
(`producers.py:42-50`):

```python
t_cold_c = t_amb_c if self.t_cold_source == "t_amb" else t_ground_c
t_hot_k  = t_hot_c + KELVIN
lift_k   = max(1.0, t_hot_k - (t_cold_c + KELVIN))   # guard: floor lift at 1 K
cop = self.eta_g * t_hot_k / lift_k
return {"cop": cop, "p_el_kw": q_kw / cop if cop > 0 else None, "t_cold_c": t_cold_c}
```

`T_hot` is the **live plant flow temperature** — so when the heating curve
lowers the supply temperature, the COP rises and the reported electric power
drops. `T_cold` is the live source temperature: `t_amb` for an air-source pump
or `t_ground` for a ground-source pump (`t_cold_source`). The Gütegrad `η_g`
(quality grade, default 0.5) scales the ideal Carnot COP to a realistic value;
the temperature lift is floored at 1 K so a source warmer than the sink cannot
blow the ratio up. This is the §1 temperature-lowering narrative made
quantitative: lowering network temperatures raises COP roughly 2–3 % per Kelvin.

The metrics are recomputed every tick from the solved feed-in and current
weather in `_collect` (`simulator.py:718-722`), so `producers[]` carries live
`cop` / `p_el_kw` / `p_fuel_kw` / `t_cold_c` as applicable alongside the always-
present `q_kw`, `t_flow_c`, `plift_bar`, `pump_el_kw`, `plant_kind`.

### 6.2 Buffer storage (Pufferspeicher)

A storage (`storage.py`, `BufferStorage` at `:46`) bridges one node's
supply/return pair with **two branches**, exactly one active per tick:

- **charge** — a `heat_consumer` (supply → return) with `qext_w` +
  `treturn_k = t_bottom_c`: draws charge power from the net, returns at the
  store's bottom temperature.
- **discharge** — a `circ_pump_const_mass_flow` (return → supply), `type="t"`,
  **no** `p_flow_bar`: pushes a fixed mass flow at `t_top_c` back into the
  supply. The pressure-free `type="t"` variant is mandatory — a `type="pt"`
  pump fixes the flow-junction pressure and fights the slack's pressure field,
  and every retry tier diverges (runtime-verified 2026-07-16).
- **idle** — the charge branch drops to the §3.2 floor pair (`IDLE_MDOT_KG_PER_S
  = 0.02`, `IDLE_QEXT_W = 100.0`) and the discharge pump goes out of service, so
  the stub pipes never reach zero flow.

The branch switching happens each tick in `_apply_step` (`simulator.py:496-519`).
`desired_state(dt_h)` (`storage.py:65-81`) applies power and capacity limits
before returning the active branch: a charge request is capped by remaining
room (`room_kwh / (eff·dt_h)`), a discharge request by available charge
(`avail_kwh · eff / dt_h`), and below `MIN_ACTIVE_KW = 1e-3` the store idles
itself — a full store stops charging on its own.

SoC integration is separated from the setpoint: `_integrate_storages`
(`simulator.py:655-680`) runs on the **solved** results, before the frame is
collected. Charge uses the realized `qext_w`; discharge uses
`ṁ·cp·(t_out − t_from)` from `res_circ_pump_mass`; idle records the standby
draw as a standing loss (SoC does not move). `integrate` (`storage.py:89-97`)
applies a one-way efficiency `eff = 0.95` (so round-trip is η² ≈ 0.90) and
clamps SoC to `[0, capacity]`. The discharge mass flow for a power request is
sized over the store's design spread, `P·1000 / (cp_nom · ΔT)` with
`cp_nom = 4190 J/(kg·K)` (`discharge_mdot`, `:83-87`) — a nominal sizing only;
the realized heat comes back from the solve.

### 6.3 The `MIN_QEXT_W` zero-flow floor

A `heat_consumer` with zero `qext_w` carries zero mass flow, and a zero-flow
branch is **singular** in pandapipes' heat-transfer matrix; the near-zero-load
regime is also exactly where `qext_w` + `treturn_k` control diverges (there is
no flow to control the return temperature of). The guard floors every
temperature-controlled consumer's total demand at `min_qext_w`
(`RTHEATFLOW_MIN_QEXT_W`, default 500 W, `config.py:48`).

Crucially the floor is **per-row and mask-aware** (`simulator.py:464-465`,
build-time at `network_builder.py:212-219`):

```python
floor = np.where(idx.mdot_mask, 0.0, self.settings.min_qext_w)
qext  = np.maximum(q_sh + p.q_dhw_w[:, tick], floor)
```

`mdot`-pair rows (bypasses and fixed-flow consumers) are **exempt**: they carry
a fixed `controlled_mdot_kg_per_s`, so there is no singularity risk and no need
to inflate their heat — a 100 W bypass stays 100 W. Only `treturn`/`deltat`
consumers, whose flow is derived from their heat draw, get the floor.

### 6.4 Bypass (Netzschluss-Bypass)

A bypass is the canonical mdot-pair element: a tiny fixed mass flow plus a small
standby heat, placed to keep a dead-end warm. `add_bypass` (`simulator.py:1047`)
defaults to `mdot = 0.02 kg/s`, `qext = 100 W` and tags the row `mdot_mask` so
the floor exempts it. Its standby heat is parked in the DHW slot
(`simulator.py:1075-1076`) so the weather override (§8) never rescales it — a
bypass's job is temperature maintenance, not weather-following demand.

---

## 7. The weather knob (degree-hour demand scaling)

One weather time series drives everything (`weather.py`, `WeatherModel` at
`:27`): ambient temperature feeds space-heating demand and the heating curve;
ground temperature feeds every pipe's `text_k`. The live override lets the
operator drag the outdoor temperature and see demand respond instantly.

The mechanism is the degree-hour factor:

```
f(T) = max(0, T_room − T) / (T_room − T_design)
```

Applied to the **space-heating part only** — domestic hot water (DHW) is
untouched, because DHW demand does not follow the weather. `scale_space_heating`
(`weather.py:64-82`):

```python
if self.override_t_amb_c is None:
    return q_sh                               # no override → profile unchanged
f_profile  = self.degree_hour_factor(self.t_amb_c[tick])
f_override = self.degree_hour_factor(self.override_t_amb_c)
if f_profile >= EPSILON:                      # ε = 0.05
    return q_sh * (f_override / f_profile)     # rescale the profile SH
return np.asarray(q_design_w) * f_override     # summer regime: rebuild from design
```

Two regimes, guarded by `EPSILON = 0.05`:

- **Normal:** the profile already has meaningful space heating
  (`f_profile ≥ ε`), so scale it by the ratio `f(T_override) / f(T_profile)`.
  This preserves the profile's diurnal shape.
- **Summer:** the profile's space heating is ~0 (`f_profile < ε`), so there is
  no ratio to scale — reconstruct the demand from the design load,
  `q_design · f(T_override)`. This lets an operator drag a summer day down to
  winter and get a physically plausible heating load out of a near-zero profile.

The override sets only the effective ambient temperature (`t_amb`, `:50-54`) —
`t_ground` is never overridden (ground temperature is a slow seasonal
quantity). Because the same `t_room` / `t_design` anchor both this factor and
the heating curve (§4.2), the supply temperature *and* the demand both respond
to the knob consistently. `_apply_step` calls `scale_space_heating` before
adding the untouched DHW and applying the floor (`simulator.py:462-466`). The
API clamps overrides to a plausible −30…45 °C band (422 outside).

---

## 8. Quasi-static fidelity and the exporter-only transient mode

### 8.1 What quasi-static means here

The live engine is **quasi-static**: every tick is an independent
steady-state solve. Hydraulics are stationary, and heat transfer is solved to
steady state for the current boundary conditions. There is **no thermal
transport delay** — when the plant raises its supply temperature, the far end
of the network sees the change in the *same* tick's solve, whereas a real
pipe would take minutes for the temperature front to travel. The warm start
(§2.5) carries the previous solution forward as an initial guess, but it does
**not** model thermal inertia; it only speeds convergence.

[ARCHITECTURE.md §4](ARCHITECTURE.md) is the canonical statement of this
limitation and why it is an honest, deliberate choice (the platform is a
teaching and operations tool, not a transient EN 13941 design study). This doc
owns the *mechanism* of the opt-in transient escape hatch.

### 8.2 The transient tiers

`solve_with_retry(net, iter_base, transient_ctx)` accepts an optional transient
context `{"dt": <simulated seconds per step>, "step": <monotonic counter>}`
(`simulator.py:102-125`). When present it **prepends two transient
bidirectional tiers** to the ladder:

```python
if transient_ctx is not None:
    base = dict(mode="bidirectional", iter=int(iter_base), transient=True,
                dt=float(transient_ctx["dt"]),
                simulation_time_step=int(transient_ctx["step"]))
    attempts = [base, {**base, "alpha": 0.5}] + attempts
```

Two facts are load-bearing:

- **`dt` is always passed explicitly.** `dt=None` crashes the numba path
  (pandapipes issue #787).
- **`simulation_time_step` is a monotonic counter; `0` starts the chain cold.**

Per-step chaining is the *same* mechanism `run_timeseries` uses internally: it
calls `pipeflow(..., transient=True, dt=..., simulation_time_step=i)` per step
and relies on `net["_pit"]` persisting between calls (its `initialize_pit`
snapshots the previous step's temperatures into the old pit when
`transient ∧ step ≠ 0 ∧ converged`). rtheatflow's standalone per-step calls
with the same kwargs were proven **bit-identical** to `run_timeseries`
(`max |Δ| = 0.0` for `t_k` and `p_bar` over six steps) in
`tests/test_transient_m7.py`. With inertia engaged, the far-end supply decays
(358.05 → 357.57 K over steps) instead of the quasi-static instantaneous jump —
the transport-delay story, > 1 K, pinned by test.

If every transient tier fails, the quasi-static ladder below is the automatic
fallback and the outcome reports `transient=False`.

### 8.3 Exporter-only, never the live loop

`run_step(step, day, solve_ctx)` (`simulator.py:523`) is the only path that
passes `transient_ctx`, and **the live engine never passes it** — `solve_ctx`
is `None` on every live tick, so the live loop is always quasi-static.
`sim.last_transient` records whether a transient tier actually converged
(`None` when the run was not a transient run, `:546-547`). The transient path is
reachable only through `BulkExporter` when `RTHEATFLOW_TRANSIENT=true`
(default `false`, `config.py:39`), which replays whole days offline into a
private recording and marks the pack transient. See
[ARCHITECTURE.md §4/§5](ARCHITECTURE.md) for the exporter and
[BENCHMARKS.md](BENCHMARKS.md) for the transient replay cost.

---

## 9. Summary of the physics contract

| Guarantee | Where |
|---|---|
| Supply/return pair expansion, build-once | `network_builder.py:116` |
| `text_k` explicit on every pipe (else ~4× fake loss) | `network_builder.py:151,481` |
| Bidirectional retry ladder, never `automatic` | `simulator.py:76-89` |
| Non-convergence → reuse last converged, `converged=false` | `simulator.py:577-581` |
| Warm start (res_junction → junction init) | `simulator.py:564-566` |
| Direction-aware loss (naive overcounts ~20 %) | `simulator.py:167-175` |
| Feed-in `ṁ·c̄p·ΔT` (enthalpy column reads +4.7 %) | `simulator.py:177-183` |
| Heating curve `t_flow(t_amb)`, 3G/4G presets | `heating_curve.py:28,50` |
| Δp control: one clamped step/tick, observed-only | `dp_control.py:63`, `simulator.py:569-576` |
| Plant dispatch: boiler/CHP/heat-pump live COP | `producers.py:39-53` |
| Storage: one branch/tick, SoC η=0.95 | `storage.py:65-97`, `simulator.py:655-680` |
| Zero-flow floor, mdot-rows exempt | `network_builder.py:212-219`, `simulator.py:464` |
| Weather override: degree-hour SH scaling, DHW untouched | `weather.py:64-82` |
| Quasi-static live; exporter-only transient chaining | `simulator.py:102-125` |

Every value here is what the code produces at master and what the known-answer
fixture and milestone acceptance runs assert. For the measurement projection
that controllers actually read, and the strict-mode honesty model, continue to
[OBSERVABILITY.md](OBSERVABILITY.md).
