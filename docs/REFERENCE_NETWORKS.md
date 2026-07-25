# Data Pipeline and Reference Networks

How a district-heating network becomes something the solver can run: the
five-file data contract, the profile toolbox that fills it, the catalog that
serves it, and every reference network shipped in `data/networks/`. This is the
rtheatflow analog of rtpowerflow's grid-extraction document — the "where does
the network come from" reference.

This document owns the **data contract** and the **network catalog**. For what
the solver does with a built net (retry ladder, warm start, derived
quantities) see `PHYSICS_AND_CONTROLS.md`; for the measurement projection and
the observer see `OBSERVABILITY.md`; for solve-time tables and per-net
performance see `BENCHMARKS.md`; for the system-level map see
`docs/ARCHITECTURE.md` (§3.2 covers the data contract at a glance — this
document is the detail behind it).

---

## 1. The pipeline at a glance

Every importer converges on one validated bundle, and `build_network` consumes
nothing else:

```
 five JSON files on disk                 pydantic v2                cross-doc            build-once
 ─────────────────────────               (models.py)               (data_loader.py)     (network_builder.py)
  network_structure.json  ─┐
  pipes.json               ├─▶ per-file  ─▶  NetInputs  ─▶  cross_validate()  ─▶  build_network()
  consumers.json           │   validate       (frozen        node refs / lengths     (net, ProfileArrays)
  producers.json           │                  dataclass)     horizons / one slack          │
  weather.json            ─┘                  net_inputs.py  reachability / dead-end       ▼
                                                                                    pandapipes net +
                                                                                    dense [n_elem, ticks]
```

Loading is a single call: `load_network(directory)` in
`src/rtheatflow/data_loader.py:56` reads the five files, validates each against
its pydantic model, assembles a `NetInputs`, then cross-validates. Any violation
raises `DataContractError` listing **every** finding, not just the first
(`data_loader.py:43`).

---

## 2. The five-file data contract

The contract is DH-native and single-sided: topology files describe **trench
nodes**, not the supply/return junction pair the solver actually simulates. The
builder expands the pair (see §3), so hand-authored files stay half the size.
All five models live in `src/rtheatflow/models.py`; every model subclasses
`_StrictModel` (`models.py:27`) with `extra="forbid"`, so an unknown key is a
hard error — typos never pass silently.

| File | Model | Top-level shape |
|---|---|---|
| `network_structure.json` | `NetworkStructure` | `{name, junctions[]}` |
| `pipes.json` | `PipesFile` | `{pipes[]}` |
| `consumers.json` | `ConsumersFile` | `{resolution_minutes, steps, consumers[]}` |
| `producers.json` | `ProducersFile` | `{producers[]}` |
| `weather.json` | `WeatherFile` | `{resolution_minutes, steps, t_amb_c[], t_ground_c[]}` |

### 2.1 `network_structure.json` — trench nodes

One entry per trench node (`StructureJunction`, `models.py:35`). The builder
auto-creates the `_s`/`_r` junction pair from each entry.

| Field | Type | Notes |
|---|---|---|
| `name` | str | unique across the file (`NetworkStructure._unique_names`, `models.py:46`) |
| `kind` | `"node"\|"consumer"\|"plant"\|"cabinet"` | default `"node"`; UI marker classification only |
| `geo` | `(lat, lon)` | **WGS84, Leaflet-native (lat, lon) order** |
| `pn_bar` | float > 0 | nominal pressure of the junction pair |

`geo` is `(lat, lon)`; the builder flips it to pandapipes' `(x, y) = (lon, lat)`
at `network_builder.py:142`. At least two junctions are required
(`Field(min_length=2)`).

### 2.2 `pipes.json` — trenches

One entry per trench (`PipeSpec`, `models.py:60`). The builder creates both a
supply and a return pipe from each.

| Field | Type | Notes |
|---|---|---|
| `from_node`, `to_node` | str | must reference structure nodes; self-loops rejected |
| `length_km` | float > 0 | trench length (both pipes get it) |
| `std_type` | str? | an ISOPLUS/pandapipes std type name … |
| `inner_diameter_mm`, `u_w_per_m2k`, `k_mm` | float? | … **or** explicit 0.14 custom parameters |
| `sections` | int ≥ 1 | discretization count (default 1; `sections ≈ v·dt` guidance in `ARCHITECTURE.md` §4) |
| `geometry` | `[(lat, lon), …]`? | optional street polyline for the map |

A model validator (`PipeSpec._std_type_xor_parameters`, `models.py:74`) enforces
the **std_type XOR custom-parameters** rule: passing `k_mm`/`u_w_per_m2k`
alongside a `std_type` is rejected, because in pandapipes 0.14 those silently
override the std-type value (SPEC §3.1). Without a std type, both
`inner_diameter_mm` and `u_w_per_m2k` are mandatory.

