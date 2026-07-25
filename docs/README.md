# rtheatflow documentation

This directory holds the deep-dive documentation for rtheatflow — the realtime
pandapipes district-heating simulator. The top-level [README](../README.md) is
the entry point; [SPEC.md](../SPEC.md) is the binding build specification and
[CLAUDE.md](../CLAUDE.md) the per-milestone development log.

## Map

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture: the Engine/Simulator/Store split, the one-wire-format `StepResult`, the solver policy, the observability layers, and the honest quasi-static limitation. **Start here.** |
| [PHYSICS_AND_CONTROLS.md](PHYSICS_AND_CONTROLS.md) | The district-heating physics model (pandapipes supply/return solve, retry ladder, direction-aware losses) and the operator controls (heating curve, worst-point Δp control, plant/storage/bypass equipment, weather override) — every formula code-accurate. |
| [OBSERVABILITY.md](OBSERVABILITY.md) | The three-layer view (Realität / Gemessen / Schätzung): the `MeasurementSet` projection, strict mode, the `ForwardObserver` digital twin, and the honesty tripwires that keep the layers genuinely distinct. |
| [REFERENCE_NETWORKS.md](REFERENCE_NETWORKS.md) | The five-file data contract, the profile toolbox (demandlib + OpenDHW), the network catalog, and every shipped network (`appendix_a`, `demo_dorf`, DESTEST, Schutterwald, Verbier) with provenance and conversion decisions. |
| [BENCHMARKS.md](BENCHMARKS.md) | Solve-time benchmarks and the reference-network validation record: DESTEST cross-tool bands, Schutterwald plausibility, and Verbier vs. real monitoring data — with reproduction commands and accepted modelling differences. |
| [UI_ARCHITECTURE.md](UI_ARCHITECTURE.md) | The React 18 + raw-Leaflet frontend: topbar shell, lifted state model, the WebSocket hook, the wire-type mirror, domain-anchored color ramps, the build-once/restyle-per-frame map, and the three-layer view splice. |
| [API.md](API.md) | Generated REST + WebSocket reference (`scripts/gen_api_doc.py`, pinned by a test). |
| [Benutzerhandbuch.md](Benutzerhandbuch.md) | German user manual, served live at `GET /manual`. |
| [Benutzerhandbuch.tex](Benutzerhandbuch.tex) / [Benutzerhandbuch.pdf](Benutzerhandbuch.pdf) | The **comprehensive printable German user manual** (LaTeX/KOMA `scrreprt`, ~46 pages, 16 chapters, 27 worked "Beispiel" boxes) — the counterpart to the rtpowerflow manual. Build with `latexmk -pdf Benutzerhandbuch.tex` in this directory. |

## Reading paths

- **New contributor:** [ARCHITECTURE.md](ARCHITECTURE.md) → [PHYSICS_AND_CONTROLS.md](PHYSICS_AND_CONTROLS.md) → [REFERENCE_NETWORKS.md](REFERENCE_NETWORKS.md).
- **Understanding the pedagogy:** [OBSERVABILITY.md](OBSERVABILITY.md) — the three-layer view is the intellectual heart of the platform.
- **Front-end work:** [UI_ARCHITECTURE.md](UI_ARCHITECTURE.md) + [API.md](API.md).
- **Trusting the physics:** [BENCHMARKS.md](BENCHMARKS.md) — what is validated against what, and how to reproduce it.
- **Using the tool:** [Benutzerhandbuch.md](Benutzerhandbuch.md).

Every technical doc is written against the code at `master` and cites real
module names and `file:line` anchors; line anchors may drift as the code
evolves, symbol names should not.
