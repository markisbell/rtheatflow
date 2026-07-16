"""Loadgen — archetype demand assignment onto a network (SPEC §4.5, §8 NetzStudio).

Given a catalog network and a :class:`LoadgenPolicy`, :func:`assign` maps
building archetypes from the committed cache (``data/profiles/``) onto the
network's consumer nodes and produces

* a full **consumers document** (drop-in replacement for the network's
  ``consumers.json`` content — same nodes, new profiles),
* a matching **weather document** — the ambient temperature is re-derived
  from the chosen archetype-year day by inverting the §4.5 degree-hour
  factor (as the demo_dorf generator does), so the live override math stays
  self-consistent with the new demand,
* an assignment table and the NetzStudio KPIs: design load, peak load,
  trench length, **linear heat density** [MWh/(m·a)] (the ≥1–1.5 viability
  rule of thumb), plus the load-duration curve for the preview Sparkline.

Everything is deterministic given the policy (seeded jitter, rotating DHW
variants) — scenario recipes replay bit-identically (SPEC §4.6).

The **temperature preset** couples the §4.2 story: ``3G`` / ``4G`` selects
archetype return temperatures (radiators return hotter) and — on apply —
the matching heating-curve preset.
"""
from __future__ import annotations

from typing import Literal, Optional

import numpy as np
from pydantic import BaseModel, Field

from ..consumers import ArchetypeLibrary, archetype_profile, pick_day
from ..net_inputs import NetInputs

KELVIN = 273.15

#: archetype return temperatures [°C] per temperature preset (SPEC §4.5:
#: heating-system type → return-temperature behavior; 4G assumes low-temp
#: emitters). Fallback by house type for archetypes not listed.
TRETURN_C: dict[str, dict[str, float]] = {
    "3G": {"EFH_ALT_4P": 55.0, "EFH_SAN_4P": 45.0, "MFH_ALT_10WE": 50.0,
           "EFH": 55.0, "MFH": 50.0},
    "4G": {"EFH_ALT_4P": 45.0, "EFH_SAN_4P": 35.0, "MFH_ALT_10WE": 40.0,
           "EFH": 45.0, "MFH": 40.0},
}

#: §4.5 degree-hour design points for the weather inversion
T_ROOM_C = 20.0
T_DESIGN_C = -12.0


class LoadgenPolicy(BaseModel):
    """NetzStudio column 2 (SPEC §4.5): mix, scaling, seed, DHW variants,
    day selection, temperature preset."""

    archetypes: Optional[list[str]] = None      # subset of the cache; None = all
    mode: Literal["round_robin", "random"] = "round_robin"
    seed: int = Field(default=0, ge=0)
    scale: float = Field(default=1.0, gt=0, le=10)
    day_percentile: float = Field(default=0.9, ge=0, le=1)
    jitter: bool = True                          # ±30 min shift + amplitude
    dhw_variants: bool = True                    # rotate stochastic DHW draws
    temperature_preset: Optional[Literal["3G", "4G"]] = None


def _treturn_c(archetype_id: str, house_type: str, preset: str | None) -> float:
    table = TRETURN_C[preset or "3G"]
    return table.get(archetype_id, table.get(house_type, 50.0))


