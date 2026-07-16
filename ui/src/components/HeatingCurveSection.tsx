/**
 * Heating-curve editor (SPEC §4.2 / §8): curve parameters + the 3G/4G
 * preset buttons that make the temperature-lowering narrative one click —
 * switching presets visibly moves the loss/mass-flow KPIs in the Übersicht.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { HeatingCurveParams, StepResult } from "../types";
import { fmt } from "../scales";
import Section, { Stat } from "./Section";
import { curveSetpoint } from "./OverviewSection";

const FIELDS: (keyof HeatingCurveParams)[] = [
  "t_flow_design_c", "t_flow_min_c", "t_amb_design_c", "t_room_c", "n",
];

export default function HeatingCurveSection({ open, onToggle, latest }: {
  open: boolean;
  onToggle: () => void;
  latest: StepResult | null;
}) {
  const { t } = useTranslation();
  const live = latest?.controls?.heating_curve ?? null;
  const [draft, setDraft] = useState<HeatingCurveParams | null>(null);

  // adopt the live params whenever nothing is being edited
  useEffect(() => {
    if (live && draft === null) setDraft(live);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live === null]);

  const preset = (p: "3G" | "4G") =>
    api.setHeatingCurve({ preset: p })
      .then((r) => setDraft(r.params))
      .catch(() => {});
  const commit = (d: HeatingCurveParams) =>
    api.setHeatingCurve(d).then((r) => setDraft(r.params)).catch(() => {});

  const setpoint = curveSetpoint(live, latest?.weather?.t_amb_c);
  const shown = draft ?? live;

  return (
    <Section title={t("hc.heading")} open={open} onToggle={onToggle}>
      <div style={{ display: "flex", gap: 6, margin: "6px 0" }}>
        <button className="ghost" style={{ flex: 1, fontSize: "0.78rem" }}
                title={t("hc.preset3gTitle")} onClick={() => preset("3G")}>
          {t("hc.preset3g")}
        </button>
        <button className="ghost" style={{ flex: 1, fontSize: "0.78rem" }}
                title={t("hc.preset4gTitle")} onClick={() => preset("4G")}>
          {t("hc.preset4g")}
        </button>
      </div>
      {shown ? (
        <>
          {FIELDS.map((f) => (
            <div className="stat-row" key={f}>
              <span className="muted">{t(`hc.${f}`)}</span>
              <input type="number" className="num-input"
                     step={f === "n" ? 0.1 : 1}
                     value={shown[f]}
                     onChange={(e) => setDraft({ ...shown, [f]: +e.target.value })}
                     onBlur={() => draft && commit(draft)}
                     onKeyDown={(e) => e.key === "Enter"
                       && (e.target as HTMLInputElement).blur()} />
            </div>
          ))}
          {setpoint != null && (
            <Stat label={t("hc.setpoint")} value={`${fmt(setpoint, 1)} °C`} />
          )}
          <Stat label={t("hc.actual")}
                value={latest?.summary?.t_flow_plant_c != null
                  ? `${fmt(latest.summary.t_flow_plant_c, 1)} °C`
                  : latest?.observed_summary?.t_flow_plant_c != null
                    ? `${fmt(latest.observed_summary.t_flow_plant_c, 1)} °C`
                    : "—"} />
        </>
      ) : (
        <div className="muted" style={{ fontSize: "0.75rem", padding: "4px 0" }}>
          {t("hc.noCurve")}
        </div>
      )}
    </Section>
  );
}
