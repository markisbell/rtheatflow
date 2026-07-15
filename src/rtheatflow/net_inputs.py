"""``NetInputs`` — the single importer contract (blueprint ``grid_inputs.py`` analog).

Every importer (five-file directory loader, catalog, future OSM import)
converges on this one validated bundle; :func:`rtheatflow.network_builder.build_network`
consumes nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import (
    ConsumersFile,
    NetworkStructure,
    PipesFile,
    ProducersFile,
    WeatherFile,
)


@dataclass(frozen=True)
class NetInputs:
    """Validated, cross-checked content of the five input files (SPEC §5)."""

    name: str
    structure: NetworkStructure
    pipes: PipesFile
    consumers: ConsumersFile
    producers: ProducersFile
    weather: WeatherFile

    @property
    def total_minutes(self) -> int:
        return self.consumers.steps * self.consumers.resolution_minutes

    @property
    def n_days(self) -> int:
        return self.total_minutes // (24 * 60)