def assign(inputs: NetInputs, library: ArchetypeLibrary,
           policy: LoadgenPolicy) -> dict:
    """Assign archetype demand to *inputs*' consumer nodes (no apply).

    Returns ``consumers_doc`` / ``weather_doc`` (drop-in file contents),
    ``assignments``, ``kpis``, ``load_kw`` (chronological total) and
    ``duration_kw`` (load-duration curve, descending).
    """
    if not library.available:
        raise FileNotFoundError(
            f"no archetype cache at {library.dir} "
            "(run scripts/generate_profiles.py)")
    ids = policy.archetypes or library.ids()
    unknown = [a for a in ids if a not in library.ids()]
    if unknown:
        raise KeyError(f"unknown archetype(s) {unknown} "
                       f"(cache has {library.ids()})")

    steps = inputs.consumers.steps
    resolution = inputs.consumers.resolution_minutes
    rng = np.random.default_rng(int(policy.seed))

    # day window from the FIRST selected archetype's year (coherent weather)
    ref_year = np.asarray(library.load(ids[0])["q_sh_w"], dtype=float)
    day = pick_day(ref_year, policy.day_percentile)

    consumers_out: list[dict] = []
    assignments: list[dict] = []
    dhw_counter: dict[str, int] = {}
    total = np.zeros(steps)
    for i, c in enumerate(inputs.consumers.consumers):
        arch_id = (ids[i % len(ids)] if policy.mode == "round_robin"
                   else ids[int(rng.integers(0, len(ids)))])
        meta = library.meta(arch_id)
        variant = None
        if policy.dhw_variants:
            variant = dhw_counter.get(arch_id, 0)
            dhw_counter[arch_id] = variant + 1
        profile = archetype_profile(
            library, arch_id, steps=steps, resolution_minutes=resolution,
            day=day, seed=int(policy.seed) + i * 101, variant=variant,
            scale=policy.scale, jitter=policy.jitter)
        treturn = _treturn_c(arch_id, meta.get("house_type", "EFH"),
                             policy.temperature_preset)
        name = c.name or f"consumer_{c.node}"
        consumers_out.append({
            "node": c.node,
            "name": name,
            "q_sh_w": [round(float(x), 1) for x in profile.q_sh_w],
            "q_dhw_w": [round(float(x), 1) for x in profile.q_dhw_w],
            "treturn_k": [round(treturn + KELVIN, 2)] * steps,
            "annual_kwh": round(profile.annual_kwh, 1),
            "q_design_w": round(profile.q_design_w, 1),
            "t_supply_min_c": c.t_supply_min_c,
            "building": arch_id,
        })
        assignments.append({
            "node": c.node, "name": name, "archetype": arch_id,
            "q_design_w": round(profile.q_design_w, 1),
            "annual_kwh": round(profile.annual_kwh, 1),
            "treturn_c": treturn,
        })
        total += profile.q_sh_w + profile.q_dhw_w

    consumers_doc = {"resolution_minutes": resolution, "steps": steps,
                     "consumers": consumers_out}
    weather_doc = _weather_doc(inputs, library, ids[0], day, steps, resolution)

    trench_km = float(sum(p.length_km for p in inputs.pipes.pipes))
    annual_kwh = float(sum(a["annual_kwh"] for a in assignments))
    lhd = (annual_kwh / 1000.0) / (trench_km * 1000.0) if trench_km else None
    duration = np.sort(total)[::-1] / 1000.0
    stride = max(1, len(duration) // 192)
    kpis = {
        "n_consumers": len(assignments),
        "design_load_kw": round(float(sum(
            a["q_design_w"] for a in assignments)) / 1000.0, 1),
        "peak_load_kw": round(float(total.max()) / 1000.0, 1),
        "mean_load_kw": round(float(total.mean()) / 1000.0, 1),
        "annual_mwh": round(annual_kwh / 1000.0, 1),
        "trench_km": round(trench_km, 3),
        "linear_heat_density_mwh_per_m_a": (round(lhd, 3)
                                            if lhd is not None else None),
        "day_of_year": int(day),
        "temperature_preset": policy.temperature_preset,
    }
    return {
        "consumers_doc": consumers_doc,
        "weather_doc": weather_doc,
        "assignments": assignments,
        "kpis": kpis,
        "load_kw": [round(float(x) / 1000.0, 3) for x in total],
        "duration_kw": [round(float(x), 3) for x in duration[::stride]],
    }


def _weather_doc(inputs: NetInputs, library: ArchetypeLibrary,
                 ref_id: str, day: int, steps: int,
                 resolution: int) -> dict:
    """Invert the §4.5 degree-hour factor from the reference archetype's day
    so demand and weather stay self-consistent (override math holds)."""
    ref = library.load(ref_id)
    spd = 24 * 60 // 15
    year = np.asarray(ref["q_sh_w"], dtype=float)
    n_days = (steps * resolution) // (24 * 60)
    hours_per_step = resolution / 60.0
    t_amb: list[float] = []
    for d in range(max(1, n_days)):
        start = ((day + d) * spd) % len(year)
        day_sh = year[start:start + spd]
        f_mean = float(day_sh.mean() / ref["q_design_w"])
        t_mean = T_ROOM_C - f_mean * (T_ROOM_C - T_DESIGN_C)
        steps_this_day = steps // max(1, n_days)
        hours = np.arange(steps_this_day) * hours_per_step
        # diurnal sinusoid: warmest at 14:00, coldest around 02:00
        t_amb += [round(float(t_mean + 2.5 * np.cos(
            (h - 14.0) / 24.0 * 2 * np.pi)), 2) for h in hours]
    t_amb = (t_amb + [t_amb[-1]] * steps)[:steps]
    # ground temperature: keep the network's own profile (slow seasonal)
    t_ground = list(inputs.weather.t_ground_c)
    if len(t_ground) != steps:  # weather file may differ in resolution
        src = np.asarray(t_ground, dtype=float)
        idx = (np.arange(steps, dtype=np.int64) * len(src)) // steps
        t_ground = [round(float(x), 2) for x in src[idx]]
    return {"resolution_minutes": resolution, "steps": steps,
            "t_amb_c": t_amb, "t_ground_c": t_ground}


def apply_policy(inputs: NetInputs, library: ArchetypeLibrary,
                 policy: LoadgenPolicy) -> NetInputs:
    """Assigned inputs: same topology, new consumers + matching weather;
    with a temperature preset the slack gets the matching §4.2 curve preset."""
    from ..data_loader import cross_validate
    from ..models import ConsumersFile, ProducersFile, WeatherFile

    result = assign(inputs, library, policy)
    producers = inputs.producers
    if policy.temperature_preset is not None:
        doc = producers.model_dump(exclude_none=True)
        for p in doc["producers"]:
            if p["kind"] == "slack":
                p["heating_curve"] = {"preset": policy.temperature_preset}
        producers = ProducersFile.model_validate(doc)
    out = NetInputs(
        name=inputs.name,
        structure=inputs.structure,
        pipes=inputs.pipes,
        consumers=ConsumersFile.model_validate(result["consumers_doc"]),
        producers=producers,
        weather=WeatherFile.model_validate(result["weather_doc"]),
    )
    cross_validate(out)
    return out
