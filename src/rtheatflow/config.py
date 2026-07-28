"""Configuration — pydantic-settings singleton, env prefix ``RTHEATFLOW_`` (SPEC §6).

All fields are documented in ``.env.example``. Mirrors the blueprint's
``config.py``: one Settings class, ``.env`` support, ``extra="ignore"``,
module-level singleton accessor.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RTHEATFLOW_", env_file=".env", extra="ignore"
    )

    # --- paths ---
    data_dir: Path = Path("./data")
    # network loaded at startup (``<data_dir>/networks/<id>``) until the M4
    # catalog/config endpoints ship; tests load their fixture explicitly
    default_network: str = "demo_dorf"
    network_library: Path = Path("./data/network_library.json")
    user_networks_dir: Path = Path("./data/user_networks")
    profiles_dir: Path = Path("./data/profiles")
    scenarios_dir: Path = Path("./data/scenarios")
    recordings_dir: Path = Path("./data/recordings")

    # --- engine ---
    step_interval_seconds: float = 1.0  # wall-clock seconds per simulated step
    steps_per_day: int = 1440           # one-minute steps
    autostart: bool = True
    history_size: int = 1440
    # Puppet mode (gamebridge): an external application owns the clock. The
    # internal tick loop never starts (autostart is ignored); steps advance
    # ONLY via POST /gb/step. See api/gamebridge.py.
    external_clock: bool = False

    # --- solver (SPEC §3.3) ---
    solver_iter: int = 100   # base iter for retry-ladder tiers 1/2/4; tier 3 uses 2x
    transient: bool = False  # experimental, offline exporter only (SPEC §3.5)

    # --- domain defaults ---
    pump_eta: float = 0.7           # P_el = P_hyd / eta
    dp_min_bar: float = 0.5         # minimum differential pressure at the critical consumer
    return_temp_margin_k: float = 5.0
    # zero-flow guard (SPEC §3.2): every consumer's qext_w is floored at this
    # value — zero-flow branches are singular in the heat-transfer matrix, and
    # the near-zero-load regime is where qext+treturn control diverges.
    min_qext_w: float = 500.0

    # --- observability ---
    expose_ground_truth: bool = True  # false = strict mode (ground truth stripped)

    # --- server (used from M2 onward) ---
    host: str = "127.0.0.1"
    # sibling port scheme: 8001 (UI dev 5174) so rtheatflow can run next to
    # netzsim/rtpowerflow (which owns 8000/5173) on the same machine
    port: int = 8001
    log_level: str = "info"
    cors_origins: str = "*"
    record: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor (blueprint convention)."""
    return Settings()
