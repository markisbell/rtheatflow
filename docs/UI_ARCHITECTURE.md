# Frontend Architecture (ui/)

This document is the deep dive into the browser client under `ui/`. It is the
frontend counterpart to `docs/ARCHITECTURE.md` §7 (which gives the one-paragraph
overview) and covers the parts that overview intentionally omits: the component
inventory, the state model, the WebSocket lifecycle, the wire-type mirror, the
color-scale system, and the raw-Leaflet render pattern.

Where this doc touches the *meaning* of the three data layers (Realität/Reality,
Gemessen/Measured, Schätzung/Estimated) it defers to `OBSERVABILITY.md`; where it
touches the physical meaning of a color threshold or the Δp controller it defers
to `PHYSICS_AND_CONTROLS.md`. Here we describe only what the UI *renders* and
*how*.

The UI is a structural port of the blueprint's `LivePowerFlow`/`NetzStudio`
frontend (netzsim / rtpowerflow) re-domained to district heating (Fernwärme).
Comments in the source that say "blueprint" mark verbatim or near-verbatim ports.

---

## 1. The stack, and what is deliberately absent

| Layer | Choice | Version (`ui/package.json`) |
|---|---|---|
| Framework | React | `^18.3.1` (StrictMode) |
| Language | TypeScript, `strict` | `~5.6.3` |
| Bundler / dev server | Vite | `^6.0.5` |
| Map | **raw Leaflet** (no `react-leaflet`) | `^1.9.4` |
| i18n | i18next + react-i18next | `^25.3.0` / `^15.6.0` |
| Tests | vitest | `^3.2.0` |

What is **not** in the tree, on purpose:

- **No chart library.** `components/Sparkline.tsx` is a hand-rolled SVG
  area/line chart (~150 lines). Daily profiles, load-duration curves and live
  Δp traces all go through it.
- **No state-management library** (no Redux/Zustand/Jotai). State is lifted into
  `App.tsx` and passed down as props; the live physics stream is a single
  React hook (§5, §6).
- **No router.** The two top-level areas (Bereich: *Live* / *NetzStudio*) are a
  `useState<Tab>` in `App.tsx`, not URL routes.
- **No `react-leaflet` wrapper.** `MapDiagram.tsx` drives the Leaflet imperative
  API directly through refs. This is the single most load-bearing decision in
  the frontend (§10): it is what lets the map restyle 10 frames/second without
  React reconciling thousands of vector layers.

`tsconfig.json` runs `strict`, `noUnusedLocals`, `noUnusedParameters`, and
`noFallthroughCasesInSwitch`. `noEmit` is set — `tsc` is used purely as a type
gate. The production build is `tsc && vite build` (`package.json`), so a type
error fails the build; there is no untyped escape hatch.

---

## 2. Module map (`ui/src/`)

```
main.tsx                 React root (StrictMode), imports i18n + styles
App.tsx                  Topbar shell: menus, tabs, the two segmented controls,
                         lifted LiveView state, network chip, DE/EN switch
api.ts                   Typed /api client for the backend routes + wsUrl()
types.ts                 Wire types mirroring the backend contract exactly
useStepStream.ts         The /ws hook: latest StepResult, reconnect, StrictMode guard
scales.ts                Domain-anchored color/size ramps + the UNOBSERVED greys
i18n.ts                  DE (authoring) + EN resources; `const en: typeof de` parity
views/
  LiveHeatFlow.tsx       Map + resizable sidebar + transport bar; the view splice
  NetzStudio.tsx         3-column network catalog → loadgen policy → preview/apply
components/
  MapDiagram.tsx         Raw-Leaflet map: build-once layers, restyle per frame
  OverviewSection.tsx    Übersicht KPI aggregates, per view mode + estimate quality
  WorstPointSection.tsx  Schlechtpunkt Δp cockpit (mode, setpoint, trace)
  MeasurementPanel.tsx   Coverage bars, presets, full/standard fidelity toggle
  HeatingCurveSection.tsx  Heizkurve editor + 3G/4G presets
  WeatherSection.tsx     Ambient-temp knob → debounced weather override
  EquipmentControls.tsx  Ctrl-click "pinned" element detail sections + config knobs
  ElementMenu.tsx        Right-click context menu (place/remove/config grammar)
  Sparkline.tsx          Hand-rolled SVG chart
  Section.tsx            Collapsible side-panel section wrapper + <Stat>
scales.test.ts           vitest: ramp anchors + unknown-grey pin (13 tests)
Sparkline.test.ts        vitest: chartExtent / axisTicks regressions (7 tests)
```

The whole application is one bundle served either by the Vite dev server (port
5174) or by nginx in the Docker image; both proxy `/api` and `/ws` to the
backend (§8).

---

## 3. The shell — `App.tsx`