### 2.3 `consumers.json` — substations (profiles are the elements)

The file carries its own time base (`resolution_minutes`, `steps`) and a
`consumers[]` list where **row order is the element index**. Each `ConsumerSpec`
(`models.py:103`) is one `heat_consumer`.

| Field | Type | Notes |
|---|---|---|
| `node` | str | the trench node it sits on |
| `name` | str? | display name; defaults to `consumer_<node>` in the builder |
| `q_sh_w`, `q_dhw_w` | `list[float]` | the space-heating / DHW split (SPEC §4.5), length `steps` |
| `treturn_k` | `list[float]`? | partner option 1: return-temperature profile (pandapipes pair 5) |
| `deltat_k` | float > 0? | partner option 2: fixed ΔT (pair 4) |
| `controlled_mdot_kg_per_s` | float > 0? | partner option 3: fixed mass flow (pair 3; also the bypass shape) |
| `annual_kwh` | float ≥ 0? | drives the linear-heat-density KPI |
| `q_design_w` | float > 0 | summer-override formula + marker sizing |
| `t_supply_min_c` | float | default 60.0; UI DHW-hygiene warning |
| `building` | str? | archetype id (for the observer's prior, see `OBSERVABILITY.md`) |

The control setpoint `qext_w` is always `q_sh_w + q_dhw_w` (floored, §3). Exactly
**one** partner must be set (`ConsumerSpec._exactly_one_partner`, `models.py:125`).
The same validator guards two classic traps: negative demand values, and
`treturn_k` below 273.15 K — because the values are Kelvin and a pandapipes
tutorial famously passes `50` (= −223 °C). The third partner
`controlled_mdot_kg_per_s` is a documented extension beyond the SPEC §5 sketch
(`treturn_k | deltat_k` only); it is required to express appendix_a's consumer C
and the SPEC §3.2 canonical bypass.

### 2.4 `producers.json` — exactly one slack, plus secondaries

`ProducerSpec` (`models.py:177`) is a discriminated shape keyed on `kind`:

- **`slack`** — the single `circ_pump_const_pressure`. Requires `p_flow_bar`,
  `plift_bar`, `t_flow_k` (all > 0; `t_flow_k` > 273.15). May carry a
  `heating_curve` (`HeatingCurveConfig`, `models.py:157`) and a `dp_control`
  (`DpControlConfig`, `models.py:168`) block.
- **`heat_exchanger`** — a fixed feed-in. Requires `inner_diameter_mm` and a
  `qext_w` dispatch profile (positive; the builder negates it, since feed-in is
  negative `qext_w` by convention).
- **`pump_mass`** — a `circ_pump_const_mass_flow`. Requires `mdot_flow_kg_per_s`
  (scalar or `[steps]`) and `t_flow_k`. `p_flow_bar` is **optional**: absent →
  the pressure-free `type="t"` variant (the only mass-flow pump that coexists
  with the pressure slack); present → the expert `"pt"` booster
  (`_kind_specific`, `models.py:194`; enforced at build in
  `network_builder.py:252`).

`ProducersFile._exactly_one_slack` (`models.py:226`) enforces the single-slack
rule at parse time; `cross_validate` re-checks it belt-and-braces.

### 2.5 `weather.json` — the master input

`WeatherFile` (`models.py:241`): its own `resolution_minutes`/`steps` plus
`t_amb_c` and `t_ground_c`, each of length `steps`
(`WeatherFile._lengths`, `models.py:247`). Ambient drives the heating curve and
the degree-hour demand override (see `PHYSICS_AND_CONTROLS.md`); ground drives
every pipe's `text_k`.

### 2.6 `NetInputs` — the one importer contract

`src/rtheatflow/net_inputs.py:20` is a `frozen=True` dataclass bundling the five
validated models plus two derived properties: `total_minutes` (`steps ×
resolution_minutes`) and `n_days` (`total_minutes // 1440`). It is immutable on
purpose — the catalog can share one cached instance across the preview, loadgen
and apply paths without defensive copies, because nothing downstream mutates it
(loadgen rebuilds new models; runtime CRUD writes to the net/profiles, never to
`NetInputs`).

### 2.7 Cross-document validation

`cross_validate(inputs)` (`data_loader.py:81`) runs the checks that no single
file can see. All findings accumulate into one `DataContractError`.

| Check | Lines | What it catches |
|---|---|---|
| Node references | `data_loader.py:86` | a pipe/consumer/producer pointing at an unknown node |
| Array lengths | `data_loader.py:98` | `q_sh_w`/`q_dhw_w`/`treturn_k`/`qext_w`/`mdot` not equal to `steps` |
| Horizon alignment | `data_loader.py:122` | consumer horizon ≠ weather horizon, or not a whole number of days |
| Exactly one slack | `data_loader.py:132` | zero or many pressure slacks |
| Reachability | `data_loader.py:137` | a consumer/producer node not reachable from the slack (BFS over the trench graph, `_bfs` `data_loader.py:181`) |
| Zero-flow guard | `data_loader.py:160` | an isolated node (no pipe), or a **dead-end node with no consumer/producer/bypass** — a live branch with nothing attached is hydraulically singular (SPEC §3.2) |

The zero-flow guard is the subtle one: degree-1 trench nodes must host a
consumer, producer, or bypass. The fix a converter reaches for is the canonical
bypass — a consumer with the `controlled_mdot_kg_per_s + qext_w` pair (Schutterwald
does exactly this for its consumer-less leaves, §7.4).

---

## 3. Supply/return expansion in the builder

`build_network(inputs, steps_per_day=1440, min_qext_w=500.0)`
(`network_builder.py:116`) constructs the pandapipes net **once** and returns
`(net, ProfileArrays)`. Each simulation tick only overwrites element values from
the dense arrays and re-solves; the net is never rebuilt (SPEC §3.4). Two
records ride along: `NetIndex` (`network_builder.py:45`, the element-index
bookkeeping) and `ProfileArrays` (`network_builder.py:88`, dense
`[n_elements, ticks]`).

### 3.1 One node → a junction pair; one trench → two pipes

```
   structure "n1"                         net junctions
   geo (lat, lon)      ──build──▶     n1_s  (supply)   tfluid_k = slack t_flow_k
                                      n1_r  (return)   tfluid_k = slack t_flow_k

   pipe n0 → n1        ──build──▶     supply pipe:  n0_s → n1_s   (trench{i}_s)
   length_km, DN                      return pipe:  n1_r → n0_r   (trench{i}_r, reversed toward plant)
```

Every trench node expands into `<name>_s` / `<name>_r`, both initialized with the
**supply temperature** (slack `t_flow_k`) — this materially helps thermal
convergence (`network_builder.py:138`). Every trench expands into a supply pipe
(from→to) and a return pipe (to→from, reversed), sharing geometry and DN
(`network_builder.py:154`). `heat_consumer` elements bridge `junction_supply[node]
→ junction_return[node]` (`network_builder.py:203`); the slack and secondaries
bridge return→supply at the plant node (`network_builder.py:231`).

### 3.2 The three build-time invariants

- **`text_k` on every pipe, explicitly.** The build-time value is
  `t_ground_c[0] + 273.15` (`network_builder.py:151`). The signature default `0`
  means 0 K ambient → roughly 4× fake losses (SPEC Appendix B item 4); the
  simulator refreshes `text_k` per tick from the ground series (see
  `PHYSICS_AND_CONTROLS.md`).
- **Zero-flow floor.** Total demand is floored at `min_qext_w` for
  temperature-controlled consumers (`network_builder.py:202` at build, and the
  whole-array floor at `network_builder.py:209`). Mass-flow-pair rows (bypasses,
  `controlled_mdot` consumers) carry a **fixed** flow and keep their configured
  standby `qext` — a 100 W bypass stays 100 W (`floor = np.where(mdot_mask, 0.0,
  min_qext_w)`, `network_builder.py:213`). The default 500 W is a deliberately
  generous solver guard; validation runs override it (DESTEST uses 62 W, §7.3).
- **Staircase resampling.** File resolution → tick resolution is piecewise-constant
  (`_resample_staircase`, `network_builder.py:109`): `idx = arange(n_ticks) *
  len(src) // n_ticks`. It preserves energy sums and DHW peak magnitudes at the
  cost of 15-minute stair-steps, and is the documented choice from SPEC §4.5
  "pick one".

`NetIndex` guards the "profiles-as-definitions" coupling explicitly rather than
assuming it: it stores per-consumer partner masks (`treturn_mask` / `deltat_mask`
/ `mdot_mask`), the `q_design_w`/`t_supply_min_c` arrays, the pipe supply/return
index arrays, and a `producer_meta` list. Producer wire ids are platform-unique
`pid`s (`next_pid`, `network_builder.py:84`) because pandapipes element indices
are per-component-table and collide across kinds (slack 0 vs heat_exchanger 0).

---

## 4. The profile toolbox

The demand numbers in `q_sh_w`/`q_dhw_w` come from a committed archetype cache
under `data/profiles/`, produced offline by `scripts/generate_profiles.py` and
cut to size at runtime by `src/rtheatflow/consumers.py`.

### 4.1 `generate_profiles.py` — the archetype year

Two independent physics-based generators feed the `q_sh_w` / `q_dhw_w` split
(`scripts/generate_profiles.py`):

- **Space heating: demandlib VDI 4655.** `vdi_space_heating_w`
  (`generate_profiles.py:45`) runs a `vdi.Region` for DWD TRY zone 4 (Potsdam
  reference climate) at 15-minute resolution, extracts the `Q_Heiz_TT` column,
  and rescales it **exactly** to the archetype's annual target (VDI's own scaling
  is close but not exact). Deterministic typical days — no stochasticity.
