"""§12 M4 acceptance 1: the 3G/4G preset switch shows the story.

Switching the heating curve from 3G (110/70) to 4G (70/65) on a RUNNING net
must (a) drop the distribution losses — they scale with (T_net − T_ground) —
and (b) raise the mass flow — the consumers' fixed return temperatures eat
the ΔT (low-ΔT syndrome direction). Both directions asserted (SPEC §1, §11).
"""
from __future__ import annotations

from conftest import make_api_client, wait_for


def _steady_frame(client, predicate):
    """Wait for a converged frame satisfying *predicate*, then one more
    converged frame (controller writes settle within a tick)."""
    frame = wait_for(lambda: (
        (f := client.get("/state").json()).get("converged")
        and predicate(f) and f))
    return frame


def test_3g_to_4g_switch_drops_losses_and_raises_mass_flow():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)

        # 3G: 110/70 — at the fixture's t_amb 0 °C the curve gives ~95 °C
        client.post("/heatingcurve", json={"preset": "3G"})
        f3 = _steady_frame(
            client, lambda f: f["summary"]["t_flow_plant_c"] > 90)
        loss_3g = f3["summary"]["q_loss_kw"]
        loss_pct_3g = f3["summary"]["loss_pct"]
        mdot_3g = f3["summary"]["mdot_plant_kg_per_s"]

        # 4G: 70/65 — the same weather gives ~68 °C flow
        client.post("/heatingcurve", json={"preset": "4G"})
        f4 = _steady_frame(
            client, lambda f: f["summary"]["t_flow_plant_c"] < 70.5)
        loss_4g = f4["summary"]["q_loss_kw"]
        loss_pct_4g = f4["summary"]["loss_pct"]
        mdot_4g = f4["summary"]["mdot_plant_kg_per_s"]

        # (a) losses drop — clearly, not marginally
        assert loss_4g < loss_3g * 0.75, (loss_3g, loss_4g)
        assert loss_pct_4g < loss_pct_3g
        # (b) mass flow rises — the low-ΔT-syndrome direction
        assert mdot_4g > mdot_3g * 1.3, (mdot_3g, mdot_4g)
        # both operating points balance-consistent
        for f in (f3, f4):
            assert abs(f["summary"]["balance_err_kw"]) <= \
                0.01 * f["summary"]["q_feed_kw"]