`App` renders a `header.topbar` and a `main.content`, and owns the small amount
of state that has to be shared across the menu bar, the two segmented controls,
and the active view.

```
┌ topbar ─────────────────────────────────────────────────────────────────────┐
│ rtheatflow │ [Datei][Ansicht][Hilfe] │ (Bereich: Live│NetzStudio) │          │
│            │                          │ (Sicht: 👁Realität│📟Gemessen│🧮Schätzung) │
│            │                          │ [⏺REC n][⬇Export p%]  │ chip · DE│EN │
└──────────────────────────────────────────────────────────────────────────────┘
┌ content ─────────────────────────────────────────────────────────────────────┐
│  <LiveHeatFlow>  (tab==="live")     OR     <NetzStudio>  (tab==="studio")      │
└──────────────────────────────────────────────────────────────────────────────┘
```

`App` holds the two enums that the rest of the tree keys off (`App.tsx:12-20`):

```ts
export type MapLayer = "supply" | "return" | "velocity" | "dp";
export type Tab = "live" | "studio";
export interface LiveView { layer: MapLayer; viewMode: "truth" | "observed" | "est"; }
```

`live` (the `LiveView`) is lifted here so three consumers share one source of
truth: the **Ansicht** menu (which color layer the map paints), the **Sicht**
segment (which data layer to read), and `LiveHeatFlow` / `MapDiagram`
themselves. `patchLive` is a shallow-merge setter passed down as `onLive` /
`onView`.

The **Sicht** segment (Realität / Gemessen / Schätzung — the three-layer view
switcher, `App.tsx:232-248`) is *always visible* in the topbar, not buried in a
menu: the layered-view concept is the platform's core teaching idea, so
switching between the real system, the metered projection, and the observer's
estimate must be one click. What each layer *means* is `OBSERVABILITY.md`'s
subject; the UI mechanics of switching are §11 below.

**The `MenuBar`** (`App.tsx:111-272`) is a desktop-style menu: *Datei* (save /
load / delete scenarios, plus the M6 recording & export workflow), *Ansicht*
(the four map layers), *Hilfe* (links to `/api/manual`, `/api/docs`, GitHub).
Two activity chips — `⏺ REC n` and `⬇ Export p%` — stay visible with all menus
closed via a lightweight 3 s global poll (`api.recording()` + `api.exportStatus()`,
`App.tsx:127-137`), and clicking one jumps into the Datei menu.

**Network swaps and the `liveKey` remount.** When a network is applied in
NetzStudio, or a scenario is loaded from the Datei menu, `onApplied`
(`App.tsx:43-47`) adopts the returned `Topology`, bumps `liveKey`, and switches
to the Live tab. `LiveHeatFlow` is keyed by `liveKey`
(`<LiveHeatFlow key={liveKey} …>`, `App.tsx:93`), so React unmounts and remounts
it: the Leaflet map, the WS-fed state, and every accumulated trace start from
scratch against the new topology. This "blueprint liveKey pattern" is the
frontend's answer to a full backend `engine.reconfigure`.

The initial topology is loaded once (`reloadTopo` → `api.network()`,
`App.tsx:34-39`); a transient proxy flake sets `topoErr`, and a later successful
refetch clears it (`setTopoErr(null)` on success — this one-line clear was a
real M5 bugfix, without it a startup flake left a permanent error banner over a
working app).

---

## 4. State model — lifted state, stamp counters, no store

There is no global store. State lives in exactly three places:

1. **`App.tsx`** — cross-cutting shell state: `tab`, `live` (layer + viewMode),
   `topo`, `liveKey`, and the recording/export poll results.
2. **`views/LiveHeatFlow.tsx`** — everything the Live view needs: engine
   `status`, panel open/closed flags, `stepSeconds`, sidebar width, the pinned
   element list, the sensor `placement`, the Δp `dpTrace`, and the active
   context `menu`.
3. **`views/NetzStudio.tsx`** — the loadgen policy form + previews.

The live physics itself is **not** stored anywhere — it flows straight from the
WebSocket hook as `latest` (§6) and is threaded down as a prop. Nothing
persists it; each frame replaces the last.

Because there is no store and no router, the codebase leans on a handful of
**stamp / sequence counters** to stay correct under React's async lifecycle and
StrictMode double-mounting:

| Counter | File:line | Purpose |
|---|---|---|
| `liveKey` | `App.tsx:32` | force a full Live remount on network/scenario swap |
| `lastStamp` (`day:step`) | `LiveHeatFlow.tsx:55,71-73` | dedupe the Δp trace so a re-render never double-appends a frame already seen |
| `intervalInit` ref | `LiveHeatFlow.tsx:54,81-86` | adopt the engine's tick interval *once*, then let the slider own it |
| `reqRef` / `asgRef` | `NetzStudio.tsx:62-86` | ignore stale preview/assign responses (last-request-wins) |
| `est.seq` (from the wire) | `types.ts:279` | the observer's monotonic estimate id, shown in the quality badge |