- **DHW: OpenDHW stochastic draw-offs.** `opendhw_dhw_w`
  (`generate_profiles.py:73`) generates DHWcalc-style draw-offs at 60 s from
  liters/person/day, resamples to 15 min, and converts to heat at ΔT = 35 K.
  This is genuinely stochastic, so **several seeds** are drawn per archetype so
  co-located buildings never draw synchronously.

The shipped catalog is three archetypes (`ARCHETYPES`, `generate_profiles.py:32`):
`EFH_ALT_4P` (unrenovated single-family, 18 MWh/a SH), `EFH_SAN_4P` (KfW-renovated,
9 MWh/a), and `MFH_ALT_10WE` (10-unit apartment block, 95 MWh/a). Each JSON file
carries `q_sh_w` (one year) and `q_dhw_w_variants` (`DHW_SEED_VARIANTS = 3`
realizations), plus `q_design_w` and annual sums; an `index.json` lists all
archetypes.

**Seed-salting** (`generate_profiles.py:117`) is the key reproducibility trick:
the per-variant seed is `zlib.crc32(f"{archetype_id}:{i}")`, so different
archetypes never share a draw pattern and the cache regenerates bit-identically
across runs.

### 4.2 `consumers.py` — cutting one consumer out of the year

`ArchetypeLibrary` (`consumers.py:42`) wraps the cache (lazy `index`, LRU-cached
per-archetype loads via `_load_archetype`, `consumers.py:78`).
`archetype_profile` (`consumers.py:98`) is the workhorse used by both runtime
placement (`POST /consumer`) and loadgen:

