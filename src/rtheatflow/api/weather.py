"""Weather endpoints (SPEC §4.1, §7) — the live override is the hot path.

``PUT /weather/override`` is the blueprint's ``PUT /ext/{eid}/value`` analog:
drag the outdoor temperature, watch demand and the heating curve respond on
the next tick. Values outside the plausible range (−30…45 °C) are a **422**
(semantic limit, SPEC §7 error-code discipline).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..sensors import _r
from .runtime import App, get_app

router = APIRouter(prefix="/weather", tags=["weather"])

T_AMB_MIN_C = -30.0  # plausible override band (SPEC §7: 422 out of range)
T_AMB_MAX_C = 45.0


class OverrideBody(BaseModel):
    t_amb_c: float = Field(
        ge=T_AMB_MIN_C, le=T_AMB_MAX_C,
        description=f"ambient override [°C], {T_AMB_MIN_C}…{T_AMB_MAX_C}")


def _weather_payload(app: App) -> dict:
    sim = app.sim
    tick = sim._tick(app.engine.step, app.engine.day)
    w = sim.weather
    return {
        "t_amb_c": _r(w.t_amb(tick)),                # effective (override wins)
        "profile_t_amb_c": _r(float(w.t_amb_c[tick])),
        "t_ground_c": _r(w.t_ground(tick)),
        "override": w.override_active,
        "override_t_amb_c": (_r(w.override_t_amb_c)
                             if w.override_active else None),
    }


@router.get("", summary="Current weather")
def get_weather() -> dict:
    """Effective + profile ambient temperature, ground temperature, override state."""
    return _weather_payload(get_app())


@router.put("/override", summary="Set the live ambient-temperature override")
def set_override(body: OverrideBody) -> dict:
    """Space-heating demand rescales instantly via the degree-hour factor
    (SPEC §4.5); DHW stays untouched. 422 outside −30…45 °C."""
    app = get_app()
    app.sim.weather.set_override(body.t_amb_c)
    return _weather_payload(app)


@router.delete("/override", summary="Release the override")
def clear_override() -> dict:
    """Back to the weather profile on the next tick."""
    app = get_app()
    app.sim.weather.clear_override()
    return _weather_payload(app)
