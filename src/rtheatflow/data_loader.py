"""Load and cross-validate the five-file data contract (SPEC §5).

Per-document schema validation lives in :mod:`rtheatflow.models`; this module
adds the **cross-document** checks:

* node references valid (pipes, consumers, producers),
* profile array lengths equal the declared ``steps``,
* consumer/weather horizons cover the same whole number of days,
* exactly one slack (already enforced per-file, re-checked here),
* every consumer node reachable from the slack node over the trench graph,
* zero-flow guard (SPEC §3.2): no dead-end trench node without a consumer or
  producer — a live branch with nothing attached is hydraulically singular.

All violations raise :class:`DataContractError` with every finding listed.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from .models import (
    ConsumersFile,
    NetworkStructure,
    PipesFile,
    ProducersFile,
    WeatherFile,
)
from .net_inputs import NetInputs

log = logging.getLogger(__name__)

FILE_NAMES = {
    "structure": "network_structure.json",
    "pipes": "pipes.json",
    "consumers": "consumers.json",
    "producers": "producers.json",
    "weather": "weather.json",
}


class DataContractError(ValueError):
    """A five-file bundle violated the data contract."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("data contract violated:\n- " + "\n- ".join(errors))


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_network(directory: str | Path) -> NetInputs:
    """Load the five files from *directory*, validate, cross-validate."""
    d = Path(directory)
    missing = [n for n in FILE_NAMES.values() if not (d / n).is_file()]
    if missing:
        raise DataContractError([f"missing file(s) in {d}: {missing}"])

    structure = NetworkStructure.model_validate(_read_json(d / FILE_NAMES["structure"]))
    pipes = PipesFile.model_validate(_read_json(d / FILE_NAMES["pipes"]))
    consumers = ConsumersFile.model_validate(_read_json(d / FILE_NAMES["consumers"]))
    producers = ProducersFile.model_validate(_read_json(d / FILE_NAMES["producers"]))
    weather = WeatherFile.model_validate(_read_json(d / FILE_NAMES["weather"]))

    inputs = NetInputs(
        name=structure.name,
        structure=structure,
        pipes=pipes,
        consumers=consumers,
        producers=producers,
        weather=weather,
    )
    cross_validate(inputs)
    return inputs


def cross_validate(inputs: NetInputs) -> None:
    """Raise :class:`DataContractError` on any cross-document violation."""
    errors: list[str] = []
    nodes = {j.name for j in inputs.structure.junctions}

    # --- node references ---
    for p in inputs.pipes.pipes:
        for ref in (p.from_node, p.to_node):
            if ref not in nodes:
                errors.append(f"pipe {p.from_node}->{p.to_node}: unknown node {ref!r}")
    for c in inputs.consumers.consumers:
        if c.node not in nodes:
            errors.append(f"consumer {c.name or c.node!r}: unknown node {c.node!r}")
    for pr in inputs.producers.producers:
        if pr.node not in nodes:
            errors.append(f"producer {pr.name or pr.kind}: unknown node {pr.node!r}")

    # --- array lengths ---
    steps = inputs.consumers.steps
    for c in inputs.consumers.consumers:
        label = c.name or c.node
        for field in ("q_sh_w", "q_dhw_w"):
            if len(getattr(c, field)) != steps:
                errors.append(
                    f"consumer {label!r}: {field} length {len(getattr(c, field))} != steps {steps}"
                )
        if c.treturn_k is not None and len(c.treturn_k) != steps:
            errors.append(
                f"consumer {label!r}: treturn_k length {len(c.treturn_k)} != steps {steps}"
            )
    for pr in inputs.producers.producers:
        if pr.qext_w is not None and len(pr.qext_w) != steps:
            errors.append(
                f"producer at {pr.node!r}: qext_w length {len(pr.qext_w)} != consumer steps {steps}"
            )
        if isinstance(pr.mdot_flow_kg_per_s, list) and len(pr.mdot_flow_kg_per_s) != steps:
            errors.append(
                f"producer at {pr.node!r}: mdot_flow_kg_per_s length "
                f"{len(pr.mdot_flow_kg_per_s)} != consumer steps {steps}"
            )

    # --- horizons: same total duration, whole days ---
    total_c = inputs.consumers.steps * inputs.consumers.resolution_minutes
    total_w = inputs.weather.steps * inputs.weather.resolution_minutes
    if total_c != total_w:
        errors.append(
            f"consumer horizon ({total_c} min) != weather horizon ({total_w} min)"
        )
    if total_c % (24 * 60) != 0:
        errors.append(f"profile horizon {total_c} min is not a whole number of days")

    # --- exactly one slack (belt and braces; ProducersFile enforces too) ---
    slacks = [p for p in inputs.producers.producers if p.kind == "slack"]
    if len(slacks) != 1:
        errors.append(f"exactly one slack producer required, got {len(slacks)}")

    # --- reachability + zero-flow guard over the trench graph ---
    adjacency: dict[str, set[str]] = defaultdict(set)
    degree: dict[str, int] = defaultdict(int)
    for p in inputs.pipes.pipes:
        adjacency[p.from_node].add(p.to_node)
        adjacency[p.to_node].add(p.from_node)
        degree[p.from_node] += 1
        degree[p.to_node] += 1

    if slacks:
        reachable = _bfs(adjacency, slacks[0].node)
        for c in inputs.consumers.consumers:
            if c.node not in reachable:
                errors.append(
                    f"consumer {c.name or c.node!r} at {c.node!r} not reachable "
                    f"from the slack at {slacks[0].node!r}"
                )
        for pr in inputs.producers.producers:
            if pr.node not in reachable:
                errors.append(
                    f"producer at {pr.node!r} not reachable from the slack"
                )

    # zero-flow guard (SPEC §3.2): dead-end nodes must host a consumer or
    # producer (a bypass is a consumer with the mdot+qext pair); isolated
    # nodes (no pipe at all) are rejected outright.
    occupied = {c.node for c in inputs.consumers.consumers} | {
        p.node for p in inputs.producers.producers
    }
    for j in inputs.structure.junctions:
        deg = degree.get(j.name, 0)
        if deg == 0:
            errors.append(f"node {j.name!r} has no pipes attached (isolated)")
        elif deg == 1 and j.name not in occupied:
            errors.append(
                f"dead-end node {j.name!r} has no consumer/producer/bypass — "
                "zero-flow branches are singular (SPEC §3.2); add a bypass "
                "consumer (controlled_mdot_kg_per_s + qext_w) or remove the stub"
            )

    if errors:
        raise DataContractError(errors)


def _bfs(adjacency: dict[str, set[str]], start: str) -> set[str]:
    seen = {start}
    frontier = [start]
    while frontier:
        nxt: list[str] = []
        for node in frontier:
            for nb in adjacency.get(node, ()):
                if nb not in seen:
                    seen.add(nb)
                    nxt.append(nb)
        frontier = nxt
    return seen
