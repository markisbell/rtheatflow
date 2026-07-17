"""Pydantic v2 models for the five-file data contract (SPEC §5).

The five DH-native input documents:

* ``network_structure.json`` — one entry per **trench node**; the builder
  auto-creates the supply/return junction *pair* per entry (suffix ``_s``/``_r``),
  so topology files stay single-sided and human-editable.
* ``pipes.json`` — one entry per trench; the builder creates the supply *and*
  the return pipe.
* ``consumers.json`` — profile rows **are** the ``heat_consumer`` elements
  (row order = element index). Demand is split into ``q_sh_w`` (space heating)
  and ``q_dhw_w`` (domestic hot water) per SPEC §4.5 — the split makes the live
  weather-override scaling well-defined.
* ``producers.json`` — exactly one ``slack`` (``circ_pump_const_pressure``);
  secondary producers are ``heat_exchanger`` (fixed feed-in) or ``pump_mass``.
* ``weather.json`` — ambient + ground temperature, the master input.

Cross-document validation lives in :mod:`rtheatflow.data_loader`.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# network_structure.json
# ---------------------------------------------------------------------------

class StructureJunction(_StrictModel):
    name: str
    kind: Literal["node", "consumer", "plant", "cabinet"] = "node"
    geo: tuple[float, float]  # (lat, lon) — WGS84, Leaflet-native order
    pn_bar: float = Field(gt=0)


class NetworkStructure(_StrictModel):
    name: str
    junctions: list[StructureJunction] = Field(min_length=2)

    @field_validator("junctions")
    @classmethod
    def _unique_names(cls, v: list[StructureJunction]) -> list[StructureJunction]:
        names = [j.name for j in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate junction names: {sorted(dupes)}")
        return v


# ---------------------------------------------------------------------------
# pipes.json
# ---------------------------------------------------------------------------

class PipeSpec(_StrictModel):
    """One trench (the builder creates the supply and the return pipe)."""

    from_node: str
    to_node: str
    length_km: float = Field(gt=0)
    std_type: Optional[str] = None
    # custom-parameter alternative to std_type (SPEC §3.1 / §10.6 0.14 units)
    inner_diameter_mm: Optional[float] = Field(default=None, gt=0)
    u_w_per_m2k: Optional[float] = Field(default=None, ge=0)
    k_mm: Optional[float] = Field(default=None, ge=0)
    sections: int = Field(default=1, ge=1)
    geometry: Optional[list[tuple[float, float]]] = None  # [(lat, lon), ...]

    @model_validator(mode="after")
    def _std_type_xor_parameters(self) -> "PipeSpec":
        if self.std_type is not None:
            # Never pass k_mm/u_w_per_m2k alongside a std type — deprecated in
            # 0.14 and silently overrides the std-type value (SPEC §3.1).
            if any(x is not None for x in (self.inner_diameter_mm, self.u_w_per_m2k, self.k_mm)):
                raise ValueError(
                    f"pipe {self.from_node}->{self.to_node}: std_type and explicit "
                    "parameters (inner_diameter_mm/u_w_per_m2k/k_mm) are mutually exclusive"
                )
        else:
            if self.inner_diameter_mm is None or self.u_w_per_m2k is None:
                raise ValueError(
                    f"pipe {self.from_node}->{self.to_node}: needs std_type or "
                    "(inner_diameter_mm + u_w_per_m2k [+ k_mm])"
                )
        if self.from_node == self.to_node:
            raise ValueError(f"pipe {self.from_node}->{self.to_node}: self-loop")
        return self


class PipesFile(_StrictModel):
    pipes: list[PipeSpec] = Field(min_length=1)


# ---------------------------------------------------------------------------
# consumers.json
# ---------------------------------------------------------------------------

class ConsumerSpec(_StrictModel):
    """One substation = one ``heat_consumer`` element.

    Control: ``qext_w`` (= ``q_sh_w + q_dhw_w``, floored per SPEC §3.2) is
    always applied; exactly **one** partner setpoint must be given —
    ``treturn_k`` profile (platform default, pandapipes pair 5), scalar
    ``deltat_k`` (pair 4), or scalar ``controlled_mdot_kg_per_s`` (pair 3,
    also the canonical bypass shape).
    """

    node: str
    name: Optional[str] = None
    q_sh_w: list[float]
    q_dhw_w: list[float]
    treturn_k: Optional[list[float]] = None
    deltat_k: Optional[float] = Field(default=None, gt=0)
    controlled_mdot_kg_per_s: Optional[float] = Field(default=None, gt=0)
    annual_kwh: Optional[float] = Field(default=None, ge=0)
    q_design_w: float = Field(gt=0)  # summer-override formula + marker sizing
    t_supply_min_c: float = 60.0     # UI supply-temperature warning (DHW hygiene)
    building: Optional[str] = None   # archetype id (data/profiles/index.json)

    @model_validator(mode="after")
    def _exactly_one_partner(self) -> "ConsumerSpec":
        partners = [
            self.treturn_k is not None,
            self.deltat_k is not None,
            self.controlled_mdot_kg_per_s is not None,
        ]
        if sum(partners) != 1:
            raise ValueError(
                f"consumer at {self.node!r}: exactly one of treturn_k / deltat_k / "
                "controlled_mdot_kg_per_s must be set (partner to qext_w; SPEC §3.2)"
            )
        if any(q < 0 for q in self.q_sh_w) or any(q < 0 for q in self.q_dhw_w):
            raise ValueError(f"consumer at {self.node!r}: negative demand values")
        if self.treturn_k is not None and any(t < 273.15 for t in self.treturn_k):
            raise ValueError(
                f"consumer at {self.node!r}: treturn_k below 273.15 K — temperatures "
                "are Kelvin (SPEC §3.2: a tutorial passes 50 K = -223 °C; don't copy)"
            )
        return self


class ConsumersFile(_StrictModel):
    resolution_minutes: int = Field(gt=0)
    steps: int = Field(gt=0)
    consumers: list[ConsumerSpec] = Field(min_length=1)


# ---------------------------------------------------------------------------
# producers.json
# ---------------------------------------------------------------------------

class HeatingCurveConfig(_StrictModel):
    """SPEC §4.2 heating-curve parameters (or a named preset)."""

    preset: Optional[Literal["3G", "4G"]] = None
    t_amb_design_c: float = -12.0
    t_flow_design_c: float = 110.0
    t_flow_min_c: float = 70.0
    t_room_c: float = 20.0
    n: float = Field(default=1.0, gt=0)  # 1 = linear; ~1.3 radiator


class DpControlConfig(_StrictModel):
    """SPEC §4.3 worst-point Δp control (controller ships in M4)."""

    mode: Literal["fixed", "controlled"] = "fixed"
    setpoint_bar: float = Field(default=0.7, ge=0.3, le=2.0)
    k: float = 0.5
    max_step_bar: float = 0.05


class ProducerSpec(_StrictModel):
    node: str
    name: Optional[str] = None
    kind: Literal["slack", "heat_exchanger", "pump_mass"]
    # slack (circ_pump_const_pressure)
    p_flow_bar: Optional[float] = Field(default=None, gt=0)
    plift_bar: Optional[float] = Field(default=None, gt=0)
    t_flow_k: Optional[float] = Field(default=None, gt=273.15)
    # heat_exchanger: positive feed-in dispatch [W] per step; the builder/
    # simulator negates it onto qext_w (feed-in = negative qext_w, SPEC §3.1)
    qext_w: Optional[list[float]] = None
    inner_diameter_mm: Optional[float] = Field(default=None, gt=0)
    # pump_mass (circ_pump_const_mass_flow): scalar or [steps]
    mdot_flow_kg_per_s: Optional[float | list[float]] = None
    heating_curve: Optional[HeatingCurveConfig] = None
    dp_control: Optional[DpControlConfig] = None

    @model_validator(mode="after")
    def _kind_specific(self) -> "ProducerSpec":
        if self.kind == "slack":
            missing = [f for f in ("p_flow_bar", "plift_bar", "t_flow_k")
                       if getattr(self, f) is None]
            if missing:
                raise ValueError(f"slack producer at {self.node!r}: missing {missing}")
        elif self.kind == "heat_exchanger":
            if self.inner_diameter_mm is None:
                raise ValueError(
                    f"heat_exchanger producer at {self.node!r}: inner_diameter_mm required"
                )
            if self.qext_w is None:
                raise ValueError(
                    f"heat_exchanger producer at {self.node!r}: qext_w dispatch profile required"
                )
        elif self.kind == "pump_mass":
            # p_flow_bar is OPTIONAL: without it the builder creates the
            # pressure-free ``type="t"`` variant (fixed mdot + flow temp) —
            # the only pump_mass shape that coexists with the pressure slack
            # (M4 runtime discovery; the live API has the same rule). An
            # explicit p_flow_bar keeps the expert "pt" booster semantics.
            missing = [f for f in ("mdot_flow_kg_per_s", "t_flow_k")
                       if getattr(self, f) is None]
            if missing:
                raise ValueError(f"pump_mass producer at {self.node!r}: missing {missing}")
        return self


class ProducersFile(_StrictModel):
    producers: list[ProducerSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _exactly_one_slack(self) -> "ProducersFile":
        n_slack = sum(1 for p in self.producers if p.kind == "slack")
        if n_slack != 1:
            raise ValueError(
                f"exactly one slack (circ_pump_const_pressure) required, got {n_slack} "
                "(single-pressure-slack rule, SPEC §3.1)"
            )
        return self


# ---------------------------------------------------------------------------
# weather.json
# ---------------------------------------------------------------------------

class WeatherFile(_StrictModel):
    resolution_minutes: int = Field(gt=0)
    steps: int = Field(gt=0)
    t_amb_c: list[float]
    t_ground_c: list[float]

    @model_validator(mode="after")
    def _lengths(self) -> "WeatherFile":
        for field in ("t_amb_c", "t_ground_c"):
            if len(getattr(self, field)) != self.steps:
                raise ValueError(
                    f"weather.{field}: length {len(getattr(self, field))} != steps {self.steps}"
                )
        return self
