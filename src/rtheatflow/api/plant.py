"""Plant control endpoints (SPEC §7): heating curve + worst-point Δp control.

``GET/POST /heatingcurve`` — curve parameters plus the 3G/4G presets that
make the temperature-lowering narrative one click (SPEC §4.2).

``GET/POST /dpcontrol`` — the §4.3 Schlechtpunktregelung: setpoint (0.3–2.0
bar, **422** outside the band per the §7 error-code discipline), mode
``controlled``/``fixed``, and — in fixed mode — the raw ``plift_bar`` knob
("ungeregelte Pumpe"). The controller itself acts once per tick after the
solve and consumes only the observed layer (see dp_control.py).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..dp_control import (
    PLIFT_MAX_BAR,
    PLIFT_MIN_BAR,
    SETPOINT_MAX_BAR,
    SETPOINT_MIN_BAR,
)
from ..heating_curve import PRESETS, from_config
from ..models import HeatingCurveConfig
from ..sensors import _r
from .runtime import App, get_app

router = APIRouter(tags=["plant"])


def _curve_payload(app: App) -> dict:
    curve = app.sim.heating_curve
    return {
        "params": curve.params() if curve is not None else None,
        "presets": {name: preset.params() for name, preset in PRESETS.items()},
    }


@router.get("/heatingcurve", summary="Heating-curve parameters + presets")
def get_heatingcurve() -> dict:
    """Active curve (``params: null`` = fixed ``t_flow_k``, no curve) and the
    3G/4G preset parameter sets."""
    return _curve_payload(get_app())


@router.post("/heatingcurve", summary="Set the heating curve")
def set_heatingcurve(cfg: HeatingCurveConfig) -> dict:
    """Install a curve from explicit parameters or ``{"preset": "3G"|"4G"}``.

    Takes effect on the next tick (the simulator writes ``t_flow_k`` per
    step). The weather model's degree-hour coupling follows the new curve's
    design points (SPEC §4.5)."""
    app = get_app()
    curve = from_config(cfg)
    sim = app.sim
    sim.heating_curve = curve
    sim.weather.t_room_c = curve.t_room_c
    sim.weather.t_design_c = curve.t_amb_design_c
    return _curve_payload(app)


# ---------------------------------------------------------------------------
# Worst-point Δp control (SPEC §4.3)
# ---------------------------------------------------------------------------

class DpControlBody(BaseModel):
    """Partial update; pydantic bounds make out-of-band values a **422**
    (semantic limit — SPEC §7: "Δp setpoint out of band")."""

    mode: Optional[Literal["controlled", "fixed"]] = None
    setpoint_bar: Optional[float] = Field(
        default=None, ge=SETPOINT_MIN_BAR, le=SETPOINT_MAX_BAR,
        description=f"Δp setpoint [bar], {SETPOINT_MIN_BAR}…{SETPOINT_MAX_BAR}")
    k: Optional[float] = Field(default=None, gt=0, le=5)
    max_step_bar: Optional[float] = Field(default=None, gt=0, le=1)
    plift_bar: Optional[float] = Field(
        default=None, ge=PLIFT_MIN_BAR, le=PLIFT_MAX_BAR,
        description="direct pump setting (effective in fixed mode)")


def _dp_payload(app: App) -> dict:
    sim = app.sim
    plift = float(sim.net.circ_pump_pressure.at[sim.index.slack, "plift_bar"])
    return {
        **sim.dp_control.params(),
        "plift_bar": _r(plift),
        "dp_worst_observed_bar": _r(sim.dp_control.last_dp_observed_bar),
        "setpoint_min_bar": SETPOINT_MIN_BAR,
        "setpoint_max_bar": SETPOINT_MAX_BAR,
    }


@router.get("/dpcontrol", summary="Worst-point Δp control state")
def get_dpcontrol() -> dict:
    """Controller mode/setpoint/gains, the live pump lift, and the last
    observed worst-point Δp (``null`` = controller is blind, SPEC §8a)."""
    return _dp_payload(get_app())


@router.post("/dpcontrol", summary="Configure the Δp control")
def set_dpcontrol(body: DpControlBody) -> dict:
    """Partial update of mode/setpoint/gains; ``plift_bar`` writes the pump
    directly (the fixed-mode teaching knob). Setpoint outside 0.3–2.0 bar is
    a 422. Takes effect on the next tick — the pump converges over ticks,
    never instantly (SPEC §4.3)."""
    app = get_app()
    dp = app.sim.dp_control
    if body.mode is not None:
        dp.mode = body.mode
    if body.setpoint_bar is not None:
        dp.setpoint_bar = float(body.setpoint_bar)
    if body.k is not None:
        dp.k = float(body.k)
    if body.max_step_bar is not None:
        dp.max_step_bar = float(body.max_step_bar)
    if body.plift_bar is not None:
        app.sim.set_plift(body.plift_bar)
    return _dp_payload(app)
