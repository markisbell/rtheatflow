# visualization/ — InfluxDB + Grafana stack (SPEC §9.3)

Second consumer of the realtime API (next to the interactive UI): a
`collector` polls `GET /state` every `POLL_INTERVAL_SECONDS` (default 0.5 s),
deduplicates on `(day, step)` so every simulated step lands exactly once, and
writes summary / junction / pipe / consumer / producer / storage points into
InfluxDB 2.7 with **wall-clock timestamps** — a Grafana "last 5 minutes" view
follows the accelerated simulation live.

Grafana 11 is fully file-provisioned (no clicking): the datasource
(`provisioning/datasources/influxdb.yml`) and the dashboard
(`dashboards/rtheatflow.json`) ship in-repo. Panels: plant supply/return vs
heating-curve setpoint, worst-point Δp vs setpoint + pump lift, loss ratio,
producer dispatch stack, storage SoC, solve time, solver status
(OK/DEGRADED/FAILED mapping).

Run it via the repo-root `docker-compose.yml`:

```bash
docker compose up --build
# Grafana:  http://localhost:3000  (admin/admin — dev credentials)
# InfluxDB: http://localhost:8086  (admin/rtheatflow-admin)
```

⚠️ All credentials/tokens here are development defaults, wired identically
into `docker-compose.yml`, the datasource provisioning and the collector env.
Change them together if the stack ever leaves a trusted machine.