1. **`pick_day`** (`consumers.py:89`) — pick a day of the archetype year by
   space-heating day-sum percentile: `0.9` = a properly cold winter day, `0.5` =
   shoulder, `0.05` = summer.
2. **Variant rotation** — select a DHW variant (`variant % len(variants)`) so
   identical buildings desynchronize.
3. **Seeded jitter** — a ±30-minute time shift (`rng.integers(-2, 3)` at 15-min
   resolution) and a 0.92–1.08 amplitude scale (`consumers.py:127`).
4. **Staircase-resample** onto the network's `resolution_minutes`.

Everything is deterministic given `(archetype, seed, variant, percentile)`, so
scenario recipes replay bit-identically. `constant_profile` (`consumers.py:145`)
is the teaching alternative — a flat load, no DHW — used by `POST /consumer` with
`q_kw`.

### 4.3 `loadgen` — assigning archetypes onto a network

`src/rtheatflow/loadgen/__init__.py` maps archetypes onto an existing network's
consumer nodes and produces a drop-in replacement `consumers.json` plus a matching
`weather.json`. `LoadgenPolicy` (`loadgen/__init__.py:51`) is the NetzStudio
column-2 control: `archetypes` subset, `mode` (`round_robin`/`random`), `seed`,
`scale`, `day_percentile`, `jitter`, `dhw_variants`, `temperature_preset`.

`assign` (`loadgen/__init__.py:70`) walks the network's consumers, assigns an
archetype per `mode`, cuts a profile with a per-consumer salted seed (`seed + i *
101`), and picks the return temperature from the preset table `TRETURN_C`
(`loadgen/__init__.py:39`; 3G returns hotter than 4G). It returns:

- `consumers_doc` / `weather_doc` — drop-in file contents,
- `assignments` — the per-node table for the UI,
- `kpis` — design/peak/mean/annual load, trench km, and the **linear heat
  density** [MWh/(m·a)] with the ≥1–1.5 viability rule of thumb,
- `load_kw` / `duration_kw` — chronological and load-duration curves for the
  Sparkline.

The clever bit is `_weather_doc` (`loadgen/__init__.py:166`): the ambient series
is **re-derived by inverting the §4.5 degree-hour factor** from the chosen
archetype day. The day-mean space-heating fraction `f = day_sh.mean() /
q_design` maps back to a mean ambient `t_mean = T_ROOM − f·(T_ROOM − T_DESIGN)`
(with `T_ROOM = 20`, `T_DESIGN = −12`), plus a diurnal sinusoid (warmest at
14:00). So the generated weather is self-consistent with the generated demand —
the override math holds. `apply_policy` (`loadgen/__init__.py:198`) wraps `assign`,
couples the heating-curve preset onto the slack when a `temperature_preset` is
set, and re-runs `cross_validate` on the result.

