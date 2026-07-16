"""Sensor / measurement layer (SPEC §8a, M5).

Two things live here:

* :func:`_r` — the single JSON-safe rounding helper used for every float that
  reaches the wire (blueprint convention: defined once, imported everywhere).
* :class:`MeasurementSet` — the measurable layer: **which elements carry a
  measurement device**, plus the projection from the collected ground truth
  to what those devices actually show.

Device kinds (SPEC §8a):

- **Heat meter (Wärmemengenzähler)** at a consumer substation — reads
  ``q_kw, mdot, t_supply_c, t_return_c, dp_bar`` at that consumer, nothing
  else.
- **T/p sensor** at a trench node — reads supply *and* return pressure and
  temperature at that node's junction pair.
- **Plant SCADA** — the plant's own quantities are *always* measured (real
  plants are); SCADA is live telemetry, so it never degrades to the
  15-minute raster.

Fidelity modes (blueprint TAF analog, one bulk mode per SPEC §7):

- ``full`` — every channel, every simulation step.
- ``standard`` — 15-minute-window means aligned to simulated time; a channel
  is ``None`` until its first window boundary passes (honest cold start —
  a Lastgang meter never emits sub-window data). A device placed mid-window
  publishes its first (partial-window) mean at the next boundary, exactly
  like the blueprint's TAF-7 model.

The measurable layer is a **projection of ground truth**, never a parallel
computation: ``observe()`` selects sensored elements out of the collected
truth payload. Controllers (Δp!) consume only this layer — see
``dp_control.py`` for the blindness semantics.
"""
from __future__ import annotations

import math
from collections.abc import Callable
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


# Consumer heat meter (Wärmemengenzähler) channels — SPEC §8a: a substation
# meter reads heat flow, mass flow and both temperatures (+ the differential
# pressure a modern substation controller logs), nothing else.
_METER_CHANNELS = ("q_kw", "mdot_kg_per_s", "t_supply_c", "t_return_c",
                   "dp_bar")

# T/p junction-pair sensor channels (SPEC §8a): supply+return p and T.
_NODE_CHANNELS = ("p_supply_bar", "p_return_bar", "t_supply_c", "t_return_c")

#: Placement presets (SPEC §8a). A preset REPLACES the whole placement —
#: the bulk actions of the Messungen panel, not incremental additions.
PRESETS = ("all_consumers", "plant_only", "key_points", "clear")
DEFAULT_PRESET = "all_consumers"

#: Meter fidelity modes (SPEC §7/§8a). One bulk mode for every placed
#: device; the plant SCADA is always live (it is telemetry, not metering).
METER_MODES = ("full", "standard")

#: The standard-metering window in simulated minutes (German Lastgang).
WINDOW_MINUTES = 15


