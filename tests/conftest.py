"""Shared fixtures: the Appendix A five-file bundle + env-isolated settings."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtheatflow.config import Settings
from rtheatflow.data_loader import load_network

REPO_ROOT = Path(__file__).resolve().parents[1]
APPENDIX_A_DIR = REPO_ROOT / "data" / "networks" / "appendix_a"


def make_settings(**overrides) -> Settings:
    """Settings isolated from any local .env / environment drift."""
    return Settings(_env_file=None, **overrides)


@pytest.fixture(scope="session")
def appendix_a_inputs():
    """The SPEC Appendix A 3-consumer loop, loaded through the contract."""
    return load_network(APPENDIX_A_DIR)


@pytest.fixture()
def settings() -> Settings:
    return make_settings()


@pytest.fixture()
def appendix_a_docs() -> dict[str, dict]:
    """Fresh mutable dicts of the five fixture documents (for negative tests)."""
    docs = {}
    for name in ("network_structure", "pipes", "consumers", "producers", "weather"):
        with open(APPENDIX_A_DIR / f"{name}.json", encoding="utf-8") as fh:
            docs[name] = json.load(fh)
    return docs