---

## 5. The network catalog

`src/rtheatflow/network_catalog.py` makes rtheatflow a pure *consumer* of
networks: it lists them and loads a chosen one through the five-file contract on
demand.

### 5.1 Sources: manifest + user scan

`NetworkCatalog.__init__` (`network_catalog.py:44`) takes a manifest, a
networks dir, and a user dir. The library manifest is
`data/network_library.json` (`_load_manifest`, `network_catalog.py:59`) — a
hand-maintained list (`{id, name, character, nodes, trench_km, dir}` per entry;
"character" is `rural`/`benchmark`/`meshed`/`abstract`). A manifest-less fallback
(`_scan_dir`, `network_catalog.py:69`) treats every five-file directory as an
entry.

Since M6 the catalog also scans `data/user_networks/` (`rescan_user`,
`network_catalog.py:76`): every subdirectory with a `network_structure.json`
becomes `user_<dirname>` with `source="user"`. The scan is cheap (two small JSON
reads for the list stats) and re-runs lazily when an unknown `user_*` id is
looked up (`has`, `network_catalog.py:113`), so `POST /networks/import` and
hand-copied bundles appear without a restart. Vanished user directories are
dropped on the next rescan.

### 5.2 The stale-cache fingerprint

`get_inputs(network_id, refresh=False)` (`network_catalog.py:149`) caches the
parsed `NetInputs` per id — but validates the cache against an on-disk
**fingerprint** first. `_disk_state` (`network_catalog.py:128`) captures
`(filename, st_mtime_ns, st_size)` for each of the five contract files; if the
current fingerprint differs from the one captured at load time, the cache entry
is dropped and the files are re-read. The fingerprint is captured **before** the
load, so a file rewritten mid-load yields a mismatch on the next access
(conservative re-read).

This closed a real bug (2026-07-17): before the fingerprint, regenerating
`data/networks/verbier/` on disk left a running backend serving the old geometry
from cache — only a process restart re-read it. The `/config/apply` path now also
passes `refresh=True` unconditionally (`network_builder`… the apply is rare and
`engine.reconfigure` rebuilds the whole Simulator, so a JSON re-parse is
negligible; `api/networks.py:192`). Repeated identical previews still hit the
cache.

`preview` (`network_catalog.py:172`) computes net-free stats for
`GET /networks/{id}` — trench km, design load, annual MWh, linear heat density,
plant summary — without building a pandapipes net.

---

## 6. `appendix_a` and `demo_dorf` — the in-repo networks

Two networks are generated in-repo rather than converted from an external source;
their provenance lives in the generator scripts, not a `DATASET.md`.

### 6.1 `appendix_a` — the known-answer fixture

`data/networks/appendix_a/` is the SPEC Appendix A reference loop, hand-authored
as five files. Four nodes on an abstract line (`n0` plant → `n1` → `n2` → `n3`),
three ISOPLUS trenches (DRE100/DRE80/DRE50 STD), and three consumers that
deliberately exercise **all three partner types**:

| Consumer | Node | Partner | q_design |
|---|---|---|---|
| A | n1 | `treturn_k` (pair 5) | 80 kW |
| B | n2 | `deltat_k` (pair 4) | 50 kW |
| C | n3 | `controlled_mdot_kg_per_s` (pair 3) | 30 kW |

The slack is a plain 6 bar / plift 2 bar / 358.15 K (85 °C) pressure pump with
**no heating curve** — so the platform never touches `t_flow_k` and the answer is
deterministic. This is the fixture the test suite pins the §11 known-answer
numbers against (A mdot 0.674357, B ΔT 30.0000, C mdot 0.2500, losses 27.258 kW,
q_feed 187.182 kW, balance −0.04 %); it is also the default validation network the
API lifespan can load explicitly. Because it is abstract, its `geo` coordinates
(48.0 N, 7.8 E) are placeholder — it is not a map demo.

### 6.2 `demo_dorf` — the map demo

`scripts/generate_demo_dorf.py` builds a hand-crafted rural village on real
geography (~49.004 N, 8.397 E, north of Karlsruhe): a `Heizzentrale` plant, 11
consumers (6× EFH_ALT, 3× EFH_SAN, 2× MFH) along a main street with two side
lanes, 15 nodes, 0.953 km trench, 182 kW design. It exists so the Leaflet map has
something real to draw (SPEC §12 M3).

Its construction is the template every later converter follows:

- Demands cut from the archetype cache at the 90th-percentile winter day
  (`pick_demo_day`, `generate_demo_dorf.py:141`), DHW variants rotating, seeded
  ±30-min shift + amplitude jitter.
