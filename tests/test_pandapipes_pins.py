"""Regression pins on pandapipes 0.14.0 behavior the platform depends on
(SPEC §11; migrated from scripts/validate_core.py)."""
from __future__ import annotations

import numpy as np
import pandapipes as pp
import pytest
from pandapipes.networks import schutterwald_heat

from rtheatflow.network_builder import build_network


# --- heat_consumer pair validation (SPEC §3.2) ------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(qext_w=1000.0),                                 # only one setpoint
    dict(qext_w=1000.0, deltat_k=20.0, treturn_k=330.0), # three setpoints
    dict(deltat_k=20.0, treturn_k=330.0),                # forbidden combination
])
def test_heat_consumer_rejects_invalid_pairs(kwargs):
    net = pp.create_empty_network(fluid="water")
    a = pp.create_junction(net, pn_bar=5, tfluid_k=358.15)
    b = pp.create_junction(net, pn_bar=5, tfluid_k=358.15)
    with pytest.raises(Exception):
        pp.create_heat_consumer(net, a, b, **kwargs)


# --- text_k must be explicit (SPEC Appendix B item 4) ------------------------

def test_builder_sets_text_k_explicitly(appendix_a_inputs):
    """The signature default 0 means 0 K ambient (~4x fake losses); the
    builder must store the ground temperature on every pipe."""
    net, _ = build_network(appendix_a_inputs)
    assert np.allclose(net.pipe.text_k.to_numpy(), 283.15)


# --- nonlinear_method="automatic" is unusable in bidirectional (App. B 2) ----

def test_automatic_nonlinear_method_raises_in_bidirectional():
    """The 0.14.0 damping-adaptation branch is broken for bidirectional
    (source TODO in pipeflow.finalize_iteration): once errors increase
    mid-iteration it hits a shape-mismatch ValueError. Only hard cases
    trigger the branch — pin it on the validated one. This is why the
    retry ladder must never use nonlinear_method="automatic"."""
    net = schutterwald_heat(tflow_degC=70, treturn_degC=45)
    with pytest.raises(ValueError, match="broadcast"):
        pp.pipeflow(net, mode="bidirectional", iter=200, alpha=0.2,
                    nonlinear_method="automatic")


# --- ISOPLUS std types carry per-length U-values (SPEC §3.1) -----------------

def test_isoplus_std_types_available(appendix_a_inputs):
    net, _ = build_network(appendix_a_inputs)
    assert set(net.pipe.std_type.dropna()) == {
        "ISOPLUS_DRE100_STD", "ISOPLUS_DRE80_STD", "ISOPLUS_DRE50_STD"}
    # u values were materialized onto the pipe table (auto-converted per-area)
    assert (net.pipe.u_w_per_m2k > 0).all()
