"""Sensor / measurement layer (SPEC §8a).

M1 ships only :func:`_r`, the single JSON-safe rounding helper used for every
float that reaches the wire (blueprint convention: defined once, imported
everywhere). The ``MeasurementSet`` (heat meters, T/p sensors, fidelity
modes, presets) arrives with M2 (default preset stub, per SPEC §8a interim
rule) and M5 (full sensor CRUD).
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
