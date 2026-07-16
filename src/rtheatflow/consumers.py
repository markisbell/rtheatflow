"""Consumer profile construction from the archetype cache (SPEC §4.5).

Runtime consumer placement (``POST /consumer``) and the loadgen assignment
(``POST /loadgen/assign`` / ``/config/apply``) both need per-consumer
``q_sh_w`` / ``q_dhw_w`` (+ ``treturn``) profiles at the *network's* profile
resolution. This module cuts them out of the committed archetype year cache
(``data/profiles/``, demandlib VDI 4655 + OpenDHW, 15-min), the same way the
demo_dorf generator does:

* pick a **day window** of the archetype year by space-heating percentile
  (0.9 = properly cold winter day, 0.5 = shoulder, 0.05 = summer);
* rotate the stochastic **DHW variants** so identical buildings do not draw
  synchronously; seeded ±30 min time shift + 0.92–1.08 amplitude jitter;
* staircase-resample onto the network's ``resolution_minutes`` grid.

Everything is deterministic given ``(archetype, seed, variant, percentile)``
— scenario recipes replay bit-identically (SPEC §4.6).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

ARCHETYPE_RESOLUTION_MIN = 15


@dataclass(frozen=True)
class ConsumerProfile:
    """One generated consumer profile at the network's resolution."""

    q_sh_w: np.ndarray
    q_dhw_w: np.ndarray
    q_design_w: float
    annual_kwh: float
    archetype: str | None = None


class ArchetypeLibrary:
    """The archetype cache ``data/profiles/`` (index + lazily loaded years)."""

    def __init__(self, profiles_dir: str | Path):
        self.dir = Path(profiles_dir)
        self._index: dict | None = None

    @property
    def available(self) -> bool:
        return (self.dir / "index.json").is_file()

    @property
    def index(self) -> dict:
        if self._index is None:
            self._index = json.loads(
                (self.dir / "index.json").read_text(encoding="utf-8"))
        return self._index

    def list(self) -> list[dict]:
        """Archetype metadata rows (no profile arrays)."""
        return list(self.index.get("archetypes", [])) if self.available else []

    def ids(self) -> list[str]:
        return [a["id"] for a in self.list()]

    def meta(self, archetype_id: str) -> dict:
        for a in self.list():
            if a["id"] == archetype_id:
                return a
        raise KeyError(f"unknown archetype {archetype_id!r}")

    def load(self, archetype_id: str) -> dict:
        return _load_archetype(str(self.dir), archetype_id,
                               self.meta(archetype_id)["file"])


@lru_cache(maxsize=16)
def _load_archetype(dir_str: str, archetype_id: str, filename: str) -> dict:
    return json.loads((Path(dir_str) / filename).read_text(encoding="utf-8"))


def _resample_staircase(values: np.ndarray, n_out: int) -> np.ndarray:
    src = np.asarray(values, dtype=float)
    idx = (np.arange(n_out, dtype=np.int64) * len(src)) // n_out
    return src[idx]


def pick_day(q_sh_year: np.ndarray, percentile: float,
             steps_per_day: int = 96) -> int:
    """Day index of the archetype year by space-heating day-sum percentile."""
    days = q_sh_year[: 365 * steps_per_day].reshape(-1, steps_per_day).sum(axis=1)
    order = np.argsort(days)
    pos = int(round(min(max(percentile, 0.0), 1.0) * (len(order) - 1)))
    return int(order[pos])


def archetype_profile(
    library: ArchetypeLibrary,
    archetype_id: str,
    *,
    steps: int,
    resolution_minutes: int,
    day: int,
    seed: int = 0,
    variant: int | None = None,
    scale: float = 1.0,
    jitter: bool = True,
) -> ConsumerProfile:
    """Cut one consumer's profile out of the archetype year.

    *steps* × *resolution_minutes* is the network horizon (whole days); the
    window starts at archetype-year day *day* and wraps modulo the year.
    """
    doc = library.load(archetype_id)
    spd = 24 * 60 // ARCHETYPE_RESOLUTION_MIN         # 96 per archetype day
    n15 = (steps * resolution_minutes) // ARCHETYPE_RESOLUTION_MIN

    q_sh_year = np.asarray(doc["q_sh_w"], dtype=float)
    variants = doc.get("q_dhw_w_variants") or [doc["q_dhw_w"]]
    v = int(variant if variant is not None else 0) % len(variants)
    q_dhw_year = np.asarray(variants[v], dtype=float)

    start = (int(day) * spd) % len(q_sh_year)
    sl = (np.arange(n15) + start) % len(q_sh_year)

    rng = np.random.default_rng(int(seed))
    amp = float(rng.uniform(0.92, 1.08)) if jitter else 1.0
    shift = int(rng.integers(-2, 3)) if jitter else 0   # ±30 min at 15-min res

    q_sh = np.roll(q_sh_year[sl], shift) * amp * float(scale)
    q_dhw = np.roll(q_dhw_year[sl], shift) * float(scale)

    annual = doc["annual_kwh"]
    return ConsumerProfile(
        q_sh_w=_resample_staircase(q_sh, steps),
        q_dhw_w=_resample_staircase(q_dhw, steps),
        q_design_w=float(doc["q_design_w"]) * amp * float(scale),
        annual_kwh=(float(annual["space_heating"]) + float(annual["dhw_mean"]))
        * amp * float(scale),
        archetype=archetype_id,
    )


def constant_profile(q_kw: float, steps: int) -> ConsumerProfile:
    """A flat teaching profile: constant space-heating load, no DHW."""
    q_w = float(q_kw) * 1000.0
    return ConsumerProfile(
        q_sh_w=np.full(steps, q_w),
        q_dhw_w=np.zeros(steps),
        q_design_w=q_w,
        annual_kwh=q_w * 8.76,   # q_w/1000 kW · 8760 h
        archetype=None,
    )
