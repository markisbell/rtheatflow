"""Platform-level producer dispatch models (SPEC §4.4).

pandapipes knows nothing about boilers, CHP units or heat pumps — the slack
is just a pressure/temperature source. The *plant kind* is a *platform-level
dispatch model on top of the slack*: it turns the solved heat feed-in into
the electric/fuel side per tick, reported in the frame's ``producers`` list.

* **boiler**    — η ≈ 0.9–1.05 (condensing depends on return temperature;
  modelled as a constant here): reports ``p_fuel_kw = q/η``.
* **chp**       — heat-led band, electric output as a scalar
  ``p_el_kw = pq_ratio · q`` (P/Q ≈ 0.4–0.6, default 0.5).
* **heat_pump** — ``COP = η_g · T_hot/(T_hot − T_cold)`` in **Kelvin**,
  Gütegrad ``η_g`` default 0.5 (0.4–0.6), ``T_cold`` from the live weather
  (``t_amb`` air-source or ``t_ground`` ground-source per config), ``T_hot``
  = the live plant flow temperature; recomputed per tick,
  ``p_el_kw = q/COP``. Lowering network temperatures raises COP ~2–3 %/K —
  the §1 temperature-lowering narrative, quantitatively.
"""
from __future__ import annotations

from dataclasses import dataclass

KELVIN = 273.15

PLANT_KINDS = ("boiler", "chp", "heat_pump")
T_COLD_SOURCES = ("t_amb", "t_ground")


@dataclass
class PlantModel:
    """Dispatch model of the central plant (the slack producer)."""

    kind: str = "boiler"            # "boiler" | "chp" | "heat_pump"
    eta: float = 0.95               # boiler: fuel → heat efficiency
    pq_ratio: float = 0.5           # chp: electric P per heat Q (0.4–0.6)
    eta_g: float = 0.5              # heat pump Gütegrad (0.4–0.6)
    t_cold_source: str = "t_amb"    # "t_amb" (air) | "t_ground" (ground)

    def metrics(self, q_kw: float, t_hot_c: float,
                t_amb_c: float, t_ground_c: float) -> dict:
        """Per-tick electric/fuel-side quantities for the solved feed-in."""
        if self.kind == "heat_pump":
            t_cold_c = t_amb_c if self.t_cold_source == "t_amb" else t_ground_c
            t_hot_k = t_hot_c + KELVIN
            # Kelvin, guarded: a source warmer than the sink would blow the
            # Carnot ratio up — clamp the temperature lift at 1 K
            lift_k = max(1.0, t_hot_k - (t_cold_c + KELVIN))
            cop = self.eta_g * t_hot_k / lift_k
            return {"cop": cop, "p_el_kw": q_kw / cop if cop > 0 else None,
                    "t_cold_c": t_cold_c}
        if self.kind == "chp":
            return {"p_el_kw": self.pq_ratio * q_kw}
        return {"p_fuel_kw": q_kw / self.eta if self.eta > 0 else None}

    def params(self) -> dict:
        return {
            "kind": self.kind, "eta": self.eta, "pq_ratio": self.pq_ratio,
            "eta_g": self.eta_g, "t_cold_source": self.t_cold_source,
        }
