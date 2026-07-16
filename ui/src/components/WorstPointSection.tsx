/**
 * WorstPointSection (blueprint AmpelSection analog, SPEC §8): the §4.3
 * Schlechtpunktregelung cockpit — mode toggle (geregelt/ungeregelt), Δp
 * setpoint editor (0.3–2.0 bar), live worst-point trace vs the setpoint,
 * pump lift + P_el readout (the oversizing penalty in plain sight).
 *
 * The controller runs server-side once per tick after the solve; this
 * section only edits its config and visualizes the observed trace.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { DpControlInfo, StepResult } from "../types";
import { dpColor, fmt } from "../scales";
import Section, { Stat } from "./Section";
import Sparkline from "./Sparkline";

export default function WorstPointSection({ open, onToggle, latest, trace }: {
  open: boolean;
  onToggle: () => void;
  latest: StepResult | null;
  /** client-accumulated worst-point Δp per frame (observed layer) */
  trace: number[];
}) {
  const { t } = useTranslation();
  const [info, setInfo] = useState<DpControlInfo | null>(null);
  const [setpoint, setSetpoint] = useState(0.7);
  const [plift, setPlift] = useState(2.0);

  useEffect(() => {
    api.dpControl().then((d) => {
      setInfo(d);
      setSetpoint(d.setpoint_bar);
      if (d.plift_bar != null) setPlift(d.plift_bar);
    }).catch(() => {});
  }, []);

  const dc = latest?.controls?.dp_control;
  const mode = dc?.mode ?? info?.mode ?? "fixed";
  const dpObserved = dc?.dp_worst_observed_bar
    ?? latest?.observed_summary?.dp_worst_bar ?? null;
  const worst = latest?.observed_summary?.worst_consumer ?? null;
  const pumpEl = latest?.summary?.pump_el_kw
    ?? latest?.observed_summary?.pump_el_kw ?? null;
  const livePlift = dc?.plift_bar ?? info?.plift_bar ?? null;

  const setMode = (m: "controlled" | "fixed") =>
    api.setDpControl({ mode: m }).then(setInfo).catch(() => {});
  const commitSetpoint = (v: number) =>
    api.setDpControl({ setpoint_bar: v }).then(setInfo).catch(() => {});
  const commitPlift = (v: number) =>
    api.setDpControl({ plift_bar: v }).then(setInfo).catch(() => {});

  return (
    <Section title={t("wp.heading")} open={open} onToggle={onToggle}>
      <div className="mbar-seg" style={{ margin: "6px 0", marginLeft: 0 }}
           role="group" aria-label={t("wp.mode")}>
        <button className={mode === "controlled" ? "on" : ""}
                title={t("wp.controlledTitle")}
                onClick={() => setMode("controlled")}>
          {t("wp.controlled")}
        </button>
        <button className={mode === "fixed" ? "on" : ""}
                title={t("wp.fixedTitle")}
                onClick={() => setMode("fixed")}>
          {t("wp.fixed")}
        </button>
      </div>

      {mode === "controlled" ? (
        <div className="field">
          <label>{t("wp.setpoint", { bar: setpoint.toFixed(2) })}</label>
          <input type="range" min={0.3} max={2.0} step={0.05} value={setpoint}
                 style={{ width: "100%" }}
                 onChange={(e) => setSetpoint(+e.target.value)}
                 onMouseUp={() => commitSetpoint(setpoint)}
                 onTouchEnd={() => commitSetpoint(setpoint)} />
        </div>
      ) : (
        <div className="field">
          <label>{t("wp.plift", { bar: plift.toFixed(2) })}</label>
          <input type="range" min={0.1} max={4.0} step={0.05} value={plift}
                 style={{ width: "100%" }}
                 onChange={(e) => setPlift(+e.target.value)}
                 onMouseUp={() => commitPlift(plift)}
                 onTouchEnd={() => commitPlift(plift)} />
        </div>
      )}

      {trace.length > 1 && (
        <Sparkline values={trace} width={300} height={110} fluid
                   hourAxis={false} color="#7fd1ff"
                   marker={mode === "controlled" ? setpoint : undefined}
                   yTitle={t("wp.yTitle")} />
      )}

      <Stat label={t("wp.observed")}
            value={dpObserved != null
              ? `${fmt(dpObserved, 2)} bar · ${worst ?? "—"}`
              : t("wp.blind")}
            color={dpColor(dpObserved)} />
      <Stat label={t("wp.pliftNow")}
            value={livePlift != null ? `${fmt(livePlift, 2)} bar` : "—"} />
      <Stat label={t("wp.pumpEl")}
            value={pumpEl != null ? `${fmt(pumpEl, 2)} kW` : "—"} />
      <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
        {mode === "controlled" ? t("wp.hintControlled") : t("wp.hintFixed")}
      </div>
    </Section>
  );
}
