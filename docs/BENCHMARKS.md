# Solve-time benchmarks

## M1 — headless core (2026-07-15)

Warm `pp.pipeflow(mode="bidirectional", iter=100)` per step, median of
20 runs after one warm-up solve (which includes the one-off numba JIT).
Both nets are quasi-static single steps — the live-loop per-tick cost.

| Net | Size | First solve (incl. JIT) | Warm median | min / max |
|---|---|---|---|---|
| Appendix A fixture (via platform builder) | 8 junctions, 6 pipes, 3 consumers | 7162 ms | **27.3 ms** | 15.7 / 39.4 ms |
| `schutterwald_heat()` (pandapipes example) | 488 junctions, 482 pipes, 44 consumers | 29 ms | **20.6 ms** | 16.3 / 38.7 ms |

Hardware/software: Windows 11 (Intel64 Family 6 Model 154 Stepping 3, GenuineIntel);
Python 3.14.3, pandapipes 0.14.0,
pandapower 3.3.3, numpy 2.4.6,
numba 0.66.0.

Run-to-run variance on this laptop-class CPU is significant (observed warm
medians 20-35 ms for both nets across repeat runs — boost/thermal scheduling);
treat single-run medians as indicative, not as regression thresholds.

Context (SPEC §10.5): the spec's reference machine measured ~21 ms (fixture)
and ~33 ms (schutterwald) warm — 1 s/step real-time ticks retain >10x headroom
either way; sub-second acceleration (0.1 s/step) stays feasible.
