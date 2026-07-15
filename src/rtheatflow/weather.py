"""Weather model — the master input (SPEC §4.1) + live override scaling (§4.5).

One weather time series drives everything: ambient temperature feeds the
space-heating demand and the heating curve; ground temperature feeds every
pipe's ``text_k``.

The **live override** lets a user drag the outdoor temperature; demand
responds instantly via the degree-hour factor

    f(T) = max(0, T_room − T) / (T_room − T_design)

applied to the **space-heating part only** (DHW untouched):

* if ``f(T_profile) ≥ ε`` (ε = 0.05):  ``q_sh = q_sh_profile · f(T_ovr)/f(T_prof)``
* else (summer regime, profile SH ≈ 0 — no ratio exists):
  ``q_sh = q_design · f(T_ovr)``
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPSILON = 0.05  # summer-regime guard for the degree-hour ratio (SPEC §4.5)


@dataclass
class WeatherModel:
    """Tick-resolution weather arrays + override state.

    ``t_amb_c``/``t_ground_c`` are the ``ProfileArrays`` views ([T] ticks).
    """

    t_amb_c: np.ndarray
    t_ground_c: np.ndarray
    t_room_c: float = 20.0
    t_design_c: float = -12.0
    override_t_amb_c: float | None = None

    @property
    def override_active(self) -> bool:
        return self.override_t_amb_c is not None

    def set_override(self, t_amb_c: float) -> None:
        self.override_t_amb_c = float(t_amb_c)

    def clear_override(self) -> None:
        self.override_t_amb_c = None

    def t_amb(self, tick: int) -> float:
        """Effective ambient temperature at *tick* (override wins)."""
        if self.override_t_amb_c is not None:
            return self.override_t_amb_c
        return float(self.t_amb_c[tick])

    def t_ground(self, tick: int) -> float:
        return float(self.t_ground_c[tick])

    def degree_hour_factor(self, t_amb_c: float | np.ndarray) -> float | np.ndarray:
        return np.maximum(0.0, self.t_room_c - t_amb_c) / (
            self.t_room_c - self.t_design_c
        )

    def scale_space_heating(
        self,
        q_sh_profile_w: np.ndarray,
        q_design_w: np.ndarray,
        tick: int,
    ) -> np.ndarray:
        """Space-heating demand per consumer at *tick*, override-aware (§4.5).

        Returns the profile values unchanged when no override is active.
        """
        q_sh = np.asarray(q_sh_profile_w, dtype=float)
        if self.override_t_amb_c is None:
            return q_sh
        f_profile = float(self.degree_hour_factor(float(self.t_amb_c[tick])))
        f_override = float(self.degree_hour_factor(self.override_t_amb_c))
        if f_profile >= EPSILON:
            return q_sh * (f_override / f_profile)
        # summer regime: profile SH ≈ 0, no ratio exists — design-load form
        return np.asarray(q_design_w, dtype=float) * f_override