class MeasurementSet:
    """Which consumers/nodes carry a device + the truth→observed projection.

    ``context`` (set by the Simulator) supplies the element inventory the
    presets need: ``{"consumer_ids": [int], "plant_node": str,
    "end_nodes": [str], "node_names": [str], "worst_consumer_id": int|None}``.
    Placement mutators flip ``preset`` to ``"custom"`` — the preset name on
    the wire always describes how the current placement came to be.
    """

    def __init__(self, preset: str = DEFAULT_PRESET,
                 window_steps: int = WINDOW_MINUTES,
                 context: Callable[[], dict] | None = None):
        self.consumer_meters: set[int] = set()
        self.node_sensors: set[str] = set()
        self.mode: str = "full"
        self.preset: str = preset
        self.window_steps: int = max(1, int(window_steps))
        self._context = context
        # standard-mode window state: running accumulator + last completed
        # window means (blueprint pattern): key -> (sum, n) / key -> mean
        self._win: int = -1
        self._acc: dict = {}
        self._held: dict = {}
        if context is not None:
            self.apply_preset(preset)

    # -- context ---------------------------------------------------------------

    def set_context(self, context: Callable[[], dict]) -> None:
        self._context = context

    def _ctx(self) -> dict:
        return self._context() if self._context is not None else {}

    def _reset_windows(self) -> None:
        self._win, self._acc, self._held = -1, {}, {}

    # -- placement mutators (True if the placement actually changed) ------------

    def add_consumer_meter(self, element: int) -> bool:
        if int(element) in self.consumer_meters:
            return False
        self.consumer_meters.add(int(element))
        self.preset = "custom"
        return True

    def remove_consumer_meter(self, element: int) -> bool:
        if int(element) not in self.consumer_meters:
            return False
        self.consumer_meters.discard(int(element))
        self._drop_keys("c", int(element))
        self.preset = "custom"
        return True

    def add_node_sensor(self, node: str) -> bool:
        if str(node) in self.node_sensors:
            return False
        self.node_sensors.add(str(node))
        self.preset = "custom"
        return True

    def remove_node_sensor(self, node: str) -> bool:
        if str(node) not in self.node_sensors:
            return False
        self.node_sensors.discard(str(node))
        self._drop_keys("n", str(node))
        self.preset = "custom"
        return True

    def _drop_keys(self, kind: str, ident) -> None:
        """Forget window state of a removed device (fresh cold start on
        re-placement — a new meter has no history)."""
        for d in (self._acc, self._held):
            for key in [k for k in d if k[0] == kind and k[1] == ident]:
                del d[key]

    def prune(self, consumer_ids: set[int]) -> None:
        """Drop meters whose consumer no longer exists (runtime removal)."""
        gone = self.consumer_meters - {int(c) for c in consumer_ids}
        for el in gone:
            self.consumer_meters.discard(el)
            self._drop_keys("c", el)

    # -- fidelity ----------------------------------------------------------------

    def set_mode(self, name: str) -> None:
        """Bulk fidelity switch for every placed device (SPEC §7). Window
        state resets — after a switch the standard raster starts honestly
        cold (no fake instant readings from stale accumulators)."""
        if name not in METER_MODES:
            raise ValueError(
                f"unknown meter mode {name!r} (modes: {METER_MODES})")
        self.mode = name
        self._reset_windows()

    # -- presets -------------------------------------------------------------------

    def apply_preset(self, preset: str) -> None:
        """Bulk placement (SPEC §8a). Replaces the current placement:

        * ``all_consumers`` — heat meter at every substation (M2–M4 default).
        * ``plant_only`` — only the plant: its SCADA (always on) plus a T/p
          sensor pair at the plant node.
        * ``key_points`` — plant T/p + T/p at the net ends (leaf nodes) + a
          heat meter at the *currently known* worst-point consumer (from the
          last converged frame; before the first solve no worst point is
          known and no meter is placed — honest).
        * ``clear`` — no placed devices at all (plant SCADA remains — real
          plants are always measured).
        """
        if preset not in PRESETS:
            raise ValueError(
                f"unknown measurement preset {preset!r} (presets: {PRESETS})")
        ctx = self._ctx()
        self.consumer_meters = set()
        self.node_sensors = set()
        if preset == "all_consumers":
            self.consumer_meters = {int(c) for c in ctx.get("consumer_ids", [])}
        elif preset == "plant_only":
            if ctx.get("plant_node"):
                self.node_sensors = {str(ctx["plant_node"])}
        elif preset == "key_points":
            if ctx.get("plant_node"):
                self.node_sensors.add(str(ctx["plant_node"]))
            self.node_sensors |= {str(n) for n in ctx.get("end_nodes", [])}
            if ctx.get("worst_consumer_id") is not None:
                self.consumer_meters = {int(ctx["worst_consumer_id"])}
        # "clear": nothing placed
        self.preset = preset
        self._reset_windows()

    # -- placement payload (GET /measurements, UI panel/markers) -------------------

    def placement(self, n_consumers: int, n_nodes: int,
                  consumer_meta: list[dict] | None = None) -> dict:
        """Static placement + coverage — no solved results needed."""
        meters = sorted(self.consumer_meters)
        if consumer_meta is not None:
            by_id = {int(m["id"]): m for m in consumer_meta}
            meter_list = [
                {"id": el, "name": by_id.get(el, {}).get("name"),
                 "node": by_id.get(el, {}).get("node")}
                for el in meters
            ]
        else:
            meter_list = [{"id": el, "name": None, "node": None}
                          for el in meters]
        return {
            "preset": self.preset,
            "mode": self.mode,
            "consumer_meters": meter_list,
            "node_sensors": sorted(self.node_sensors),
            "coverage": {
                "n_consumers": int(n_consumers),
                "n_consumer_meters": len(self.consumer_meters),
                "consumer_fraction": (round(
                    len(self.consumer_meters) / n_consumers, 4)
                    if n_consumers else 0.0),
                "n_nodes": int(n_nodes),
                "n_node_sensors": len(self.node_sensors),
                "node_fraction": (round(
                    len(self.node_sensors) / n_nodes, 4)
                    if n_nodes else 0.0),
            },
        }

    # -- the projection ---------------------------------------------------------

    def observe(self, truth: dict, tick: int = 0) -> tuple[dict, dict | None]:
        """Project the collected truth payload onto the sensored elements.

        *truth* is the ``Simulator._collect()`` payload (``consumers`` /
        ``junctions`` / ``summary`` keys); *tick* is the global profile tick
        (window bookkeeping — windows are aligned to simulated time and
        survive day wraps). Returns ``(measurements, observed_summary)`` per
        the SPEC §6 wire format.

        Standard mode: on a window boundary the accumulated means become the
        published values; readings are ``None`` until the first boundary
        after placement (honest cold start, strictly no intra-window data).
        """
        consumers = truth.get("consumers") or []
        junctions = truth.get("junctions") or []
        summary = truth.get("summary") or {}
        if not summary:  # no converged physics yet — honest empty view
            return {}, None

        standard = self.mode == "standard"
        if standard:
            w = int(tick) // self.window_steps
            if w != self._win:          # window boundary: publish means
                if self._acc:
                    self._held = {k: s / n for k, (s, n) in self._acc.items()}
                self._acc = {}
                self._win = w

        def channel(key: tuple, value):
            """Full mode: pass through. Standard: accumulate, read the last
            completed window's mean (None until one closed)."""
            if not standard:
                return _r(value)
            if value is not None:
                s_, n_ = self._acc.get(key, (0.0, 0))
                self._acc[key] = (s_ + float(value), n_ + 1)
            held = self._held.get(key)
            return _r(held) if held is not None else None

        # -- substation heat meters -------------------------------------------
        meters = []
        for c in consumers:
            el = int(c["id"])
            if el not in self.consumer_meters:
                continue
            entry = {"id": el, "name": c.get("name"), "node": c.get("node")}
            for ch in _METER_CHANNELS:
                entry[ch] = channel(("c", el, ch), c.get(ch))
            meters.append(entry)

        # -- T/p junction-pair sensors ------------------------------------------
        junc: dict[tuple[str, str], dict] = {}
        node_names: set[str] = set()
        for j in junctions:
            junc[(j["name"], j["side"])] = j
            node_names.add(j["name"])
        nodes = []
        for node in sorted(self.node_sensors):
            js, jr = junc.get((node, "s")), junc.get((node, "r"))
            if js is None and jr is None:
                continue  # node vanished (swap) — nothing to read
            entry = {"node": node}
            values = {
                "p_supply_bar": js.get("p_bar") if js else None,
                "t_supply_c": js.get("t_c") if js else None,
                "p_return_bar": jr.get("p_bar") if jr else None,
                "t_return_c": jr.get("t_c") if jr else None,
            }
            for ch in _NODE_CHANNELS:
                entry[ch] = channel(("n", node, ch), values[ch])
            nodes.append(entry)

        # -- plant SCADA (always measured, always live — SPEC §8a) ---------------
        plant = {
            "q_feed_kw": summary.get("q_feed_kw"),
            "t_flow_c": summary.get("t_flow_plant_c"),
            "t_return_c": summary.get("t_return_plant_c"),
            "mdot_kg_per_s": summary.get("mdot_plant_kg_per_s"),
            "pump_el_kw": summary.get("pump_el_kw"),
        }

        measurements = {
            "preset": self.preset,
            "mode": self.mode,
            "consumers": meters,
            "nodes": nodes,
            "plant": plant,
        }

        # Aggregates over metered elements ONLY (SPEC §6 observed_summary) —
        # the operator's arithmetic on the operator's readings, never the
        # ground truth's. In standard mode a cold-start meter contributes
        # nothing yet; with no usable Δp reading the worst point is honestly
        # unknown (None) and the Δp controller stays blind (dp_control.py).
        q_metered = [m["q_kw"] for m in meters if m["q_kw"] is not None]
        dp_metered = [(m["dp_bar"], m["name"]) for m in meters
                      if m["dp_bar"] is not None]
        dp_worst, worst_name = min(dp_metered, default=(None, None))
        observed_summary = {
            "q_feed_kw": plant["q_feed_kw"],
            "q_demand_metered_kw": _r(sum(q_metered)) if q_metered else None,
            "n_metered": len(meters),
            "n_consumers": len(consumers),
            "n_node_sensors": len(nodes),
            "n_nodes": len(node_names),
            "dp_worst_bar": dp_worst,
            "worst_consumer": worst_name,
            "t_flow_plant_c": plant["t_flow_c"],
            "t_return_plant_c": plant["t_return_c"],
            "mdot_plant_kg_per_s": plant["mdot_kg_per_s"],
            "pump_el_kw": plant["pump_el_kw"],
        }
        return measurements, observed_summary