The estimate also carries `step`/`day`/`seq` **staleness stamps** (§2.7 of the
shared wire schema): the observer runs on a throttle, so the last estimate rides
along on every following frame; `LiveHeatFlow.tsx:199-203` turns the gap between
`latest.{day,step}` and `est.{day,step}` into an "estimate age" in simulated
minutes for the badge.

---

## 5. Wire types — `types.ts` mirrors the backend exactly

`types.ts` is a hand-maintained mirror of the backend contract (`StepResult`,
`Topology`, `EngineStatus`, the measurement and estimation shapes). It is not
generated; it is kept in lockstep with `src/rtheatflow/simulator.py` /
`sensors.py` / `estimator.py` by hand, and the backend surface test plus this
file are what pin the contract. Every numeric field is `number | null` because
the backend's `_r()` rounds to 6 digits and maps `NaN`/`±Inf` → `null`; every
temperature arrives in **°C** (Kelvin never leaves the solver).

The single most important modeling decision here is **strict mode as absence**.
When the backend runs with `RTHEATFLOW_EXPOSE_GROUND_TRUTH=false`, the projection
in `state.py` pops the four truth keys and blanks the free-text error. `types.ts`
encodes that by making exactly those keys optional on `StepResult`
(`types.ts:288-308`):

```ts
export interface StepResult {
  step: number; day: number; time_of_day: string;
  converged: boolean; solver_status: "ok" | "degraded" | "failed";
  solve_ms: number; timestamp: number;
  junctions?: JunctionState[];   // ← absent in strict mode
  pipes?: PipeState[];           // ← absent in strict mode
  consumers?: ConsumerState[];   // ← absent in strict mode
  summary?: StepSummary;         // ← absent in strict mode
  producers: ProducerState[]; storages: StorageState[];
  weather: WeatherState; controls: Controls;
  measurements: Measurements; observed_summary: ObservedSummary | null;
  estimated: EstimatedState | null;  // survives strict mode (measurement-derived)
  error: string | null;
}
```

The `?` on `junctions/pipes/consumers/summary` is the whole strict-mode story on
the client: TypeScript then *forces* every reader to handle the absent case, and
`LiveHeatFlow` reads exactly one of them — `summary !== undefined` — to decide
whether ground truth is available (§11). `estimated` is **not** optional: it is
derived from measurements and stays visible in strict mode.

`EstimatedState` (`types.ts:263-282`) mirrors the truth arrays
(`junctions/pipes/consumers/summary`) so the map and overview can render it
through the same code path (the splice, §11), plus an `error` innovation block
(`|twin − measurement|` at sensored points) and the staleness stamps.

---

## 6. The WebSocket data path — `useStepStream.ts`

`useStepStream(enabled)` (whole hook, `useStepStream.ts:11-55`) is the only live
data source. It opens `/ws`, keeps the latest parsed `StepResult` in state, and
exposes `{ latest, status }` where `status` is `"connecting" | "open" | "closed"`.

Three robustness properties, all in ~40 lines:

- **Auto-reconnect (1.5 s).** On `onclose`, if the hook is still mounted, it
  schedules `setTimeout(connect, 1500)` (`:37-41`). A dropped backend heals
  without a page reload.
- **StrictMode double-mount hardening.** React 18 StrictMode mounts effects
  twice in dev. The hook guards with a `stopped` flag (set in cleanup) *and*
  per-socket identity checks: every handler early-returns unless
  `!stopped && self === ws` (`:26-42`), where `self` captures the socket at
  connect time. So a socket orphaned by the first (discarded) mount can never
  write state or schedule a reconnect for the live mount.
- **Malformed frames are ignored.** `onmessage` wraps `JSON.parse` in
  try/catch and silently drops anything unparseable (`:31-35`) — a partial or
  corrupt frame never throws into React.

`wsUrl()` (`api.ts:205-208`) builds the socket URL from the page origin,
upgrading `https:` → `wss:`:

```ts
export function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}
```

The browser therefore only ever talks to its own origin; the dev proxy / nginx
forwards `/ws` to the backend (§8).

The data path end to end:

```
backend /ws ──frame──▶ useStepStream ──latest──▶ LiveHeatFlow
                                                    │  (fallback + splice, §11)
                                                    ├─▶ MapDiagram   (restyle, §10)
                                                    ├─▶ OverviewSection
                                                    ├─▶ WorstPointSection (+dpTrace)
                                                    └─▶ HeatingCurve / Weather / Pinned
```

`LiveHeatFlow` polls `GET /status` every 2 s for the engine clock/running state
(`LiveHeatFlow.tsx:59-66`) and re-syncs `status` from the response of every
control verb — the WS gives physics, the status poll gives control state.

