"""Buffer storage (Pufferspeicher) — SPEC §4.4.

A storage bridges one trench node's supply/return junction pair with **two
branches**, of which **exactly one is active per tick**:

* **charge**    — a ``heat_consumer`` (supply → return) with the classic
  ``qext_w`` + ``treturn_k`` pair: it draws the charge power from the net and
  returns at the store's bottom temperature.
* **discharge** — a ``circ_pump_const_mass_flow`` (return → supply) with
  ``type="t"`` and **no** ``p_flow_bar``: it pushes a fixed mass flow at the
  store's top temperature back into the supply side. ⚠ Runtime-verified
  (2026-07-16, pandapipes 0.14.0): the default ``type="pt"`` **fixes the
  pressure at the flow junction** (`CirculationPump.create_pit_node_entries`
  → `set_fixed_node_entries(..., 'p')`) and fights the slack's pressure
  field — the solve diverges. The pressure-free ``type="t"`` variant
  converges and coexists with the single slack.
* **idle**      — the charge branch drops to the SPEC §3.2 canonical floor
  pair (tiny ``controlled_mdot_kg_per_s`` + standby ``qext_w``) and the
  discharge pump goes ``in_service=False`` — the stub pipes never reach zero
  flow (zero-flow rows are singular).

The **bookkeeping controller** integrates the realized heat flow into the
state of charge (kWh) with power and capacity limits (StorageController
tutorial pattern): the *requested* mode is user state; the *active* branch
per tick is the request after limits (a full store stops charging by itself).
``mass_storage`` is explicitly *not* suitable for thermal storage (SPEC §4.4).
"""
from __future__ import annotations

from dataclasses import dataclass

MODES = ("idle", "charge", "discharge")

#: SPEC §3.2 canonical idle floor for the charge branch (pair 3).
IDLE_MDOT_KG_PER_S = 0.02
IDLE_QEXT_W = 100.0

#: nominal cp for sizing the discharge mass flow from the power request
#: (the realized heat is integrated from the solved results, not from this)
CP_NOMINAL_J_PER_KG_K = 4190.0

#: activation threshold: below this power request the storage idles [kW]
MIN_ACTIVE_KW = 1e-3


@dataclass
class BufferStorage:
    """One placed storage: element handles + bookkeeping state."""

    sid: int                    # platform-unique storage id (wire id)
    node: str
    name: str
    capacity_kwh: float
    power_kw: float             # charge/discharge power limit
    charge_element: int         # net.heat_consumer index
    discharge_element: int      # net.circ_pump_mass index
    mode: str = "idle"          # requested: "idle" | "charge" | "discharge"
    soc_kwh: float = 0.0
    t_top_c: float = 80.0       # discharge flow temperature (store top)
    t_bottom_c: float = 45.0    # charge return temperature (store bottom)
    eff: float = 0.95           # one-way efficiency (√ round-trip)
    active: str = "idle"        # branch actually active this tick (post-limits)
    q_kw: float = 0.0           # last realized heat flow (+charge / −discharge)

    def desired_state(self, dt_h: float) -> tuple[str, float]:
        """(active_branch, |power_kw|) after power/capacity/SoC limits."""
        if self.mode == "charge":
            room_kwh = max(0.0, self.capacity_kwh - self.soc_kwh)
            p = self.power_kw
            if dt_h > 0:
                p = min(p, room_kwh / (self.eff * dt_h))
            if p > MIN_ACTIVE_KW:
                return "charge", p
        elif self.mode == "discharge":
            avail_kwh = max(0.0, self.soc_kwh)
            p = self.power_kw
            if dt_h > 0:
                p = min(p, avail_kwh * self.eff / dt_h)
            if p > MIN_ACTIVE_KW:
                return "discharge", p
        return "idle", 0.0

    def discharge_mdot(self, power_kw: float) -> float:
        """Nominal discharge mass flow for a power request over the store's
        design spread (realized heat is read from the solve afterwards)."""
        dt_k = max(1.0, self.t_top_c - self.t_bottom_c)
        return power_kw * 1000.0 / (CP_NOMINAL_J_PER_KG_K * dt_k)

    def integrate(self, q_kw: float, dt_h: float) -> None:
        """Advance SoC by holding ``q_kw`` (+charge / −discharge) for *dt_h*
        hours, one-way efficiency applied, clamped to [0, capacity]."""
        self.q_kw = float(q_kw)
        if q_kw > 0:
            self.soc_kwh += q_kw * self.eff * dt_h
        else:
            self.soc_kwh += q_kw / self.eff * dt_h
        self.soc_kwh = min(self.capacity_kwh, max(0.0, self.soc_kwh))

    def config(self) -> dict:
        """Static configuration (REST inventory / scenario recipe)."""
        return {
            "id": self.sid, "node": self.node, "name": self.name,
            "capacity_kwh": self.capacity_kwh, "power_kw": self.power_kw,
            "mode": self.mode, "t_top_c": self.t_top_c,
            "t_bottom_c": self.t_bottom_c, "eff": self.eff,
        }
