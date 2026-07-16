"""Catalog of loadable networks served by the ``/networks`` API (SPEC §4.6, §5).

rtheatflow is a pure *consumer*: it lists networks from the committed
manifest (``data/network_library.json``) and loads a chosen one through the
five-file contract on demand (cached). Blueprint ``grid_catalog.py`` port.

``POST /networks/import`` (user network upload) is deferred to M6 together
with the ``data/user_networks/`` scan — the M4 catalog serves the committed
library; the manifest format already carries a ``source`` field for it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .data_loader import load_network
from .net_inputs import NetInputs

log = logging.getLogger(__name__)


@dataclass
class NetworkEntry:
    id: str
    name: str
    character: str | None = None     # "rural" | "suburban" | "urban"
    nodes: int | None = None
    trench_km: float | None = None
    dir: str | None = None           # five-file directory (manifest-relative)
    source: str = "library"


class NetworkCatalog:
    """Lists loadable networks; converts a chosen one to NetInputs on demand."""

    def __init__(self, manifest: str | Path | None = None,
                 networks_dir: str | Path | None = None):
        self.manifest = Path(manifest) if manifest else None
        self.networks_dir = Path(networks_dir) if networks_dir else None
        self._entries: dict[str, NetworkEntry] = {}
        self._cache: dict[str, NetInputs] = {}
        if self.manifest and self.manifest.is_file():
            self._load_manifest()
        elif self.networks_dir and self.networks_dir.is_dir():
            self._scan_dir()

    def _load_manifest(self) -> None:
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        base = self.manifest.parent
        for n in data.get("networks", []):
            self._entries[n["id"]] = NetworkEntry(
                id=n["id"], name=n.get("name", n["id"]),
                character=n.get("character"), nodes=n.get("nodes"),
                trench_km=n.get("trench_km"),
                dir=str(base / n["dir"]), source=n.get("source", "library"))

    def _scan_dir(self) -> None:
        """Manifest-less fallback: every five-file directory is an entry."""
        for sub in sorted(self.networks_dir.iterdir()):
            if sub.is_dir() and (sub / "network_structure.json").is_file():
                self._entries[sub.name] = NetworkEntry(
                    id=sub.name, name=sub.name, dir=str(sub), source="scan")

    @property
    def available(self) -> bool:
        return bool(self._entries)

    def has(self, network_id: str) -> bool:
        return network_id in self._entries

    def entry(self, network_id: str) -> NetworkEntry:
        return self._entries[network_id]

    def list(self) -> list[dict]:
        return [
            {"id": e.id, "name": e.name, "character": e.character,
             "nodes": e.nodes, "trench_km": e.trench_km, "source": e.source}
            for e in self._entries.values()
        ]

    def get_inputs(self, network_id: str) -> NetInputs:
        """Load (and cache) a network through the five-file contract."""
        if network_id not in self._entries:
            raise KeyError(network_id)
        if network_id not in self._cache:
            self._cache[network_id] = load_network(
                self._entries[network_id].dir)
        return self._cache[network_id]


def preview(entry: NetworkEntry, inputs: NetInputs) -> dict:
    """Net-free preview stats for ``GET /networks/{id}`` (NetzStudio col 3)."""
    trench_km = float(sum(p.length_km for p in inputs.pipes.pipes))
    design_w = float(sum(c.q_design_w for c in inputs.consumers.consumers))
    annual_kwh = float(sum(c.annual_kwh or 0.0
                           for c in inputs.consumers.consumers))
    lhd = (annual_kwh / 1000.0) / (trench_km * 1000.0) if trench_km else None
    slack = next(p for p in inputs.producers.producers if p.kind == "slack")
    return {
        "id": entry.id,
        "name": inputs.name,
        "character": entry.character,
        "n_nodes": len(inputs.structure.junctions),
        "n_trenches": len(inputs.pipes.pipes),
        "n_consumers": len(inputs.consumers.consumers),
        "n_producers": len(inputs.producers.producers),
        "trench_km": round(trench_km, 3),
        "design_load_kw": round(design_w / 1000.0, 1),
        "annual_mwh": round(annual_kwh / 1000.0, 1) if annual_kwh else None,
        "linear_heat_density_mwh_per_m_a": (round(lhd, 3)
                                            if lhd is not None else None),
        "resolution_minutes": inputs.consumers.resolution_minutes,
        "steps": inputs.consumers.steps,
        "n_days": inputs.n_days,
        "plant": {
            "node": slack.node, "name": slack.name,
            "p_flow_bar": slack.p_flow_bar, "plift_bar": slack.plift_bar,
            "t_flow_c": round(float(slack.t_flow_k) - 273.15, 1),
            "heating_curve": (slack.heating_curve.model_dump()
                              if slack.heating_curve else None),
        },
    }