---

## 7. The API client — `api.ts`

`api.ts` is a thin, fully-typed wrapper over `fetch`. Every call goes through a
constant `/api` prefix (`api.ts:31`) and one of four helpers (`get`/`post`/`put`/
`del`, `:33-63`) that throw an `Error` carrying the status and body text on a
non-2xx response — so every caller's `.catch` gets a human-readable message.

`export const api = { … }` (`api.ts:67-203`) is a flat object of named methods,
one per backend route the UI uses, each returning a typed Promise. A few
conventions worth noting for anyone extending it:

- **Control verbs take JSON bodies, not query params** — a deliberate deviation
  from the blueprint (`seek: (step) => post("/control/seek", { step })`,
  `:76-79`) that matches this backend's control router.
- **Node ids are URL-encoded** because node names can contain slashes/spaces
  (`placeNodeSensor`, `:152-153`).
- **`recordingDownloadUrl(rid)`** returns a plain `/api/...` string
  (`:195-196`) used directly as an `<a href>` for ZIP download — not fetched.
- **`manualUrl()`** (`:166`) likewise returns a link target for the Hilfe menu.

The client is intentionally dumb: no caching, no retry, no interceptors. Staleness
and races are handled at the call site with the stamp counters of §4.

---

## 8. Dev server, proxy, and the sibling-port scheme — `vite.config.ts`

The browser only ever talks to the Vite origin; Vite proxies to the backend so
there is no CORS in dev (`vite.config.ts:4-26`):

```ts
const BACKEND = process.env.RTHEATFLOW_BACKEND ?? "http://127.0.0.1:8001";
server: {
  port: 5174, strictPort: true,
  proxy: {
    "/api": { target: BACKEND, changeOrigin: true,
              rewrite: (p) => p.replace(/^\/api/, "") },
    "/ws":  { target: BACKEND.replace(/^http/, "ws"), ws: true },
  },
}
```

Three details are load-bearing and each fixed a real bug:

1. **`127.0.0.1`, never `localhost`.** On Windows, `localhost` resolves to IPv6
   `::1` first, which uvicorn (IPv4-only by default) refuses. The proxy target
   is pinned to the IPv4 literal.
2. **`strictPort: true`.** rtheatflow runs on the *sibling* ports 8001/5174 so
   it can sit next to netzsim/rtpowerflow (8000/5173) on one machine. Without
   `strictPort`, a busy 5174 would make Vite silently hop to another port while
   still proxying `/api` into the *wrong* project's backend — an observed live
   failure. Now Vite fails loudly instead.
3. **The `/api` rewrite** strips the prefix so the backend sees bare routes; the
   same prefix strip is reproduced in the production nginx config.

---

## 9. Color and size scales — `scales.ts`

`scales.ts` is the visual grammar of the map, and its governing principle
(stated at the top of the file) is **the unknown is styled as unknown**: an
element with no measurement renders in a dedicated dim grey, *never* in any color
a healthy ramp could produce.

```ts
export const UNOBSERVED       = "#39424f";  // fill for unmeasured markers
export const UNOBSERVED_LINE  = "#2b323c";  // stroke for unmeasured trenches
export const UNOBSERVED_DASH  = "6 6";      // dash — grey alone can read "cold"
```

The three temperature/velocity ramps are **domain-anchored**, not min–max
normalized, so the same reading always maps to the same color regardless of the
current network:

| Ramp | Fn | Anchoring |
|---|---|---|
| Supply (Vorlauf) | `supplyTempColor(t, tFlowDesign)` | warm YlOrRd, **full-hot exactly at the active Heizkurve's `t_flow_design`** — a 4G 70 °C net glows at 70 the way a 3G 110 °C net glows at 110 |
| Return (Rücklauf) | `returnTempColor(t)` | cool blue→violet over 25–70 °C, so a hot return (low-ΔT syndrome) pops in violet, unmistakable against the warm supply ramp |
| Velocity | `velocityColor(v)` | green (idle) → amber exactly at `V_WARN=1.5` m/s → red at `V_MAX=3` m/s; sign ignored (return pipes flow "backwards") |
| Δp | `dpColor(dp, dpMin)` | traffic light: red strictly below `DP_MIN_BAR=0.5`, amber in a 0.15-bar margin, green above |

The physical *why* behind these thresholds (why 1.5 m/s, why 0.5 bar) belongs to
`PHYSICS_AND_CONTROLS.md`; `scales.ts` only encodes them. Every ramp function
returns `UNOBSERVED` for a `null`/`undefined` input (`scales.ts:77,84,90,100`),
which is how "no measurement" becomes grey.