- Weather inverted from the day's SH level via the degree-hour factor
  (`generate_demo_dorf.py:196`), plus a diurnal sinusoid.
- Pipe DNs sized from aggregated downstream design load at ΔT = 30 K to ≤ 1 m/s,
  choosing the smallest ISOPLUS STD type (`pick_std_type`,
  `generate_demo_dorf.py:129`).
- Archetype-specific return temperatures (55/50/45 °C) so the return-temperature
  map layer has something to show.
- A 3G sliding heating curve (85/55, `n = 1.3`).

The honest teaching point: its annual-energy linear heat density is
0.39–0.46 MWh/(m·a), **below** the ≥1 viability rule — exactly what the NetzStudio
viability marker is meant to flag for a sparse rural net.

---

## 7. The converted reference networks

Four reference networks are converted offline-deterministically from vendored
published sources under `data/sources/<id>/` (each with its license file). Each
carries a `data/networks/<id>/DATASET.md` recording provenance, conversion
decisions, and its validation record — the netzsim convention. **Full validation
numbers live in each `DATASET.md`; solve-time performance lives in
`BENCHMARKS.md`.** The summaries below give the shape and the load-bearing
conversion decisions.

### 7.1 DESTEST — the validated benchmark (8 / 16 / 32 buildings)

`scripts/convert_destest.py` → `destest_16`, `destest_8`, `destest_32`.

- **Source & license:** IBPSA Project 1 "DESTEST" common exercises, network
  CE_1 (modified BSD-3), vendored under `data/sources/destest/`.
- **The CSV-vs-Table-6 discovery.** The repo's `Pipe_data.csv` and the official
  case-description **Table 6 disagree** on diameters, insulation thicknesses, and
  main-line lengths (e.g. h–i: 36 m vs 26.83 m). The DATASET.md establishes with
  evidence that every published CE 0 / CE 1 tool models **Table 6**: published
  CE 0 pressure drop i→e is 22.4–25.4 kPa (Table 6 computes ~19–21 kPa, the CSV
  ~7 kPa), and the published i–h supply-line loss of 314–326 W matches Table 6's
  layered U′ (~320 W) but not the CSV's. So `destest_16` is built from a vendored
  **Table 6 transcription** (`Pipe_data_16_case_description_table6.csv`, read by
  `pipes_16`, `convert_destest.py:186`); the 8/32 variants use the repo CSVs and
  have **no published aggregates** — they are catalog examples, not validation
  cases.
- **Loss model.** The CSV's flat "U-value 0.035 W/mK" is a design-tool constant
  (hard-coded in upstream `Pipedimensioning.py`) and is **ignored**. The real
  model is a layered cylinder: PE-X wall (λ = 0.35, ISO 15875-2 series S5, SDR 11)
  + insulation (λ = 0.026), converted to a per-metre U′ (`u_per_metre`,
  `convert_destest.py:85`) and then to pandapipes' per-area convention on the
  **inner** diameter (`u_w_per_m2k = U′/(π·ID)`, `convert_destest.py:92`; the DO
  fallback verified in pandapipes source).
- **Case rules encoded.** Constant 70 °C source (slack, no heating curve);
  substations hold primary ΔT = 30 K (`deltat_k` pair); constant 10 °C boundary in
  both weather channels; identical SFH profile on all buildings, first 7 days
  (the CE 1 window), 600-s steps.
- **The bypass ≡ floor trick.** DESTEST's 1.77 kg/h minimum bypass ≡ **62 W at
  ΔT 30 K**, so the validation suite runs `min_qext_w = 62` — the platform's
  zero-flow floor **is** the DESTEST bypass. The app default (500 W) is untouched.
- **Geodata.** Local Cartesian metres, no CRS → projected onto a synthetic
  on-land anchor NE of the demo region (`local_to_wgs84`, `convert_destest.py:79`);
  the earlier over-water anchor rendered confusingly.
- **Validation (see DATASET.md / BENCHMARKS.md):** CE 0 supply temp at SD1 lands
  inside the published band (the pure loss-physics pin); CE 1 7-day injection
  inside the published band, with a documented quasi-static night-loss excess
  bounded by the bypass-enthalpy ceiling. DIMOSIM is excluded as a published
  outlier.

### 7.2 Schutterwald — the real-town flagship

`scripts/convert_schutterwald.py` → `schutterwald` (208 nodes, 2.626 km,
44 substations).

