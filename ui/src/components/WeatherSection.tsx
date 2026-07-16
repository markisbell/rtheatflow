/**
 * Weather knob (SPEC §4.1 / §8): drag the ambient temperature and watch
 * the whole network respond — the hot path (PUT /weather/override). The
 * space-heating demand rescales instantly via the §4.5 degree-hour factor;
 * releasing falls back to the profile.
 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { StepResult } from "../types";
import { fmt } from "../scales";
import Section, { Stat } from "./Section";

export default function WeatherSection({ open, onToggle, latest }: {
  open: boolean;
  onToggle: () => void;
  latest: StepResult | null;
}) {
  const { t } = useTranslation();
  const w = latest?.weather;
  const [drag, setDrag] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  const value = drag ?? w?.t_amb_c ?? 0;

  const push = (v: number) => {
    setDrag(v);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api.setWeatherOverride(v).catch(() => {});
    }, 150); // debounce the hot path while dragging
  };
  const release = () => {
    if (timer.current) window.clearTimeout(timer.current);
    setDrag(null);
    api.clearWeatherOverride().catch(() => {});
  };

  return (
    <Section title={t("wx.heading")} open={open} onToggle={onToggle}
             badges={w?.override ? [t("wx.badge")] : []}>
      <div className="field" style={{ marginTop: 6 }}>
        <label>
          {t("wx.knob", { t: fmt(value, 1) })}
          {w?.override && (
            <span className="badge" style={{ marginLeft: 6, background: "#4d3a10" }}>
              {t("wx.badge")}
            </span>
          )}
        </label>
        <input type="range" min={-30} max={45} step={0.5} value={value}
               style={{ width: "100%" }}
               onChange={(e) => push(+e.target.value)} />
      </div>
      {w?.override && (
        <button className="ghost" style={{ width: "100%", fontSize: "0.78rem" }}
                onClick={release}>
          {t("wx.release")}
        </button>
      )}
      <Stat label={t("wx.ground")}
            value={w?.t_ground_c != null ? `${fmt(w.t_ground_c, 1)} °C` : "—"} />
      <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
        {t("wx.hint")}
      </div>
    </Section>
  );
}
