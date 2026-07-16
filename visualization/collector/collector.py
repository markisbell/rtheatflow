"""Collector: poll rtheatflow's REST API and write each solved step into InfluxDB.

Reads ``GET /state`` on the realtime district-heating service, deduplicates
by ``(day, step)`` so every simulated step is written exactly once, and
stores summary / junctions / pipes / consumers / producers / storages as
InfluxDB measurements. The point timestamp is the wall-clock time the step
was solved, so a Grafana "last 5 minutes" view follows the accelerated
realtime simulation live (SPEC §9.3).
"""
from __future__ import annotations

import logging
import os
import time

import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

log = logging.getLogger("collector")

RTHEATFLOW_URL = os.getenv("RTHEATFLOW_URL", "http://backend:8000").rstrip("/")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "rtheatflow-dev-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "rtheatflow")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "heatflow")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "0.5"))

#: solver verdict as a plottable field: 2 ok · 1 degraded · 0 failed
_SOLVER_STATE = {"ok": 2, "degraded": 1, "failed": 0}


def wait_for(url: str, label: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=3).status_code < 500:
                log.info("%s is up.", label)
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {label} at {url}")


def _fields(point: Point, row: dict, names: tuple[str, ...]) -> Point:
    for f in names:
        v = row.get(f)
        if v is not None:
            point = point.field(f, float(v))
    return point


def build_points(state: dict) -> list[Point]:
    ts = int(float(state["timestamp"]) * 1e9)  # unix seconds -> ns
    day = int(state["day"])
    step = int(state["step"])
    tod = state["time_of_day"]
    pts: list[Point] = []

    def base(measurement: str) -> Point:
        return (
            Point(measurement)
            .field("day", day)
            .field("step", step)
            .tag("time_of_day", tod)
            .time(ts, WritePrecision.NS)
        )

    # -- summary: KPIs + solver verdict + the operator's Δp/pump settings ---
    summary = base("summary").field("converged", int(bool(state["converged"])))
    summary = summary.field("solve_ms", float(state.get("solve_ms") or 0.0))
    summary = summary.field(
        "solver_state", _SOLVER_STATE.get(state.get("solver_status"), 0))
    for k, v in (state.get("summary") or {}).items():
        if v is not None and not isinstance(v, str):
            summary = summary.field(k, float(v))
    worst = (state.get("summary") or {}).get("worst_consumer")
    if worst:
        summary = summary.tag("worst_consumer", str(worst))
    dpc = (state.get("controls") or {}).get("dp_control") or {}
    for src, dst in (("setpoint_bar", "dp_setpoint_bar"),
                     ("plift_bar", "dp_plift_bar"),
                     ("dp_worst_observed_bar", "dp_worst_observed_bar")):
        if dpc.get(src) is not None:
            summary = summary.field(dst, float(dpc[src]))
    weather = state.get("weather") or {}
    for k in ("t_amb_c", "t_ground_c"):
        if weather.get(k) is not None:
            summary = summary.field(k, float(weather[k]))
    pts.append(summary)

    for j in state.get("junctions", []):
        p = (base("junction").tag("junction", str(j["id"]))
             .tag("name", j["name"]).tag("side", j["side"]))
        pts.append(_fields(p, j, ("p_bar", "t_c")))

    for pipe in state.get("pipes", []):
        p = (base("pipe").tag("pipe", str(pipe["id"]))
             .tag("trench", str(pipe["trench"])).tag("side", pipe["side"]))
        pts.append(_fields(p, pipe, ("mdot_kg_per_s", "v_m_per_s", "t_from_c",
                                     "t_to_c", "q_loss_kw", "dp_bar")))

    for c in state.get("consumers", []):
        p = base("consumer").tag("consumer", str(c["id"])).tag("name", c["name"])
        pts.append(_fields(p, c, ("q_kw", "mdot_kg_per_s", "t_supply_c",
                                  "t_return_c", "dp_bar")))

    for pr in state.get("producers", []):
        p = (base("producer").tag("producer", str(pr["id"]))
             .tag("kind", pr["kind"]).tag("name", pr["name"]))
        pts.append(_fields(p, pr, ("q_kw", "t_flow_c", "plift_bar",
                                   "pump_el_kw", "cop", "p_el_kw",
                                   "p_fuel_kw", "mdot_kg_per_s")))

    for s in state.get("storages", []):
        p = base("storage").tag("storage", str(s["id"])).tag("name", s["name"])
        pts.append(_fields(p, s, ("soc_kwh", "capacity_kwh", "q_kw")))

    return pts


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    wait_for(f"{INFLUX_URL}/health", "InfluxDB")
    wait_for(f"{RTHEATFLOW_URL}/health", "rtheatflow")

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    log.info("Collecting %s/state -> InfluxDB bucket '%s' every %.2fs",
             RTHEATFLOW_URL, INFLUX_BUCKET, POLL_INTERVAL)

    last_key: tuple[int, int] | None = None
    while True:
        try:
            resp = requests.get(f"{RTHEATFLOW_URL}/state", timeout=5)
            if resp.status_code == 404:
                time.sleep(POLL_INTERVAL)  # no step solved yet
                continue
            resp.raise_for_status()
            state = resp.json()
            key = (int(state["day"]), int(state["step"]))
            if key != last_key:            # dedupe on (day, step) — SPEC §9.3
                write_api.write(bucket=INFLUX_BUCKET, record=build_points(state))
                last_key = key
                log.debug("wrote day=%s step=%s (%s)", key[0], key[1],
                          state["time_of_day"])
        except Exception as exc:  # noqa: BLE001 — keep the collector resilient
            log.warning("poll/write failed: %s", exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
