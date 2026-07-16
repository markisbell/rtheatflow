/**
 * The Übersicht side-panel section (blueprint OverviewSection port): per view
 * mode it aggregates a different data layer — the revealed ground truth or
 * only what the placed heat meters deliver (SPEC §8 KPI set).
 */
import { useTranslation } from "react-i18next";
import Section, { Stat } from "./Section";
import { dpColor, fmt } from "../scales";
import type {
  Controls,
  HeatingCurveParams,
  ObservedSummary,
  StepSummary,
  WeatherState,
} from "../types";

/** Client-side heating-curve evaluation (SPEC §4.2) — shows the setpoint the
 *  plant is steering toward next to the actual flow temperature. */
export function curveSetpoint(
  c: HeatingCurveParams | null | undefined,
  tAmb: number | null | undefined,
): number | null {
  if (!c || tAmb == null) return null;
  const f = (c.t_room_c - tAmb) / (c.t_room_c - c.t_amb_design_c);
  if (f <= 0) return c.t_flow_min_c;
  const t = c.t_flow_min_c
    + (c.t_flow_design_c - c.t_flow_min_c) * Math.pow(f, 1 / c.n);
  return Math.min(c.t_flow_design_c, Math.max(c.t_flow_min_c, t));
}

export default function OverviewSection({
  open, onToggle, mode, summary, observed, weather, controls,
  solverStatus, solveMs, canReveal,
}: {
  open: boolean;
  onToggle: () => void;
  mode: "truth" | "observed";
  summary: StepSummary | undefined;
  observed: ObservedSummary | null | undefined;
  weather: WeatherState | undefined;
  controls: Controls | undefined;
  solverStatus: string | undefined;
  solveMs: number | null;
  canReveal: boolean;
}) {
  const { t } = useTranslation();
  const reveal = mode === "truth" && !!summary;
  const s = summary;
  const os = observed;
  const setpoint = curveSetpoint(controls?.heating_curve, weather?.t_amb_c);

  const solverBadge = solverStatus ? (
    <span className={`badge ${solverStatus}`}>
      {solverStatus === "ok" ? t("ov.solverOk")
        : solverStatus === "degraded" ? t("ov.solverDegraded")
        : t("ov.solverFailed")}
    </span>
  ) : null;

  const tempPair = (flow: number | null | undefined, ret: number | null | undefined) =>
    `${fmt(flow, 1)} / ${fmt(ret, 1)} °C`;

  return (
    <Section title={t("ov.heading")} open={open} onToggle={onToggle}>
      <div className="muted" style={{ fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 2 }}>
        {reveal ? <>👁 {t("ov.groundTruth")}</> : <>📟 {t("ov.observedCaption")}</>}
      </div>
      {reveal && s ? (
        <>
          <Stat label={t("ov.feedIn")} value={`${fmt(s.q_feed_kw, 1)} kW`} />
          <Stat label={t("ov.demand")} value={`${fmt(s.q_demand_kw, 1)} kW`} />
          <Stat label={t("ov.losses")}
                value={`${fmt(s.q_loss_kw, 1)} kW (${fmt(s.loss_pct, 1)} %)`} />
          <Stat label={t("ov.pumpEl")} value={`${fmt(s.pump_el_kw, 2)} kW`} />
          <Stat label={`${t("ov.plantFlow")} / ${t("ov.plantReturn")}`}
                value={tempPair(s.t_flow_plant_c, s.t_return_plant_c)} />
          {setpoint != null && (
            <Stat label={t("ov.curveSetpoint")} value={`${fmt(setpoint, 1)} °C`} />
          )}
          <Stat label={t("ov.worstPoint")}
                value={`${fmt(s.dp_worst_bar, 2)} bar · ${s.worst_consumer ?? "—"}`}
                color={dpColor(s.dp_worst_bar)} />
        </>
      ) : (
        <>
          <Stat label={t("ov.feedIn")}
                value={os?.q_feed_kw != null ? `${fmt(os.q_feed_kw, 1)} kW` : t("ov.na")} />
          <Stat label={t("ov.demandMetered")}
                value={os?.q_demand_metered_kw != null
                  ? `${fmt(os.q_demand_metered_kw, 1)} kW` : t("ov.na")} />
          <Stat label={t("ov.pumpEl")}
                value={os?.pump_el_kw != null ? `${fmt(os.pump_el_kw, 2)} kW` : t("ov.na")} />
          <Stat label={`${t("ov.plantFlow")} / ${t("ov.plantReturn")}`}
                value={tempPair(os?.t_flow_plant_c, os?.t_return_plant_c)} />
          {setpoint != null && (
            <Stat label={t("ov.curveSetpoint")} value={`${fmt(setpoint, 1)} °C`} />
          )}
          <Stat label={t("ov.worstPoint")}
                value={os?.dp_worst_bar != null
                  ? `${fmt(os.dp_worst_bar, 2)} bar · ${os.worst_consumer ?? "—"}`
                  : t("ov.na")}
                color={dpColor(os?.dp_worst_bar)} />
          <Stat label={t("ov.coverage")}
                value={os ? `${os.n_metered}/${os.n_consumers}` : "—"} />
        </>
      )}
      <Stat label={t("ov.weather")}
            value={weather?.t_amb_c != null
              ? `${fmt(weather.t_amb_c, 1)} °C${weather.override ? t("ov.override") : ""}`
              : "—"} />
      <div className="stat-row">
        <span className="muted">{t("ov.solver")}</span>
        <span className="v">{solverBadge ?? "—"}</span>
      </div>
      <Stat label={t("ov.solveTime")} value={solveMs != null ? `${fmt(solveMs, 1)} ms` : "—"} />
      {!reveal && (
        <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
          {t("ov.observedNote")}{!canReveal ? ` ${t("live.truthHidden")}` : ""}
        </div>
      )}
    </Section>
  );
}