Two size ramps: `mdotWidth(mdot, maxMdot)` (stroke width by √|mdot| relative to
the current snapshot's max) and `consumerRadius(qDesignW)` (marker radius by
√ design load, bounded 4–14 px). `gradientCss` derives the colorbar legend
gradients (`SUPPLY_GRADIENT` etc.) from the very same stop arrays that color the
map, so legend and map can never drift.

`fmt(n, digits)` is the shared locale-aware number formatter (`–` for null),
used across every panel and popup.

**Tested (`scales.test.ts`, 13 of the 20 vitest cases).** The anchors are pinned
("full-hot exactly at `t_flow_design`, for *any* design value"), and the
unknown-grey principle is pinned from both sides: every ramp returns
`UNOBSERVED` for null, and — critically — **no healthy ramp value can ever
produce the `UNOBSERVED` grey** (a regression guard so grey stays unambiguous).

---

## 10. The map — `MapDiagram.tsx` (raw Leaflet, build-once / restyle-per-frame)

`MapDiagram` is the reason there is no `react-leaflet`. It drives the Leaflet
imperative API directly and keeps every vector layer in a `useRef` map. The core
pattern: **build the layers once per topology, then restyle them in place on
every frame with `.setStyle()` — never rebuild.** At 10 frames/second this is
what keeps the map cheap; React never reconciles the vector layers.

### 10.1 Refs, not children

The map, tile layer, and every element collection are refs (`MapDiagram.tsx:85-92`):

```ts
const mapRef    = useRef<L.Map | null>(null);
const trenchRef = useRef<Map<number, L.Polyline>>(new Map());     // by trench id
const consRef   = useRef<Map<number, L.CircleMarker>>(new Map()); // by consumer id
const equipRef  = useRef<Map<string, L.Marker>>(new Map());       // "p<id>"/"s<id>"
const sensorRef = useRef<Map<string, L.Marker>>(new Map());       // "m<id>"/"t<node>"
const plantRef  = useRef<L.CircleMarker | null>(null);
```

### 10.2 Build once — the `[topo, i18n.language]` effect

The construction effect (`MapDiagram.tsx:201-326`) runs only when the topology
or the UI language changes. It creates the map (`preferCanvas: true`,
`zoomSnap: 0.25`), the CARTO tile layer, and **one polyline per trench** (Trasse)
from the shared supply/return geometry (`:219-232`) — supply and return are the
same physical trench, so they are one line on the map, colored by whichever layer
is active. Consumers become `circleMarker`s sized by design load, bypasses
(Netzschluss) become 🔀 divIcons, plain trench nodes become small grey handles,
and the slack plant becomes an amber station marker plus a decorative 🏭 glyph.

A **mount-sizing race** is handled here: when the map mounts in the same React
commit that lays out the CSS grid, its container can be 0-sized at construction
and `fitBounds` lands at world zoom. The fix re-runs `invalidateSize()` + `fit()`
inside a deferred 80 ms timer (`:305-315`).

Cleanup calls `map.remove()` and clears the diffed marker refs (`:317-324`).

### 10.3 Restyle per frame — the `[latest, layer, observedOnly, tFlowDesign, topo]` effect

The per-frame effect (`MapDiagram.tsx:432-500`) touches only `.setStyle()`. It
computes the snapshot's `maxMdot` for relative stroke widths, then for each
trench picks a color from the active layer's ramp (supply/return/velocity, or a
receding slate for the Δp layer where the *consumer markers* carry the signal)
and a weight from `mdotWidth`. Unknown trenches (no pipe data, or the measured
view where pipes carry no meter) get `UNOBSERVED_LINE` + the `6 6` dash. Consumer
markers are filled from `supply`/`return`/`dp` ramps; a standard-fidelity meter
still inside its first 15-minute window is shown "cold-starting" with a dashed
`3 3` ring and muted fill (`:479-488`).

### 10.4 Live popups — the `liveRef` pattern

Popups must keep updating while frames stream, even though their content was
bound once at build time. The trick (`MapDiagram.tsx:97-101`): a single
`liveRef` holds `{ latest, observedOnly }` and is overwritten every render, and
every popup content function is a closure that reads `liveRef.current`. Popups
bind lazily (`bindPopup(() => trenchPopup(tr))`), and the per-frame effect
refreshes any *open* popup via `setPopupContent` (`:467-470, 490-497`). So a
trench popup opened in reality view shows live Vorlauf → Rücklauf temperatures
that tick as the simulation runs, and flips to "keine Messung — unbekannt" in
the measured view — all without rebinding.

### 10.5 Marker diffing — equipment and sensors

Runtime-placed producers/storages (M4) and heat-meters/sensors (M5) come and go,
so they are **diffed**, not rebuilt. Two separate effects build a `want` map
keyed by a stable string (`p<id>`/`s<id>` for equipment on `[latest, topo]`,
`m<id>`/`t<node>` for sensors on `[placement, topo]`), remove any ref no longer
wanted, and add any wanted ref not yet present (`:331-377`, `:383-415`). Sensor
glyphs (📟 / 🌡️) are `interactive: false` so they never steal the underlying
element's own right-click/Ctrl-click.