- **Source & license:** two pandapipes 0.14.0 example nets (BSD-3), vendored
  under `data/sources/schutterwald/`: `sw_heat.json` (the STANET-exported DH net)
  and `gas_net_schutterwald_1bar.json` (the town's gas net, the load donor).
- **The reversed-geo trap.** Junction/pipe `geo` columns store **[lat, lon] —
  reversed vs the GeoJSON spec**, which happens to be this platform's native
  order, so they are taken as-is and range-asserted (`geo_point`,
  `convert_schutterwald.py:117`). `junction_geodata` x/y is EPSG:31467 (Gauß-Krüger
  zone 3), used only for house→substation proximity.
- **The dead-diameter-column trap.** Every source pipe carries
  `inner_diameter_mm = 800` and `u = 10` — dead export columns. Ignored; DNs are
  re-sized from aggregated downstream design load on the trench tree
  (ṁ = q_design/(cp·40 K), smallest `ISOPLUS_DRE*_STD` with v ≤ 1.2 m/s,
  `pick_std_type`, `convert_schutterwald.py:90`).
- **Topology contraction.** 34 zero-length pipes per side (STANET stubs) and the
  two fully-open DN200 feeder valves are merged via union-find
  (`convert_schutterwald.py:100`), collapsing 244 supply junctions → 208 trench
  nodes, 241 pipes → 207 trenches. The heat net is a perfect `return_<name>`
  mirror, so only the supply side is converted (the builder recreates the return).
- **The gas-net load donor + 50 m service area.** Each of 1506 gas houses is
  assigned to its nearest heat substation in EPSG:31467; houses farther than
  `SERVICE_CUTOFF_M = 50` m stay off the DH net (`convert_schutterwald.py:66`).
  This is a **documented deviation** from naive all-houses aggregation: the gas net
  covers the whole town (33.4 GWh/a → an incoherent 12.7 MWh/(m·a) on 2.6 km),
  whereas the 50 m service area yields 155 houses ⇒ 3.52 GWh/a ⇒ LHD
  1.34 MWh/(m·a), squarely in the viable band. Gas → heat is
  `m³/a × 10 kWh/m³ × 0.9`; substations with no house in reach keep the sw_heat
  fallback demand. Archetype-by-annual-energy, scaled to target, 90th-percentile
  winter day, 3G preset.
- **Leaf bypasses.** Consumer-less leaves get the canonical SPEC §3.2 bypass pair
  (0.02 kg/s + 100 W, `convert_schutterwald.py:304`) so the zero-flow guard passes.
- **Validation (see DATASET.md):** all 96 winter-day steps solve at retry tier 1,
  balance ≤ 0.075 %, v_max 0.98 m/s; winter-day loss 7.5 %, annualized ≈13 % —
  inside the 8–20 % plausibility band for a 3G net at this density.

### 7.3 Verbier — the measured meshed network

`scripts/convert_verbier.py` → `verbier` (676 nodes, 681 trenches, 12.05 km,
6 loops, 150 substations, 2 plants).

- **Source & license:** the OpenDHN dataset (Boghetti & Kämpf, Idiap/EPFL,
  CISBAT 2023), Zenodo DOI 10.5281/zenodo.10793816, **CC BY 4.0**, vendored under
  `data/sources/verbier/`.
- **The AGPL caveat.** The authors' pydhn simulation code is AGPLv3 and was
  **deliberately not consulted** — the entire conversion derives from the Zenodo
  data + README alone. This is stated in both the converter docstring and the
  DATASET.md.
- **Meshed, and lossless single-sided.** Supply and return sides are a perfect
  mirror (identical base pairs, lengths **and** diameters), so the single-sided
  contract is lossless. Unlike the tree networks, this one has 6 independent loops
  — the meshed-solver stress case.
- **Measured validation.** Consumers use CASE1 measured power as fixed loads +
  measured return temperatures (`qext_w + treturn_k` pair,
  `convert_verbier.py:146`); the measured **supply** temperatures are never used
  as inputs — they are the validation target (see `OBSERVABILITY.md` for the
  measured-layer semantics). Per-pipe U is the insulation annulus only
  (`u_w_per_m2k`, `convert_verbier.py:95`; steel wall + casing < 1 %, neglected).
- **Two plants, single-slack rule.** HS0 is the pressure slack (measured 81.11 °C);
  HS1 is a `pump_mass` with measured 5.03 kg/s + 81.81 °C and **no `p_flow_bar`** →
  the pressure-free `type="t"` variant, because a second pressure-fixing node
  over-determines the loop. This drove the **contract extension** making
  `p_flow_bar` optional for file-defined `pump_mass` (`convert_verbier.py:161`;
  the current `models.py:210` `_kind_specific` reflects it), mirroring the live-API
  rule — no pre-existing file used `pump_mass`, so no behavior changed.
- **The synthetic-anchor geo lesson: land, not water.** Coordinates are
  anonymised local metres (z ≡ 0) → projected onto WGS84 with the network centroid
  over Verbier village (`to_wgs84`, `convert_verbier.py:76`). The real routing is
  undisclosed, so the placement is synthetic — but an alpine network belongs on
  alpine terrain, not in Lac Léman (the same lesson DESTEST learned).
- **Boundary assumption.** Not in the dataset → constant 5 °C ground for all
  pipes (the 55 aerial pipes share it, per the single-`text_k` convention); the
  DATASET.md documents ±5 K ⇒ ∓7 % on losses.
- **Validation (see DATASET.md / BENCHMARKS.md):** the meshed net needs retry
  **tier 2** (α = 0.5) — tier 1 fails on the 1352-junction mesh with the type-t
  second pump. Plant-level totals within −0.6 % of measured; 150 substation supply
  temperatures median |ΔT| 0.83 K, p90 2.68 K, with named outliers analyzed in the
  DATASET.md.

### 7.4 Catalog summary

From `data/network_library.json`:

| id | character | nodes | trench km | Role |
|---|---|---|---|---|
| `demo_dorf` | rural | 15 | 0.953 | map demo (default network) |
| `appendix_a` | abstract | 4 | 1.0 | known-answer fixture |
| `schutterwald` | rural | 208 | 2.626 | real-town flagship |
| `destest_16` | benchmark | 25 | 0.39 | **validated** (Table 6) |
| `destest_8` | benchmark | 13 | 0.216 | catalog example |
| `destest_32` | benchmark | 49 | 0.792 | catalog example |
| `verbier` | meshed | 676 | 12.05 | measured meshed validation |

---

## 8. Scenarios — recipes, not snapshots

`src/rtheatflow/scenarios.py` persists the configured live setup as small,
hand-editable JSON. A scenario is a **recipe**: the network id + the deterministic
loadgen policy + the runtime mutations layered on top (producers, storages,
consumer/bypass ops) + heating-curve/Δp/plant config + weather override + the
engine clock. Because every ingredient is deterministic (seeded profiles,
recorded op log), replaying the recipe reproduces the setup exactly, and the file
stays readable — deliberately editable for teaching and demo prep.

`ScenarioStore` (`scenarios.py:29`) is a thin directory reader/writer; the slug of
the name is the id, so the same name overwrites (iterate on a scenario in place).
The interesting logic is the API replay (`api/scenarios.py`): `scenarios_save`
serializes only **runtime** producers (`meta["runtime"]`; file-defined ones reload
with the network), storage configs, the consumer op log, controller config, and
the M5 sensor placement (meters saved by consumer **name**, since element ids
shift across replay). `scenarios_load` (`api/scenarios.py:120`) is **tolerant per
entry**: it swaps the network, then replays each runtime layer, skipping any op
that no longer matches with a warning rather than a partial 500 (the accepted
non-atomic blueprint pain point, SPEC §9.3). Two reference scenarios ship
(`demo-dorf-3g-winter` / `demo-dorf-4g-vergleich`, same seed 42 for direct
comparison).

---

## 9. Adding a new reference network

The established path (four converters have walked it):

1. **Vendor the source.** Put the raw data and its license under
   `data/sources/<id>/`. Never fetch at convert time — conversion must be offline
   and deterministic.
2. **Write `scripts/convert_<id>.py`.** Emit the five contract files into
   `data/networks/<id>/`. Reuse the conventions: supply side only (the builder
   mirrors the return), DN sizing from downstream design load, weather inverted
   from the demand day, canonical bypass on consumer-less leaves, synthetic geo
   anchored **on land** if the source has no CRS. Round floats, `ensure_ascii=False`.
3. **Validate against the contract.** The converter's own output must load
   cleanly — `load_network` will report node-ref, length, horizon, one-slack,
   reachability, and dead-end violations (§2.7). Fix the converter, not the files.
4. **Write `data/networks/<id>/DATASET.md`.** Record provenance + license,
   conversion decisions, every documented deviation, and the validation record
   (headline accuracy here; solve-time performance goes to `BENCHMARKS.md`).
5. **Register in `data/network_library.json`.** Add the manifest entry
   (`id`, `name`, `character`, `nodes`, `trench_km`, `dir`) — hand-maintained by
   repo convention. It then appears in `GET /networks`, previews, apply, and
   NetzStudio automatically.
6. **Add a test.** A load/solve/balance test at minimum (the 8/32 DESTEST
   variants); a validation test against published or measured data if the source
   carries reference results (`test_destest_validation.py`, `test_schutterwald.py`,
   `test_verbier.py`).

A network imported at runtime via `POST /networks/import` takes a shorter path —
a five-file bundle written to `data/user_networks/<slug>/`, validated by actually
loading it, and surfaced by the lazy user-dir rescan (§5.1) — but a *shipped*
reference network wants the full converter + DATASET.md + manifest + test so it is
reproducible from its vendored source.
