/**
 * MeasurementPanel (blueprint port, SPEC §9.2/§8a): the bulk measurement
 * tools — coverage stats + progress bars, placement presets and the meter
 * fidelity toggle (full = live each step, standard = 15-min Lastgang means
 * with an honest cold start). Individual meters/sensors are placed and
 * removed through each element's map context menu (ElementMenu).
 */
import { useTranslation } from "react-i18next";
import type { MeasurementsResponse, MeterMode, MeterPreset } from "../types";
import Section from "./Section";

const PRESETS: MeterPreset[] = [
  "all_consumers", "plant_only", "key_points", "clear",
];

function CoverageBar({ label, n, total, fraction }: {
  label: string; n: number; total: number; fraction: number;
}) {
  return (
    <div style={{ marginTop: 4 }}>
      <div className="muted" style={{
        fontSize: "0.7rem", fontVariantNumeric: "tabular-nums",
        display: "flex", justifyContent: "space-between",
      }}>
        <span>{label}</span>
        <span>{n}/{total}</span>
      </div>
      <div style={{
        height: 5, background: "#1b2028", borderRadius: 3,
        overflow: "hidden", margin: "2px 0 4px",
      }}>
        <span style={{
          display: "block", height: "100%", background: "#4c8dff",
          width: `${Math.round((fraction || 0) * 100)}%`,
        }} />
      </div>
    </div>
  );
}

export default function MeasurementPanel({
  open, onToggle, placement, onPreset, onMode,
}: {
  open: boolean;
  onToggle: () => void;
  placement: MeasurementsResponse | null;
  onPreset: (name: MeterPreset) => void;
  onMode: (name: MeterMode) => void;
}) {
  const { t } = useTranslation();
  if (!placement) {
    return (
      <Section title={t("meas.heading")} open={open} onToggle={onToggle}>
        <div className="muted" style={{ fontSize: "0.72rem" }}>—</div>
      </Section>
    );
  }
  const cov = placement.coverage;
  const nPlaced = cov.n_consumer_meters + cov.n_node_sensors;
  const std = placement.mode === "standard";

  return (
    <Section title={t("meas.heading")} open={open} onToggle={onToggle}>
      {/* meter fidelity: what each placed device actually delivers */}
      <div className="mbar-seg" style={{ margin: "6px 0", marginLeft: 0 }}
           role="group" aria-label={t("meas.modeHdr")}>
        <button className={!std ? "on" : ""} title={t("meas.modeFullTitle")}
                onClick={() => onMode("full")}>
          {t("meas.modeFull")}
        </button>
        <button className={std ? "on" : ""} title={t("meas.modeStdTitle")}
                onClick={() => onMode("standard")}>
          {t("meas.modeStd")}
        </button>
      </div>

      <CoverageBar label={`📟 ${t("meas.meters")}`}
                   n={cov.n_consumer_meters} total={cov.n_consumers}
                   fraction={cov.consumer_fraction} />
      <CoverageBar label={`🌡️ ${t("meas.sensors")}`}
                   n={cov.n_node_sensors} total={cov.n_nodes}
                   fraction={cov.node_fraction} />
      {nPlaced === 0 && (
        <div className="muted" style={{ fontSize: "0.72rem" }}>
          {t("meas.none")}
        </div>
      )}

      <div className="muted" style={{
        fontSize: "0.68rem", textTransform: "uppercase",
        letterSpacing: "0.05em", marginTop: 7,
      }}>
        {t("meas.presetHdr")}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
        {PRESETS.map((p) => (
          <button key={p} className="ghost"
                  style={{
                    fontSize: "0.68rem", padding: "1px 6px",
                    ...(placement.preset === p ? { borderColor: "#4c8dff" } : {}),
                  }}
                  title={t(`meas.preset_${p}Title`)}
                  onClick={() => onPreset(p)}>
            {t(`meas.preset_${p}`)}
          </button>
        ))}
      </div>
      <div className="muted" style={{ fontSize: "0.68rem", marginTop: 5 }}>
        {std ? t("meas.stdHint") : t("meas.placeHint")}
      </div>
      {!placement.expose_ground_truth && (
        <div className="note" style={{ fontSize: "0.68rem", marginTop: 5 }}>
          {t("meas.strict")}
        </div>
      )}
    </Section>
  );
}
