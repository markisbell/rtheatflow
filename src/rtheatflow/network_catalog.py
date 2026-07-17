"""Catalog of loadable networks served by the ``/networks`` API (SPEC §4.6, §5).

rtheatflow is a pure *consumer*: it lists networks from the committed
manifest (``data/network_library.json``) and loads a chosen one through the
five-file contract on demand (cached against an on-disk fingerprint — a
bundle regenerated on disk is re-read on the next access, never served
stale). Blueprint ``grid_catalog.py`` port.

Since M6 the catalog also scans ``data/user_networks/`` (``POST
/networks/import`` writes validated five-file bundles there): every
subdirectory with a ``network_structure.json`` becomes an entry with id
``user_<dirname>`` and ``source="user"``. The scan is cheap (two small JSON
reads per entry for the list stats) and re-runs lazily when an unknown
``user_*`` id is looked up, so imports and hand-copied bundles appear
without a restart.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .data_loader import FILE_NAMES, load_network
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
                 networks_dir: str | Path | None = None,
                 user_dir: str | Path | None = None):
        self.manifest = Path(manifest) if manifest else None
        self.networks_dir = Path(networks_dir) if networks_dir else None
        self.user_dir = Path(user_dir) if user_dir else None
        self._entries: dict[str, NetworkEntry] = {}
        self._cache: dict[str, NetInputs] = {}
        self._cache_state: dict[str, tuple] = {}   # id -> _disk_state at load
        if self.manifest and self.manifest.is_file():
            self._load_manifest()
        elif self.networks_dir and self.networks_dir.is_dir():
            self._scan_dir()
        self.rescan_user()

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

    def rescan_user(self) -> None:
        """(Re)scan ``user_networks/`` — imported bundles become ``user_*``
        entries; entries whose directory vanished are dropped (M6)."""
        stale = [nid for nid, e in self._entries.items()
                 if e.source == "user" and not (
                     e.dir and (Path(e.dir) / "network_structure.json").is_file())]
        for nid in stale:
            self._entries.pop(nid, None)
            self._cache.pop(nid, None)
            self._cache_state.pop(nid, None)
        if self.user_dir is None or not self.user_dir.is_dir():
            return
        for sub in sorted(self.user_dir.iterdir()):
            if not sub.is_dir() or not (sub / "network_structure.json").is_file():
                continue
            nid = f"user_{sub.name}"
            name, nodes, trench_km = sub.name, None, None
            try:  # cheap list stats — full validation happens on get_inputs
                struct = json.loads(
                    (sub / "network_structure.json").read_text(encoding="utf-8"))
                name = struct.get("name") or sub.name
                nodes = len(struct.get("junctions") or []) or None
                pipes = json.loads(
                    (sub / "pipes.json").read_text(encoding="utf-8"))
                trench_km = round(sum(
                    float(p.get("length_km") or 0.0)
                    for p in pipes.get("pipes") or []), 3) or None
            except Exception:  # noqa: BLE001 — stats stay unknown, entry listed
                log.debug("user network %s: could not read list stats", nid)
            self._entries[nid] = NetworkEntry(
                id=nid, name=name, nodes=nodes, trench_km=trench_km,
                dir=str(sub), source="user")

    @property
    def available(self) -> bool:
        return bool(self._entries)

    def has(self, network_id: str) -> bool:
        if network_id not in self._entries and network_id.startswith("user_"):
            self.rescan_user()      # imports appear without a restart
        return network_id in self._entries

    def entry(self, network_id: str) -> NetworkEntry:
        return self._entries[network_id]

    def list(self) -> list[dict]:
        return [
            {"id": e.id, "name": e.name, "character": e.character,
             "nodes": e.nodes, "trench_km": e.trench_km, "source": e.source}
            for e in self._entries.values()
        ]

    @staticmethod
    def _disk_state(directory: str | None) -> tuple | None:
        """Fingerprint of the five contract files (mtime_ns + size each).

        ``get_inputs`` compares it against the state captured at load time
        and drops its cache on any difference, so a bundle regenerated on
        disk is picked up without a process restart (2026-07-17 bug: after
        regenerating ``data/networks/verbier/``, ``POST /config/apply``
        kept serving the old geometry from this cache)."""
        if not directory:
            return None
        d = Path(directory)
        state = []
        for fname in FILE_NAMES.values():
            try:
                st = (d / fname).stat()
                state.append((fname, st.st_mtime_ns, st.st_size))
            except OSError:      # missing file — load_network reports why
                state.append((fname, None, None))
        return tuple(state)

    def get_inputs(self, network_id: str, *,
                   refresh: bool = False) -> NetInputs:
        """Load (and cache) a network through the five-file contract.

        The cache only serves entries whose five files are unchanged on
        disk (:meth:`_disk_state`); *refresh* forces a re-read regardless —
        the ``/config/apply`` path passes it so a swap ALWAYS reflects the
        on-disk state (apply is rare and heavyweight: ``engine.reconfigure``
        rebuilds the whole Simulator, re-parsing JSON is negligible)."""
        if network_id not in self._entries:
            raise KeyError(network_id)
        entry = self._entries[network_id]
        state = self._disk_state(entry.dir)
        if refresh or self._cache_state.get(network_id, state) != state:
            self._cache.pop(network_id, None)
        if network_id not in self._cache:
            # state captured BEFORE the load: a file rewritten mid-load
            # yields a mismatch on the next access → conservative re-read
            self._cache[network_id] = load_network(entry.dir)
            self._cache_state[network_id] = state
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
