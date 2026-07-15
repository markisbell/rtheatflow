"""Plant control endpoints (SPEC §7): the heating curve (gleitender Betrieb).

``GET/POST /heatingcurve`` — curve parameters plus the 3G/4G presets that
make the temperature-lowering narrative one click (SPEC §4.2). The Δp
control endpoints (``/dpcontrol``) ship with the M4 controller — no stub
routes; the API surface pins only what exists.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..heating_curve import PRESETS, from_config
from ..models import HeatingCurveConfig
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
