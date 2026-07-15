# M1 solve-time benchmark (SPEC §12 M1 acceptance; expectations in §10.5).
# Records median warm solve times for the Appendix A fixture (via the
# platform builder) and pandapipes' schutterwald_heat into docs/BENCHMARKS.md.
# Run: .venv/Scripts/python scripts/benchmark_m1.py
from __future__ import annotations

import platform
import statistics
import sys
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numba  # noqa: E402
import numpy as np  # noqa: E402
import pandapipes as pp  # noqa: E402
import pandapower  # noqa: E402
from pandapipes.networks import schutterwald_heat  # noqa: E402

from rtheatflow.data_loader import load_network  # noqa: E402
from rtheatflow.network_builder import build_network  # noqa: E402

N_WARM = 20


def bench(net, n=N_WARM, **pf_kwargs) -> tuple[float, float, float, float]:
    """(first_ms, median_ms, min_ms, max_ms) — first solve separate (JIT)."""
    t0 = time.perf_counter()
    pp.pipeflow(net, **pf_kwargs)
    first_ms = (time.perf_counter() - t0) * 1000
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        pp.pipeflow(net, **pf_kwargs)
        times.append((time.perf_counter() - t0) * 1000)
    return first_ms, statistics.median(times), min(times), max(times)


def main() -> None:
    inputs = load_network(REPO / "data" / "networks" / "appendix_a")
    fixture, _ = build_network(inputs)
    f_first, f_med, f_min, f_max = bench(
        fixture, mode="bidirectional", iter=100)

    sw = schutterwald_heat()
    s_first, s_med, s_min, s_max = bench(sw, mode="bidirectional", iter=100)

    cpu = platform.processor() or platform.machine()
    lines = f"""# Solve-time benchmarks

## M1 — headless core ({date.today().isoformat()})

Warm `pp.pipeflow(mode="bidirectional", iter=100)` per step, median of
{N_WARM} runs after one warm-up solve (which includes the one-off numba JIT).
Both nets are quasi-static single steps — the live-loop per-tick cost.

| Net | Size | First solve (incl. JIT) | Warm median | min / max |
|---|---|---|---|---|
| Appendix A fixture (via platform builder) | {len(fixture.junction)} junctions, {len(fixture.pipe)} pipes, {len(fixture.heat_consumer)} consumers | {f_first:.0f} ms | **{f_med:.1f} ms** | {f_min:.1f} / {f_max:.1f} ms |
| `schutterwald_heat()` (pandapipes example) | {len(sw.junction)} junctions, {len(sw.pipe)} pipes, {len(sw.heat_consumer)} consumers | {s_first:.0f} ms | **{s_med:.1f} ms** | {s_min:.1f} / {s_max:.1f} ms |

Hardware/software: {platform.system()} {platform.release()} ({cpu});
Python {sys.version.split()[0]}, pandapipes {pp.__version__},
pandapower {pandapower.__version__}, numpy {np.__version__},
numba {numba.__version__}.

Run-to-run variance on this laptop-class CPU is significant (observed warm
medians 20-35 ms for both nets across repeat runs — boost/thermal scheduling);
treat single-run medians as indicative, not as regression thresholds.

Context (SPEC §10.5): the spec's reference machine measured ~21 ms (fixture)
and ~33 ms (schutterwald) warm — 1 s/step real-time ticks retain >10x headroom
either way; sub-second acceleration (0.1 s/step) stays feasible.
"""
    out = REPO / "docs" / "BENCHMARKS.md"
    out.write_text(lines, encoding="utf-8")
    print(lines)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