### 10.6 Interaction wiring

`wireInteractions(layer, target)` (`MapDiagram.tsx:106-121`) attaches two
handlers to every interactive element: `contextmenu` opens the `ElementMenu` at
the click's viewport coordinates (§13), and `click` **with Ctrl held** pins an
element detail section (§12). A plain click keeps Leaflet's own popup. The
callbacks are read through a `cbRef` so the handlers, bound once, always call the
current props.

### 10.7 Basemap and legend

A light/dark CARTO basemap toggle lives on the map (`:419-428`); switching only
swaps the tile layer and restrokes the consumer markers. The colorbar legend
(`:504-554`) is derived per layer from `scales.ts` gradients, with the supply
bar's top label reading the *live* `tFlowDesign` so the legend re-anchors when
the Heizkurve preset changes.

> The map fetches CARTO/OSM raster tiles from external hosts at runtime — the one
> place the client reaches beyond its own origin. Everything else (physics,
> topology, config) is same-origin through the proxy.

---

## 11. The three-layer view — fallback chain and splice (`LiveHeatFlow.tsx`)

The Sicht segment sets `viewMode ∈ {truth, observed, est}`. `LiveHeatFlow`
resolves that *desired* mode against what the current frame actually offers, and
produces the concrete `frame` the map and overview render
(`LiveHeatFlow.tsx:182-203`):

```ts
const canReveal = latest ? latest.summary !== undefined : true;   // strict mode?
const est = latest?.estimated ?? null;
const mode =
    viewMode === "truth" && !canReveal ? "observed"                         // (a)
  : viewMode === "est"   && !est        ? (canReveal ? "truth" : "observed") // (b)
  : viewMode;
const frame = mode === "est" && latest && est
  ? { ...latest, junctions: est.junctions, pipes: est.pipes,
      consumers: est.consumers, summary: est.summary }                       // splice
  : latest;
```

Two fallbacks and one splice:

- **(a) Strict-mode fallback.** If the operator asked for *Realität* but the
  backend withheld ground truth (`summary` absent, so `canReveal===false`), the
  view degrades to *Gemessen* — the client cannot invent truth it was never sent.
  A one-line note ("Wahrheit ausgeblendet") appears in the sidebar
  (`LiveHeatFlow.tsx:258-262`).
- **(b) No-estimate fallback.** If the operator asked for *Schätzung* but no
  estimate exists yet (observer throttled/warming up), the view falls back to
  truth (or observed under strict mode).
- **The splice pattern.** When an estimate *is* shown, the code shallow-spreads
  the live frame and overwrites only the four truth arrays with the estimate's.
  The result is an ordinary-looking `StepResult`, so the *same* `MapDiagram` and
  `OverviewSection` render the estimate with no estimate-specific code path. This
  is the blueprint's "splice" idea: one renderer, three data sources.

`MapDiagram` is told which mode via `observedOnly={mode === "observed"}`; in that
mode trench lookups return nothing (pipes carry no meter) and consumers are read
from `measurements.consumers` instead of `consumers` (`MapDiagram.tsx:125-142`).

---

## 12. The sidebar — section grammar and component inventory

The Live sidebar is a stack of collapsible `Section`s (`components/Section.tsx` —
chevron + title + optional badges; body renders only while open, so heavy
children stay lazy). `<Stat label value color>` is the shared one-line readout.
The sidebar is resizable by dragging its left edge (`LiveHeatFlow.tsx:209-219`).

| Section | Component | What it shows / does |
|---|---|---|
| Übersicht (Overview) | `OverviewSection` | KPI aggregates for the active layer; estimate-quality block in est mode |
| Schlechtpunkt (Worst point) | `WorstPointSection` | Δp control cockpit: mode, setpoint, live trace, blind-spot note |
| Messstellen (Measurements) | `MeasurementPanel` | coverage bars, presets, full/standard fidelity toggle |
| Heizkurve (Heating curve) | `HeatingCurveSection` | curve editor + 3G/4G presets |
| Wetter (Weather) | `WeatherSection` | ambient-temp override knob |
| pinned elements | `EquipmentControls` (`PinnedSection`) | Ctrl-click detail + config knobs |

