"""Generate heat-demand archetype profiles for rtheatflow.

Space heating: demandlib VDI 4655 (deterministic typical days, German TRY zone),
scaled by the archetype's annual demand. DHW: OpenDHW (stochastic DHWcalc-style
draw-offs), several seeds per archetype so co-located buildings do not draw
synchronously. Output: one JSON file per archetype plus an index.json, in the
q_sh_w / q_dhw_w split required by the consumers.json data contract (SPEC §5).

Usage:  python scripts/generate_profiles.py [--out data/profiles] [--year 2023]
"""
import argparse
import json
import random
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import OpenDHW
from demandlib import vdi

RESOLUTION_MIN = 15
TRY_ZONE = 4                # DWD TRY zone 4 (Potsdam reference climate)
DHW_TEMP_DT_K = 35          # DHWcalc convention: cold inlet ~10 degC -> ~45 degC
DHW_SEED_VARIANTS = 3

# Prototype archetype catalog. Annual space-heating demands follow TABULA-style
# German stock values; DHW is defined via liters/person/day (DHWcalc convention,
# 40 L/d @ dT=35K ~= 594 kWh/person/a).
ARCHETYPES = [
    dict(id="EFH_ALT_4P", name="EFH Bestand unsaniert, 4 Personen",
         house_type="EFH", n_persons=4, n_units=1,
         q_heiz_a_kwh=18000, dhw_l_per_person_day=40, building_type="SFH"),
    dict(id="EFH_SAN_4P", name="EFH saniert (KfW-Niveau), 4 Personen",
         house_type="EFH", n_persons=4, n_units=1,
         q_heiz_a_kwh=9000, dhw_l_per_person_day=40, building_type="SFH"),
    dict(id="MFH_ALT_10WE", name="MFH Bestand, 10 Wohneinheiten (25 Personen)",
         house_type="MFH", n_persons=25, n_units=10,
         q_heiz_a_kwh=95000, dhw_l_per_person_day=40, building_type="MFH"),
]


def vdi_space_heating_w(arche, year):
    """VDI 4655 space-heating profile, resampled to 15 min, scaled to annual kWh -> W."""
    house = {
        "name": arche["id"],
        "house_type": arche["house_type"],
        "N_Pers": arche["n_persons"],
        "N_WE": arche["n_units"],
        "Q_Heiz_a": arche["q_heiz_a_kwh"],
        "Q_TWW_a": 0,        # DHW comes from OpenDHW, not VDI
        "W_a": 0,
        "copies": 0,
        "summer_temperature_limit": 15,
        "winter_temperature_limit": 5,
    }
    region = vdi.Region(
        year,
        climate=vdi.Climate().from_try_data(TRY_ZONE),
        resample_rule=f"{RESOLUTION_MIN}min",
        houses=[house],
    )
    lc = region.get_load_curve_houses()          # kWh per timestep, MultiIndex columns
    q_heiz_kwh = lc.xs("Q_Heiz_TT", level=-1, axis=1).iloc[:, 0]
    # rescale exactly to the annual target (VDI scaling is close but not exact)
    q_heiz_kwh = q_heiz_kwh * (arche["q_heiz_a_kwh"] / q_heiz_kwh.sum())
    q_sh_w = q_heiz_kwh.values * 1000.0 / (RESOLUTION_MIN / 60.0)
    return q_sh_w


def opendhw_dhw_w(arche, year, seed):
    """OpenDHW stochastic DHW profile at 60 s, resampled to 15 min -> W."""
    random.seed(seed)
    np.random.seed(seed)
    df = OpenDHW.generate_dhw_profile(
        s_step=60,
        categories=4,
        mean_drawoff_vol_per_day=arche["dhw_l_per_person_day"],
        occupancy=arche["n_persons"],
        holidays=OpenDHW.get_holidays("DE", year),
        building_type=arche["building_type"],
        weekend_weekday_factor=1.2,
    )
    df = OpenDHW.resample_water_series(df, s_step_output=RESOLUTION_MIN * 60)
    df = OpenDHW.compute_heat(df, temp_dT=DHW_TEMP_DT_K)
    return df["Heat_W"].values, df["Water_L"].sum()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/profiles")
    ap.add_argument("--year", type=int, default=2023)   # non-leap year -> 35040 steps
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    steps = 365 * 24 * 60 // RESOLUTION_MIN
    index = dict(
        source=f"demandlib VDI 4655 (TRY zone {TRY_ZONE}) + OpenDHW "
               f"(DHWcalc method, dT={DHW_TEMP_DT_K} K), year {args.year}",
        generated=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        resolution_minutes=RESOLUTION_MIN,
        steps=steps,
        archetypes=[],
    )

    for arche in ARCHETYPES:
        q_sh_w = vdi_space_heating_w(arche, args.year)
        assert len(q_sh_w) == steps, f"SH steps {len(q_sh_w)} != {steps}"

        variants, litres = [], []
        for i in range(DHW_SEED_VARIANTS):
            # salt the seed with the archetype id so different archetypes never
            # share draw patterns; stable across runs (reproducible cache)
            seed = zlib.crc32(f"{arche['id']}:{i}".encode()) & 0x7FFFFFFF
            q_dhw_w, annual_l = opendhw_dhw_w(arche, args.year, seed)
            assert len(q_dhw_w) == steps, f"DHW steps {len(q_dhw_w)} != {steps}"
            variants.append([round(float(v), 1) for v in q_dhw_w])
            litres.append(annual_l)

        dhw_kwh = [sum(v) * RESOLUTION_MIN / 60 / 1000 for v in variants]
        record = dict(
            id=arche["id"], name=arche["name"],
            house_type=arche["house_type"], n_persons=arche["n_persons"],
            n_units=arche["n_units"],
            annual_kwh=dict(space_heating=round(float(np.sum(q_sh_w)) * RESOLUTION_MIN / 60 / 1000, 1),
                            dhw_mean=round(float(np.mean(dhw_kwh)), 1)),
            q_design_w=round(float(np.max(q_sh_w)), 1),
            peak_dhw_w=round(float(max(max(v) for v in variants)), 1),
            n_variants_dhw=DHW_SEED_VARIANTS,
            file=f"{arche['id']}.json",
        )
        payload = dict(
            **record,
            resolution_minutes=RESOLUTION_MIN, steps=steps,
            dhw_l_per_person_day=arche["dhw_l_per_person_day"],
            annual_dhw_litres=[round(float(v)) for v in litres],
            q_sh_w=[round(float(v), 1) for v in q_sh_w],
            q_dhw_w_variants=variants,
        )
        (out / record["file"]).write_text(json.dumps(payload), encoding="utf-8")
        index["archetypes"].append(record)
        print(f"{arche['id']}: SH {record['annual_kwh']['space_heating']:.0f} kWh/a "
              f"(target {arche['q_heiz_a_kwh']}), peak {record['q_design_w']/1000:.1f} kW | "
              f"DHW {record['annual_kwh']['dhw_mean']:.0f} kWh/a mean of "
              f"{DHW_SEED_VARIANTS} seeds, peak {record['peak_dhw_w']/1000:.1f} kW")

    (out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nwrote {len(ARCHETYPES)} archetypes + index.json to {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
