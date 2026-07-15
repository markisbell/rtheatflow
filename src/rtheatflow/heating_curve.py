"""Heating curve — sliding supply-temperature control (SPEC §4.2).

    t_flow(t_amb) = clamp(t_flow_min + (t_flow_design − t_flow_min) ·
                          ((t_room − t_amb)/(t_room − t_amb_design))^(1/n),
                          t_flow_min, t_flow_design)

Presets make the 3rd-vs-4th-generation temperature-lowering narrative one
click: "3G" = 110/60 system (flow design 110 °C), "4G" = 70/40 (flow 70 °C).
The simulator writes the result to ``circ_pump_pressure.t_flow_k`` each tick.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import HeatingCurveConfig

KELVIN = 273.15


@dataclass
class HeatingCurve:
    t_amb_design_c: float = -12.0
    t_flow_design_c: float = 110.0
    t_flow_min_c: float = 70.0
    t_room_c: float = 20.0
    n: float = 1.0  # curve exponent: 1 = linear, ~1.3 radiator

    def t_flow_c(self, t_amb_c: float) -> float:
        ratio = (self.t_room_c - t_amb_c) / (self.t_room_c - self.t_amb_design_c)
        ratio = max(0.0, ratio)
        t = self.t_flow_min_c + (
            self.t_flow_design_c - self.t_flow_min_c
        ) * ratio ** (1.0 / self.n)
        return min(max(t, self.t_flow_min_c), self.t_flow_design_c)

    def t_flow_k(self, t_amb_c: float) -> float:
        return self.t_flow_c(t_amb_c) + KELVIN

    def params(self) -> dict:
        return {
            "t_amb_design_c": self.t_amb_design_c,
            "t_flow_design_c": self.t_flow_design_c,
            "t_flow_min_c": self.t_flow_min_c,
            "t_room_c": self.t_room_c,
            "n": self.n,
        }


# SPEC §4.2: presets "3. Generation (110/60)" and "4. Generation (70/40)"
PRESETS: dict[str, HeatingCurve] = {
    "3G": HeatingCurve(t_flow_design_c=110.0, t_flow_min_c=70.0),
    "4G": HeatingCurve(t_flow_design_c=70.0, t_flow_min_c=65.0),
}


def from_config(cfg: HeatingCurveConfig) -> HeatingCurve:
    """Build a curve from a producers.json ``heating_curve`` block."""
    if cfg.preset is not None:
        return PRESETS[cfg.preset]
    return HeatingCurve(
        t_amb_design_c=cfg.t_amb_design_c,
        t_flow_design_c=cfg.t_flow_design_c,
        t_flow_min_c=cfg.t_flow_min_c,
        t_room_c=cfg.t_room_c,
        n=cfg.n,
    )