**`OverviewSection`** (`components/OverviewSection.tsx`) is layer-aware: in
truth/est mode it reads `summary` (feed-in, demand, losses kW + %, pump P_el,
plant Vorlauf/Rücklauf, Heizkurve setpoint, Schlechtpunkt); in observed mode it
reads `observed_summary` (metered aggregates + coverage `n_metered/n_consumers`).
The Heizkurve setpoint is evaluated **client-side** by `curveSetpoint()`
(`:20-30`), the same SPEC §4.2 formula the backend uses, so the setpoint the plant
is steering toward sits next to the actual flow temperature. In est mode it adds
a **quality badge** (`:113-136`): estimate age in simulated minutes + `#seq`, the
innovation maxima (`max |ΔT_return|`, `max |Δṁ|`, `max |ΔΔp|` at sensors), and the
observer's own solve time. The solver badge (`ok`/`degraded`/`failed`) and solve
time round out the section.

**`WorstPointSection`** (`components/WorstPointSection.tsx`) is the Δp cockpit.
It toggles `controlled` (Schlechtpunktregelung) vs `fixed` (ungeregelte Pumpe),
edits the setpoint (0.3–2.0 bar) or the fixed lift, and plots the client-
accumulated observed-Δp trace against the setpoint marker via `Sparkline`. It
renders the **blind-spot** warning when `controls.dp_control.blind_spot === true`,
with two texts — "no reading at all" vs "true worst point unmetered"
(`:106-112`). The section only *edits* config and *visualizes* the trace; the
controller runs server-side. The controller algorithm is
`PHYSICS_AND_CONTROLS.md`'s topic; the blind-spot as an observability concept is
`OBSERVABILITY.md`'s.

**`MeasurementPanel`** (`components/MeasurementPanel.tsx`) carries the bulk
tools: the full/standard fidelity segment, two coverage bars (📟 meters, 🌡️
sensors), the four preset buttons (`all_consumers` / `plant_only` / `key_points`
/ `clear`), and the strict-mode notice when `expose_ground_truth` is false.
Individual meters/sensors are placed via the map context menu, not here.

**`HeatingCurveSection`** edits the five curve parameters with a live/draft split
(adopts live params when nothing is being edited) and the 3G/4G preset buttons;
**`WeatherSection`** is the hot-path ambient knob (−30…45 °C) that debounces
`PUT /weather/override` at 150 ms while dragging and clears the override on
release; **`EquipmentControls`** renders a pinned `Section` per Ctrl-clicked
element with live values and config knobs (heat-exchanger dispatch, pump mdot,
storage SoC bar + charge/discharge/idle segment + power).

**Transport bar** (`LiveHeatFlow.tsx:305-338`): play/pause, a time-of-day seek
slider (`0..steps_per_day-1`), a day slider when `n_days > 1`, and a step-duration
slider (0.1–1 s). Every verb re-syncs `status` from its own response.

---

## 13. The context menu — `ElementMenu.tsx`

Right-clicking any map element opens `ElementMenu` (`components/ElementMenu.tsx`)
at the click coordinates — the SPEC §8 interaction grammar. It is a two-page
menu: the **main** page lists element-specific actions first (📌 pin, remove,
storage mode, plant-kind picker, place/remove 📟 heat-meter or 🌡️ T/p sensor),
then the **placement** block that every target offers because every target sits
at a trench node — "Hier platzieren": ☀️ heat-exchanger, ⚙️ pump, 🛢️ storage,
🏠 consumer, 🔀 bypass. Choosing "consumer" or "plant kind" swaps the page in
place (`page` state) to an archetype picker or a boiler/CHP/heat-pump picker.

`MenuTarget` (`:7-17`) records what was clicked (`kind`, `id`, `node`, viewport
`x/y`, plus `consumerKind`/`producerKind` for correct remove/config labels), and
`MenuAction` (`:19-34`) is the discriminated union the view's dispatcher
(`LiveHeatFlow.tsx:99-166`) turns into `api.*` calls. Every mutating action, on
success, calls `onTopoChange()` (App refetches `/network`, since CRUD changes the
inventory) and re-syncs the sensor placement (a removed consumer takes its meter
with it). Escape or an overlay click closes.

---

## 14. NetzStudio — the 3-column network workflow (`views/NetzStudio.tsx`)

The NetzStudio tab is a left-to-right pipeline:

```
① pick / import a network   ②  loadgen policy          ③  preview & apply
   ┌ Bibliothek ───┐          ┌ 3G/4G preset ─┐          ┌ KPI tiles ─────┐
   │ demo_dorf     │          │ day percentile │          │ consumers/km/kW│
   │ appendix_a    │   ─────▶ │ archetype mix  │  ─────▶  │ LHD (viability)│
   │ destest_16 …  │          │ scale / seed   │          ├ load-duration ─┤
   ├ Eigene Netze ─┤          │ jitter         │          │  Sparkline     │
   │ ⬆ Netz import │          └────────────────┘          │ [Anwenden]     │
   └───────────────┘                                      └────────────────┘
```

