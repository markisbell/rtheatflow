"""Worst-point differential-pressure control (SPEC §4.3, Schlechtpunktregelung).

Per tick, **after** the solve, the controller nudges the plant pump's
``plift_bar`` **once** by a clamped proportional step toward the Δp setpoint:

    plift_bar += clamp(K · (dp_set − dp_worst), −max_step, +max_step)

Deliberately **no inner re-solve loop** — the pump visibly *reacts over
ticks*, like a real speed-controlled pump, and the loop stays O(1 solve/tick).

Two modes (SPEC §4.3):

* ``controlled`` — Schlechtpunktregelung as above.
* ``fixed``      — "ungeregelte Pumpe": ``plift_bar`` stays wherever the user
  set it (teaching the difference — and the oversizing penalty).

Blindness semantics (SPEC §8a, fixed in M5 — the documented decision, see
CLAUDE.md): the controller consumes the **observed** worst-point Δp only
(``observed_summary.dp_worst_bar`` = min over *metered* consumers). It never
touches ground truth. Degradation ladder:

* **No usable Δp reading** (no consumer meters — e.g. presets ``clear`` /
  ``plant_only`` — or every meter still inside its cold-start window in
  standard mode): ``dp_worst_bar`` is ``None`` → the controller **holds**
  ``plift_bar``. Holding the lift *is* "controlling on plant Δp" (SPEC §8a):
  the plant pump keeps its own differential pressure constant, like a real
  constant-Δp pump with no remote sensor.
* **Metered, but the TRUE worst point carries no meter**: the controller
  regulates on the best *measured* Δp — exactly what a real Schlechtpunkt
  controller does; it cannot know better. The frame flags this as
  ``controls.dp_control.blind_spot`` (UI-visible), because the platform —
  unlike the operator — knows the truth: the unmetered worst point may be
  starved while the measured one sits at the setpoint.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import DpControlConfig

#: Setpoint band (SPEC §4.3: user-adjustable 0.3–2.0 bar; 422 outside).
SETPOINT_MIN_BAR = 0.3
SETPOINT_MAX_BAR = 2.0

#: Physical pump-lift clamp range ("clamped rate AND range", SPEC §4.3).
PLIFT_MIN_BAR = 0.1
PLIFT_MAX_BAR = 10.0


@dataclass
class DpController:
    """One plant pump Δp controller (state lives across ticks)."""

    mode: str = "fixed"                 # "controlled" | "fixed"
    setpoint_bar: float = 0.7
    k: float = 0.5                      # proportional gain [1/tick]
    max_step_bar: float = 0.05          # rate clamp per tick
    plift_min_bar: float = PLIFT_MIN_BAR
    plift_max_bar: float = PLIFT_MAX_BAR
    #: what the controller last saw (None = blind — no metered worst point)
    last_dp_observed_bar: float | None = None

    def step(self, plift_bar: float, dp_worst_observed_bar: float | None) -> float:
        """One post-solve control step; returns the next ``plift_bar``.

        ``None`` means the observed layer carries no worst-point Δp (no
        metered consumers) — the controller **holds** (blindness principle).
        """
        self.last_dp_observed_bar = dp_worst_observed_bar
        if self.mode != "controlled" or dp_worst_observed_bar is None:
            return float(plift_bar)
        delta = self.k * (self.setpoint_bar - float(dp_worst_observed_bar))
        delta = max(-self.max_step_bar, min(self.max_step_bar, delta))
        return max(self.plift_min_bar,
                   min(self.plift_max_bar, float(plift_bar) + delta))

    def params(self) -> dict:
        return {
            "mode": self.mode,
            "setpoint_bar": self.setpoint_bar,
            "k": self.k,
            "max_step_bar": self.max_step_bar,
            "plift_min_bar": self.plift_min_bar,
            "plift_max_bar": self.plift_max_bar,
        }

    @classmethod
    def from_config(cls, cfg: DpControlConfig | None) -> "DpController":
        """Build from a producers.json ``dp_control`` block (or defaults)."""
        if cfg is None:
            return cls()
        return cls(mode=cfg.mode, setpoint_bar=cfg.setpoint_bar,
                   k=cfg.k, max_step_bar=cfg.max_step_bar)
