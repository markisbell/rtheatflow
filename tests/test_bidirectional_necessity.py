"""Bidirectional-necessity regression (SPEC §3.3, §11).

Temperature-controlled consumers make mass flow depend on the arriving
temperature, so ``mode="sequential"`` silently misses set points: on the
Appendix A fixture consumer B's deltat comes back ~30.08 K (setpoint 30.00)
and consumer A's mass flow is ~6 % off. This is the reason ``bidirectional``
is the platform default — pin it.
"""
from __future__ import annotations

import pandapipes as pp
import pytest

from rtheatflow.network_builder import build_network


def test_sequential_violates_setpoints(appendix_a_inputs):
    net, _ = build_network(appendix_a_inputs)
    pp.pipeflow(net, mode="sequential", iter=100)
    hc = net.res_heat_consumer
    deltat_b = hc.deltat_k.iloc[1]
    # violated: not the 30.00 K setpoint ...
    assert abs(deltat_b - 30.0) > 0.05
    # ... and pinned at the known magnitude ~30.08 K
    assert deltat_b == pytest.approx(30.08, abs=0.02)
    # consumer A mass flow off by ~6 % (0.6361 vs 0.6744 kg/s)
    assert hc.mdot_from_kg_per_s.iloc[0] == pytest.approx(0.6361, rel=0.005)


def test_bidirectional_honors_setpoints(appendix_a_inputs):
    net, _ = build_network(appendix_a_inputs)
    pp.pipeflow(net, mode="bidirectional", iter=100)
    assert net.res_heat_consumer.deltat_k.iloc[1] == pytest.approx(30.0, abs=1e-3)
