"""Sensor / measurement layer (SPEC §8a).

Two things live here:

* :func:`_r` — the single JSON-safe rounding helper used for every float that
  reaches the wire (blueprint convention: defined once, imported everywhere).
* :class:`MeasurementSet` — the **M2 interim** measurable layer (SPEC §8a
  interim rule): it exists from M2 onward with the default preset
  ``all_consumers`` + plant SCADA, so every frame carries ``measurements`` /
  ``observed_summary``, strict mode is meaningful, and the M4 Δp controller
  reads the observed layer from day one (no M5 refactor). Sensor placement
  CRUD, the ``key_points`` preset, and fidelity modes (``full`` vs 15-min
  ``standard`` windows) ship in M5.

The measurable layer is a **projection of ground truth**, never a parallel
computation: ``observe()`` selects sensored elements out of the collected
truth payload — what a heat meter at a substation or the plant SCADA would
actually show.
"""
from __future__ import annotations

import math
from typing import Any


def _r(value: Any, ndigits: int = 6) -> float | None:
    """JSON-safe rounding: round to 6 digits; NaN/±Inf/unconvertible → None."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, ndigits)


# Consumer heat meter (Wärmemengenzähler) channels — SPEC §8a: a substation
# meter reads heat flow, mass flow and both temperatures, nothing else.
_HEAT_METER_KEYS = ("id", "name", "node", "q_kw", "mdot_kg_per_s",
                    "t_supply_c", "t_return_c", "dp_bar")

# M2 interim presets (SPEC §8a). "key_points" (plant + worst point + net
# ends) needs sensor placement bookkeeping and ships with the M5 CRUD.
PRESETS = ("all_consumers", "plant_only", "clear")
DEFAULT_PRESET = "all_consumers"


class MeasurementSet:
    """Observed-layer projection (SPEC §8a interim: preset-only, no CRUD yet).

    * ``all_consumers`` (default): heat meter at every substation.
    * ``plant_only`` / ``clear``: no substation meters (equivalent until the
      M5 placement CRUD distinguishes them — plant SCADA is always measured,
      real plants are).
    """

    def __init__(self, preset: str = DEFAULT_PRESET):
        self.apply_preset(preset)

    def apply_preset(self, preset: str) -> None:
        if preset not in PRESETS:
            raise ValueError(
                f"unknown measurement preset {preset!r} (M2 interim presets: "
                f"{PRESETS}; 'key_points' ships with the M5 sensor CRUD)")
        self.preset = preset

    # -- the projection --------------------------------------------------------

    def observe(self, truth: dict) -> tuple[dict, dict | None]:
        """Project the collected truth payload onto the sensored elements.

        *truth* is the ``Simulator._collect()`` payload (``consumers`` /
        ``producers`` / ``summary`` keys). Returns ``(measurements,
        observed_summary)`` per the SPEC §6 wire format. From M5 on this will
        take the full ``StepResult`` and honor placements/fidelity modes.
        """
        consumers = truth.get("consumers") or []
        summary = truth.get("summary") or {}
        if not summary:  # no converged physics yet — honest empty view
            return {}, None

        metered = list(consumers) if self.preset == "all_consumers" else []
        meters = [{k: c.get(k) for k in _HEAT_METER_KEYS} for c in metered]

        # Plant SCADA (always measured): the plant's own quantities only.
        plant = {
            "q_feed_kw": summary.get("q_feed_kw"),
            "t_flow_c": summary.get("t_flow_plant_c"),
            "t_return_c": summary.get("t_return_plant_c"),
            "mdot_kg_per_s": summary.get("mdot_plant_kg_per_s"),
            "pump_el_kw": summary.get("pump_el_kw"),
        }

        measurements = {
            "preset": self.preset,
            "consumers": meters,
            "nodes": [],  # T/p junction sensors: placement CRUD ships in M5
            "plant": plant,
        }

        # Aggregates over metered elements ONLY (SPEC §6 observed_summary) —
        # the operator's arithmetic, not the ground truth's.
        q_metered = [m["q_kw"] for m in meters if m["q_kw"] is not None]
        dp_metered = [(m["dp_bar"], m["name"]) for m in meters
                      if m["dp_bar"] is not None]
        dp_worst, worst_name = min(dp_metered, default=(None, None))
        observed_summary = {
            "q_feed_kw": plant["q_feed_kw"],
            "q_demand_metered_kw": _r(sum(q_metered)) if q_metered else None,
            "n_metered": len(meters),
            "n_consumers": len(consumers),
            "dp_worst_bar": dp_worst,
            "worst_consumer": worst_name,
            "t_flow_plant_c": plant["t_flow_c"],
            "t_return_plant_c": plant["t_return_c"],
            "mdot_plant_kg_per_s": plant["mdot_kg_per_s"],
            "pump_el_kw": plant["pump_el_kw"],
        }
        return measurements, observed_summary
