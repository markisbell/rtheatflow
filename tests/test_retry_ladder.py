"""Retry ladder (SPEC §3.3) — semantics + the validated hard case.

``schutterwald_heat(70, treturn_degC=45)``: tier 1 (undamped) fails, tier 2
(``alpha=0.5``) converges (SPEC Appendix B item 2, validated 2026-07-15).
"""
from __future__ import annotations

import pytest
from pandapipes.networks import schutterwald_heat

from rtheatflow.simulator import SolveOutcome, retry_attempts, solve_with_retry


def test_ladder_tiers_and_solver_iter_semantics():
    """RTHEATFLOW_SOLVER_ITER sets base iter of tiers 1/2/4; tier 3 uses 2x."""
    tiers = retry_attempts(50)
    assert [t["mode"] for t in tiers] == [
        "bidirectional", "bidirectional", "bidirectional", "sequential"]
    assert [t["iter"] for t in tiers] == [50, 50, 100, 50]
    assert "alpha" not in tiers[0]
    assert tiers[1]["alpha"] == 0.5
    assert tiers[2]["alpha"] == 0.2
    # never nonlinear_method="automatic" in bidirectional (ValueError in 0.14)
    assert all("nonlinear_method" not in t for t in tiers)


def test_hard_case_tier2_converges():
    net = schutterwald_heat(tflow_degC=70, treturn_degC=45)
    outcome = solve_with_retry(net, iter_base=100)
    assert isinstance(outcome, SolveOutcome)
    assert outcome.converged is True
    assert outcome.status == "ok"          # bidirectional tier, not degraded
    assert outcome.tier == 2               # alpha=0.5 catches it
    assert net.converged


def test_easy_case_tier1():
    net = schutterwald_heat()
    outcome = solve_with_retry(net, iter_base=100)
    assert outcome.converged and outcome.tier == 1 and outcome.status == "ok"
