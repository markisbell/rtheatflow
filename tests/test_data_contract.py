"""Five-file contract validation: schema rules + cross-document checks (SPEC §5)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rtheatflow.data_loader import DataContractError, cross_validate, load_network
from rtheatflow.models import (
    ConsumerSpec,
    ConsumersFile,
    NetworkStructure,
    PipeSpec,
    ProducersFile,
)
from rtheatflow.net_inputs import NetInputs

from conftest import APPENDIX_A_DIR


def _rebuild(docs) -> NetInputs:
    """Validate mutated fixture documents through the full contract path."""
    from rtheatflow.models import PipesFile, WeatherFile

    inputs = NetInputs(
        name=docs["network_structure"]["name"],
        structure=NetworkStructure.model_validate(docs["network_structure"]),
        pipes=PipesFile.model_validate(docs["pipes"]),
        consumers=ConsumersFile.model_validate(docs["consumers"]),
        producers=ProducersFile.model_validate(docs["producers"]),
        weather=WeatherFile.model_validate(docs["weather"]),
    )
    cross_validate(inputs)
    return inputs


def test_fixture_loads_clean():
    inputs = load_network(APPENDIX_A_DIR)
    assert inputs.name == "appendix_a"
    assert inputs.n_days == 1
    assert len(inputs.consumers.consumers) == 3


# --- consumer control-pair rule (SPEC §3.2: qext_w + exactly one partner) ---

@pytest.mark.parametrize("extra", [
    {},                                                        # no partner
    {"treturn_k": [328.15] * 4, "deltat_k": 30.0},             # two partners
    {"deltat_k": 30.0, "controlled_mdot_kg_per_s": 0.25},      # two partners
])
def test_consumer_partner_rule(extra):
    base = dict(node="n1", q_sh_w=[1000.0] * 4, q_dhw_w=[0.0] * 4,
                q_design_w=1000.0)
    with pytest.raises(ValidationError, match="exactly one of"):
        ConsumerSpec.model_validate({**base, **extra})


def test_consumer_treturn_kelvin_guard():
    # a tutorial passes treturn_k=50 (= -223 degC); the contract rejects it
    with pytest.raises(ValidationError, match="Kelvin"):
        ConsumerSpec.model_validate(dict(
            node="n1", q_sh_w=[1000.0] * 4, q_dhw_w=[0.0] * 4,
            treturn_k=[50.0] * 4, q_design_w=1000.0))


# --- pipe rule: std_type XOR explicit parameters (SPEC §3.1) ---

def test_pipe_std_type_excludes_overrides():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        PipeSpec.model_validate(dict(
            from_node="a", to_node="b", length_km=0.1,
            std_type="ISOPLUS_DRE100_STD", u_w_per_m2k=1.0))


def test_pipe_needs_type_or_parameters():
    with pytest.raises(ValidationError, match="std_type or"):
        PipeSpec.model_validate(dict(from_node="a", to_node="b", length_km=0.1))


# --- exactly one slack (SPEC §3.1 single-pressure-slack rule) ---

def test_second_slack_rejected(appendix_a_docs):
    docs = appendix_a_docs
    docs["producers"]["producers"].append({
        "node": "n3", "kind": "slack",
        "p_flow_bar": 6.0, "plift_bar": 2.0, "t_flow_k": 358.15})
    with pytest.raises(ValidationError, match="exactly one slack"):
        _rebuild(docs)


def test_no_slack_rejected(appendix_a_docs):
    docs = appendix_a_docs
    docs["producers"]["producers"] = []
    with pytest.raises(ValidationError):
        _rebuild(docs)


# --- cross-document checks ---

def test_unknown_node_rejected(appendix_a_docs):
    docs = appendix_a_docs
    docs["consumers"]["consumers"][0]["node"] = "nope"
    with pytest.raises(DataContractError, match="unknown node"):
        _rebuild(docs)


def test_array_length_mismatch_rejected(appendix_a_docs):
    docs = appendix_a_docs
    docs["consumers"]["consumers"][0]["q_sh_w"] = [80000.0] * 10
    with pytest.raises(DataContractError, match="length"):
        _rebuild(docs)


def test_weather_horizon_mismatch_rejected(appendix_a_docs):
    docs = appendix_a_docs
    docs["weather"]["steps"] = 48
    docs["weather"]["t_amb_c"] = [0.0] * 48
    docs["weather"]["t_ground_c"] = [10.0] * 48
    with pytest.raises(DataContractError, match="horizon"):
        _rebuild(docs)


def test_unreachable_consumer_rejected(appendix_a_docs):
    docs = appendix_a_docs
    # island: n4/n5 connected to each other but not to the slack's component
    docs["network_structure"]["junctions"] += [
        {"name": "n4", "kind": "consumer", "geo": [48.1, 7.9], "pn_bar": 6},
        {"name": "n5", "kind": "node", "geo": [48.1, 7.91], "pn_bar": 6},
    ]
    docs["pipes"]["pipes"].append({
        "from_node": "n4", "to_node": "n5",
        "std_type": "ISOPLUS_DRE50_STD", "length_km": 0.1})
    steps = docs["consumers"]["steps"]
    docs["consumers"]["consumers"].append({
        "node": "n4", "name": "island consumer",
        "q_sh_w": [1000.0] * steps, "q_dhw_w": [0.0] * steps,
        "treturn_k": [328.15] * steps, "q_design_w": 1000.0})
    with pytest.raises(DataContractError, match="not reachable"):
        _rebuild(docs)


def test_dead_end_without_consumer_rejected(appendix_a_docs):
    """Zero-flow guard (SPEC §3.2): a stub trench with nothing attached is
    hydraulically singular and must be rejected at load time."""
    docs = appendix_a_docs
    docs["network_structure"]["junctions"].append(
        {"name": "n4", "kind": "node", "geo": [48.0, 7.816], "pn_bar": 6})
    docs["pipes"]["pipes"].append({
        "from_node": "n3", "to_node": "n4",
        "std_type": "ISOPLUS_DRE50_STD", "length_km": 0.05})
    with pytest.raises(DataContractError, match="dead-end"):
        _rebuild(docs)


def test_isolated_node_rejected(appendix_a_docs):
    docs = appendix_a_docs
    docs["network_structure"]["junctions"].append(
        {"name": "lonely", "kind": "node", "geo": [48.2, 7.9], "pn_bar": 6})
    with pytest.raises(DataContractError, match="isolated"):
        _rebuild(docs)