- **Column 1** lists the catalog split into *Bibliothek* (`source !== "user"`)
  and *Eigene Netze* (`source === "user"`), plus a "Netz importieren" file
  picker. The picker accepts either the five contract JSON files together (named
  `<doc>.json`) or a single bundle carrying all five as keys; `importFiles`
  (`:101-136`) validates the five documents client-side, `POST /networks/import`s
  the bundle, refreshes the list, and auto-selects the result.
- **Column 2** builds the SPEC §4.5 `LoadgenPolicy` (`useMemo`, `:53-59`):
  3G/4G temperature preset, day percentile (design/winter/shoulder/summer),
  archetype checkboxes, scale, seed, jitter.
- **Column 3** shows KPI tiles and a **load-duration Sparkline** vs the design-
  load marker, auto-previewed via a **debounced, last-request-wins**
  `POST /loadgen/assign` (`asgRef`, `:74-86`). The **linear heat density** (LHD)
  KPI is color-toned against the ≥1–1.5 MWh/(m·a) viability rule of thumb
  (`:146-149`). "Anwenden" calls `POST /config/apply`; the `ApplyResponse`
  bubbles to `App.onApplied`, which remounts Live via `liveKey`.

The catalog/dataset content — provenance, conversion decisions, validation
numbers — is `REFERENCE_NETWORKS.md`'s subject; NetzStudio is just its cockpit.

---

## 15. Internationalization — German-first with tsc-enforced parity

`i18n.ts` holds one inline resource file per language and one init call
(`i18n.ts:760-765`, `lng: "de"`, `fallbackLng: "en"`). Keys are feature-prefixed
(`mbar.*`, `ov.*`, `wp.*`, `meas.*`, `netz.*`, `pop.*`, …).

**German is the authoring reference.** `const de = { … }` is declared first
(`:8`); the English table is declared as `const en: typeof de = { … }` (`:385`).
That type annotation is the parity mechanism: if a key exists in `de` but is
missing from `en`, `tsc` fails with TS2741 — so `tsc && vite build` cannot pass
with an incomplete translation in either direction. (This was verified once by a
deliberate DE-only probe key failing the build.) Domain vocabulary lives in the
German side as the source of truth (Vorlauf, Rücklauf, Schlechtpunkt, Heizkurve,
Wärmemengenzähler, Beobachter/observer), matching the terminology table in
`docs/ARCHITECTURE.md`.

The DE/EN switch in the topbar calls `i18n.changeLanguage`; `MapDiagram` lists
`i18n.language` in its build-once dependency array (`MapDiagram.tsx:326`) so
tooltips and popups — bound at construction — are rebuilt on a language flip.

---

## 16. Build, tests, and dev workflow

- **Build gate:** `npm run build` = `tsc && vite build` (`package.json:8`). The
  `tsc` pass is a pure type check (`noEmit`); a strict-mode type error fails the
  build. This is what keeps `types.ts` honest against the backend contract and
  the i18n tables in parity.
- **Tests:** `npm test` = `vitest run`, **20 unit tests** across two files.
  `scales.test.ts` (13) pins the ramp anchors and the unknown-grey principle from
  both sides. `Sparkline.test.ts` (7) pins the chart math — chiefly
  `chartExtent` giving the marker line headroom **without mutating the series**
  (a marker pushed into the data array would draw a phantom peak at the end of
  the day) and `axisTicks` spanning min–max inclusively.
- **Dev:** `npm run dev` serves on 5174 with the proxy of §8. In production the
  same bundle is served by nginx (two-stage `node:22-alpine` → `nginx:1.27-alpine`
  image) with an SPA fallback and matching `/api` prefix strip + `/ws` upgrade
  headers.

The map cannot be pixel-tested in the embedded browser pane: it runs the tab
hidden, Chrome suspends `requestAnimationFrame`, and Leaflet's canvas renderer
never repaints. Automated UI checks therefore assert against Leaflet layer
*styles* (reached via the ref maps) and the DOM, not canvas pixels — a caveat for
anyone doing end-to-end verification here.

---

## 17. Cross-references

- **`docs/ARCHITECTURE.md` §7** — the system-level frontend summary this doc
  expands; §1 (three-layer view) and §3.5 (observability layers) for context.
- **`OBSERVABILITY.md`** — what Realität / Gemessen / Schätzung *mean*, the
  MeasurementSet device model, strict mode semantics, the forward observer and
  its innovation metric, and the Δp blind-spot as an observability concept. The
  UI renders these; that doc defines them.
- **`PHYSICS_AND_CONTROLS.md`** — the physical meaning behind the color
  thresholds (velocity, Δp), and the Δp controller algorithm the WorstPoint
  cockpit merely configures.
- **`REFERENCE_NETWORKS.md`** — the catalog and datasets NetzStudio drives, the
  five-file contract the import picker validates against.
- **`BENCHMARKS.md`** — solve timings and the observer's cost, which set the
  frame cadence the map's restyle pattern must keep up with.
