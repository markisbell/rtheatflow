/**
 * Pinned element sections + small equipment input controls (blueprint
 * EquipmentControls port, DH domain): Ctrl-click on a map element pins a
 * details Section here (SPEC §8 interaction grammar); the section shows
 * live values from the stream and offers the element's config knobs.
 */
import { useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { StepResult } from "../types";
import { dpColor, fmt } from "../scales";
import Section, { Stat } from "./Section";

export interface PinTarget {
  kind: "consumer" | "producer" | "storage";
  id: number;
  name: string;
}

const numStyle: CSSProperties = {
  width: 70, fontSize: "0.75rem", background: "var(--panel-2)",
  color: "var(--text)", border: "1px solid var(--border)", borderRadius: 4,
  padding: "1px 4px", textAlign: "right",
};

function NumInput({ value, onCommit, min, step }: {
  value: number; onCommit: (v: number) => void; min?: number; step?: number;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input type="number" style={numStyle} value={v} min={min} step={step}
           onChange={(e) => setV(+e.target.value)}
           onBlur={() => v !== value && v > (min ?? -Infinity) && onCommit(v)}
           onKeyDown={(e) => e.key === "Enter"
             && (e.target as HTMLInputElement).blur()} />
  );
}

export default function PinnedSection({ pin, latest, onClose, onChanged }: {
  pin: PinTarget;
  latest: StepResult | null;
  onClose: () => void;
  onChanged: () => void; // equipment inventory changed -> reload topology
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);

  const head = (icon: string) => `${icon} ${pin.name}`;
  const remove = (fn: () => Promise<unknown>) => () =>
    fn().then(() => { onClose(); onChanged(); }).catch(() => {});

  if (pin.kind === "consumer") {
    const c = latest?.consumers?.find((x) => x.id === pin.id)
      ?? latest?.measurements?.consumers?.find((x) => x.id === pin.id);
    const kind = c && "kind" in c ? c.kind : undefined;
    return (
      <Section title={head(kind === "bypass" ? "🔀" : "🏠")} open={open}
               onToggle={() => setOpen(!open)} badges={["📌"]}>
        {c ? (
          <>
            <Stat label={t("pin.q")} value={`${fmt(c.q_kw, 1)} kW`} />
            <Stat label={t("pin.mdot")}
                  value={`${fmt(c.mdot_kg_per_s, 3)} kg/s`} />
            <Stat label={`${t("pin.tSupply")} / ${t("pin.tReturn")}`}
                  value={`${fmt(c.t_supply_c, 1)} / ${fmt(c.t_return_c, 1)} °C`} />
            <Stat label="Δp" value={`${fmt(c.dp_bar, 2)} bar`}
                  color={dpColor(c.dp_bar)} />
          </>
        ) : <div className="muted" style={{ fontSize: "0.75rem" }}>{t("pin.noData")}</div>}
        <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
          <button className="ghost" style={{ flex: 1, fontSize: "0.72rem" }}
                  onClick={remove(() => api.removeConsumer(pin.id))}>
            🗑️ {kind === "bypass" ? t("menu.removeBypass") : t("menu.removeConsumer")}
          </button>
          <button className="ghost" style={{ fontSize: "0.72rem" }} onClick={onClose}>
            {t("pin.unpin")}
          </button>
        </div>
      </Section>
    );
  }

  if (pin.kind === "producer") {
    const p = latest?.producers?.find((x) => x.id === pin.id);
    return (
      <Section title={head(p?.kind === "slack" ? "🏭"
                : p?.kind === "pump_mass" ? "⚙️" : "☀️")}
               open={open} onToggle={() => setOpen(!open)} badges={["📌"]}>
        {p ? (
          <>
            <Stat label={t("pin.q")} value={`${fmt(p.q_kw, 1)} kW`} />
            {p.kind === "slack" && (
              <>
                <Stat label={t("pin.tFlow")} value={`${fmt(p.t_flow_c, 1)} °C`} />
                <Stat label={t("pin.plift")} value={`${fmt(p.plift_bar, 2)} bar`} />
                <Stat label={t("pin.pumpEl")} value={`${fmt(p.pump_el_kw, 2)} kW`} />
                <Stat label={t("pin.plantKind")}
                      value={t(`plant.${p.plant_kind ?? "boiler"}`)} />
                {p.cop != null && (
                  <Stat label={t("pin.cop")}
                        value={`${fmt(p.cop, 2)} (${t("pin.tCold")} ${fmt(p.t_cold_c, 1)} °C)`} />
                )}
                {p.p_el_kw != null && (
                  <Stat label={p.plant_kind === "chp"
                          ? t("pin.pElChp") : t("pin.pEl")}
                        value={`${fmt(p.p_el_kw, 1)} kW`} />
                )}
                {p.p_fuel_kw != null && (
                  <Stat label={t("pin.pFuel")} value={`${fmt(p.p_fuel_kw, 1)} kW`} />
                )}
              </>
            )}
            {p.kind === "heat_exchanger" && (
              <div className="stat-row">
                <span className="muted">{t("pin.dispatch")}</span>
                <span><NumInput value={p.q_kw ?? 0} min={0.1} step={1}
                        onCommit={(v) => api.configProducer(pin.id,
                          { qext_w: v * 1000 }).catch(() => {})} /> kW</span>
              </div>
            )}
            {p.kind === "pump_mass" && (
              <>
                <div className="stat-row">
                  <span className="muted">{t("pin.mdot")}</span>
                  <span><NumInput value={p.mdot_kg_per_s ?? 0} min={0.001}
                          step={0.01}
                          onCommit={(v) => api.configProducer(pin.id,
                            { mdot_flow_kg_per_s: v }).catch(() => {})} /> kg/s</span>
                </div>
                <Stat label={t("pin.tFlow")} value={`${fmt(p.t_flow_c, 1)} °C`} />
              </>
            )}
          </>
        ) : <div className="muted" style={{ fontSize: "0.75rem" }}>{t("pin.noData")}</div>}
        <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
          {p && p.kind !== "slack" && (
            <button className="ghost" style={{ flex: 1, fontSize: "0.72rem" }}
                    onClick={remove(() => api.removeProducer(pin.id))}>
              🗑️ {t("menu.removeProducer")}
            </button>
          )}
          <button className="ghost" style={{ fontSize: "0.72rem" }} onClick={onClose}>
            {t("pin.unpin")}
          </button>
        </div>
      </Section>
    );
  }

  // storage
  const s = latest?.storages?.find((x) => x.id === pin.id);
  const socPct = s && s.capacity_kwh
    ? Math.round(100 * (s.soc_kwh ?? 0) / s.capacity_kwh) : null;
  return (
    <Section title={head("🛢️")} open={open} onToggle={() => setOpen(!open)}
             badges={["📌"]}>
      {s ? (
        <>
          <div className="soc-bar" title={`${socPct ?? "—"} %`}>
            <div className="soc-fill" style={{ width: `${socPct ?? 0}%` }} />
          </div>
          <Stat label={t("pin.soc")}
                value={`${fmt(s.soc_kwh, 1)} / ${fmt(s.capacity_kwh, 0)} kWh (${socPct ?? "—"} %)`} />
          <Stat label={t("pin.q")} value={`${fmt(s.q_kw, 1)} kW`} />
          <Stat label={t("pin.active")} value={t(`storage.${s.active}`)} />
          <div className="mbar-seg" style={{ margin: "6px 0", marginLeft: 0 }}>
            {(["charge", "discharge", "idle"] as const).map((m) => (
              <button key={m} className={s.mode === m ? "on" : ""}
                      onClick={() => api.configStorage(pin.id, { mode: m })
                        .catch(() => {})}>
                {t(`storage.set_${m}`)}
              </button>
            ))}
          </div>
          <div className="stat-row">
            <span className="muted">{t("pin.power")}</span>
            <span><NumInput value={s.power_kw ?? 0} min={1} step={5}
                    onCommit={(v) => api.configStorage(pin.id,
                      { power_kw: v }).catch(() => {})} /> kW</span>
          </div>
        </>
      ) : <div className="muted" style={{ fontSize: "0.75rem" }}>{t("pin.noData")}</div>}
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button className="ghost" style={{ flex: 1, fontSize: "0.72rem" }}
                onClick={remove(() => api.removeStorage(pin.id))}>
          🗑️ {t("menu.removeStorage")}
        </button>
        <button className="ghost" style={{ fontSize: "0.72rem" }} onClick={onClose}>
          {t("pin.unpin")}
        </button>
      </div>
    </Section>
  );
}
