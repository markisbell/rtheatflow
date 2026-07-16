import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type {
  ApplyResponse, ArchetypeInfo, AssignPreview, LoadgenPolicy,
  NetworkImportBundle, NetworkListItem, NetworkPreview,
} from "../types";
import { fmt } from "../scales";
import Sparkline from "../components/Sparkline";

const FIVE_FILES = [
  "network_structure", "pipes", "consumers", "producers", "weather",
] as const;

/** The network workflow view (blueprint NetzStudio port, SPEC §8): column 1
 *  picks a catalog network, column 2 configures the §4.5 loadgen policy
 *  (mix, scaling, seed, day, 3G/4G temperature preset), column 3 previews
 *  the assignment — load-duration Sparkline vs design load, KPI tiles incl.
 *  the linear heat density with the ≥1–1.5 MWh/(m·a) viability rule of
 *  thumb — and applies it to the running engine. */
export default function NetzStudio({ selected, onSelect, onApplied }: {
  selected: string | null;
  onSelect: (id: string | null) => void;
  onApplied: (r: ApplyResponse) => void;
}) {
  const { t } = useTranslation();
  const [networks, setNetworks] = useState<NetworkListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [netPrev, setNetPrev] = useState<NetworkPreview | null>(null);

  // ---- loadgen policy (SPEC §4.5) ----
  const [archetypes, setArchetypes] = useState<ArchetypeInfo[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [preset, setPreset] = useState<"3G" | "4G" | null>(null);
  const [dayPct, setDayPct] = useState(0.9);
  const [scale, setScale] = useState(1);
  const [seed, setSeed] = useState(42);
  const [jitter, setJitter] = useState(true);
  const [preview, setPreview] = useState<AssignPreview | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.networks().then((r) => setNetworks(r.networks))
      .catch((e) => setError(String(e)));
    api.archetypes().then((r) => {
      setAvailable(r.available);
      setArchetypes(r.archetypes);
      setChosen(new Set(r.archetypes.map((a) => a.id)));
    }).catch(() => setAvailable(false));
  }, []);

  const policy = useMemo<LoadgenPolicy>(() => ({
    archetypes: chosen.size ? [...chosen] : null,
    seed, scale,
    day_percentile: dayPct,
    jitter,
    temperature_preset: preset,
  }), [chosen, seed, scale, dayPct, jitter, preset]);

  // topology preview of the selected network (stale responses ignored)
  const reqRef = useRef(0);
  useEffect(() => {
    setNetPrev(null);
    setPreview(null);
    if (!selected) return;
    const my = ++reqRef.current;
    api.networkPreview(selected)
      .then((p) => { if (reqRef.current === my) setNetPrev(p); })
      .catch((e) => { if (reqRef.current === my) setError(String(e)); });
  }, [selected]);

  // auto-preview the assignment (debounced) on network/policy changes
  const asgRef = useRef(0);
  useEffect(() => {
    if (!selected || !available) return;
    const my = ++asgRef.current;
    setBusy(true);
    const id = window.setTimeout(() => {
      api.assign(selected, policy)
        .then((r) => { if (asgRef.current === my) { setPreview(r); setError(null); } })
        .catch((e) => { if (asgRef.current === my) setError(String(e)); })
        .finally(() => { if (asgRef.current === my) setBusy(false); });
    }, 500);
    return () => window.clearTimeout(id);
  }, [selected, policy, available]);

  const apply = (withLoadgen: boolean) => {
    if (!selected) return;
    setBusy(true);
    api.applyConfig(selected, withLoadgen ? policy : undefined)
      .then(onApplied)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  };

  // ---- M6: five-file bundle import (POST /networks/import) ----
  const fileRef = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState<string | null>(null);

  const importFiles = async (files: File[]) => {
    setNote(null);
    try {
      let bundle: NetworkImportBundle;
      if (files.length === 1) {
        // a single JSON carrying all five documents as keys
        const doc = JSON.parse(await files[0].text());
        const missing = FIVE_FILES.filter((k) => !(k in doc));
        if (missing.length) {
          throw new Error(`${t("netz.importNeedFive")} (${missing.join(", ")})`);
        }
        bundle = doc as NetworkImportBundle;
      } else {
        // the five contract files picked together (named <doc>.json)
        const byName: Record<string, unknown> = {};
        for (const f of files) {
          const key = f.name.replace(/\.json$/i, "");
          if ((FIVE_FILES as readonly string[]).includes(key)) {
            byName[key] = JSON.parse(await f.text());
          }
        }
        const missing = FIVE_FILES.filter((k) => !(k in byName));
        if (missing.length) {
          throw new Error(`${t("netz.importNeedFive")} (${missing.join(", ")})`);
        }
        bundle = byName as unknown as NetworkImportBundle;
      }
      const r = await api.importNetwork(bundle);
      const nets = await api.networks();
      setNetworks(nets.networks);
      onSelect(r.id);
      setNote(t("netz.imported", { name: r.name }));
    } catch (e) {
      setNote(`${t("netz.importErr")} ${String(e)}`);
    }
  };

  const toggle = (id: string) =>
    setChosen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const lhd = preview?.kpis.linear_heat_density_mwh_per_m_a
    ?? netPrev?.linear_heat_density_mwh_per_m_a ?? null;
  const lhdTone = lhd == null ? undefined
    : lhd >= 1.0 ? "ok" : lhd >= 0.5 ? "warn" : "bad";

  if (error && !networks) return <div className="empty">{t("netz.failed")}<br />{error}</div>;
  if (!networks) return <div className="spinner">{t("netz.loading")}</div>;

  return (
    <div className="netzstudio">
      {/* ---- 1 · pick or import a network -------------------------------- */}
      <aside className="ns-list">
        <h3>{t("netz.step1")}</h3>
        <button className="ghost" style={{ width: "100%" }}
                title={t("netz.importTitle")}
                onClick={() => fileRef.current?.click()}>
          ⬆ {t("netz.import")}
        </button>
        <input ref={fileRef} type="file" multiple
               accept=".json,application/json" style={{ display: "none" }}
               onChange={(e) => {
                 const files = Array.from(e.target.files ?? []);
                 if (files.length) importFiles(files);
                 e.target.value = "";
               }} />
        {note && (
          <p className="note" style={{ fontSize: "0.75rem" }}>{note}</p>
        )}
        <div className="ns-hdr">{t("netz.library")}</div>
        {networks.filter((n) => n.source !== "user").map((n) => (
          <NetworkRow key={n.id} n={n} selected={n.id === selected}
                      onClick={() => onSelect(n.id)} />
        ))}
        {networks.some((n) => n.source === "user") && (
          <>
            <div className="ns-hdr">{t("netz.own")}</div>
            {networks.filter((n) => n.source === "user").map((n) => (
              <NetworkRow key={n.id} n={n} selected={n.id === selected}
                          onClick={() => onSelect(n.id)} />
            ))}
          </>
        )}
      </aside>

      {/* ---- 2 · loadgen policy ----------------------------------------- */}
      <section className="ns-config">
        <h3>{t("netz.step2")}</h3>
        {!selected && <div className="muted">{t("netz.pickHint")}</div>}
        {selected && (
          <>
            {!available && <p className="note">{t("netz.noCache")}</p>}
            <div className="field">
              <label>{t("netz.tempPreset")}</label>
              <div className="mbar-seg" style={{ marginLeft: 0 }}>
                <button className={preset === "3G" ? "on" : ""}
                        title={t("netz.preset3gTitle")}
                        onClick={() => setPreset(preset === "3G" ? null : "3G")}>
                  {t("netz.preset3g")}
                </button>
                <button className={preset === "4G" ? "on" : ""}
                        title={t("netz.preset4gTitle")}
                        onClick={() => setPreset(preset === "4G" ? null : "4G")}>
                  {t("netz.preset4g")}
                </button>
              </div>
              <span className="muted" style={{ fontSize: "0.72rem" }}>
                {t("netz.tempPresetHint")}
              </span>
            </div>
            <div className="field">
              <label>{t("netz.day")}</label>
              <select value={String(dayPct)}
                      onChange={(e) => setDayPct(+e.target.value)}>
                <option value="0.98">{t("netz.dayDesign")}</option>
                <option value="0.9">{t("netz.dayWinter")}</option>
                <option value="0.5">{t("netz.dayShoulder")}</option>
                <option value="0.08">{t("netz.daySummer")}</option>
              </select>
            </div>
            {archetypes && available && (
              <div className="field">
                <label>{t("netz.archetypes", { count: chosen.size })}</label>
                <div className="arch-list">
                  {archetypes.map((a) => (
                    <label className="arch-row" key={a.id}>
                      <input type="checkbox" checked={chosen.has(a.id)}
                             onChange={() => toggle(a.id)} />
                      <span style={{ flex: 1 }}>{a.name}</span>
                      <span className="muted">
                        {fmt((a.annual_kwh.space_heating + a.annual_kwh.dhw_mean) / 1000, 1)} MWh/a
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <div className="field">
              <label>{t("netz.scale", { scale: scale.toFixed(2) })}</label>
              <input type="range" min={0.2} max={3} step={0.1} value={scale}
                     onChange={(e) => setScale(+e.target.value)} />
            </div>
            <div className="field">
              <label>{t("netz.seed")}</label>
              <input type="number" value={seed}
                     onChange={(e) => setSeed(Math.max(0, +e.target.value))} />
            </div>
            <div className="field">
              <label className="arch-row" style={{ padding: 0 }}>
                <input type="checkbox" checked={jitter}
                       onChange={() => setJitter(!jitter)} />
                <span style={{ flex: 1 }}>{t("netz.jitter")}</span>
              </label>
              <span className="muted" style={{ fontSize: "0.72rem" }}>
                {t("netz.jitterHint")}
              </span>
            </div>
          </>
        )}
      </section>

      {/* ---- 3 · preview & apply ----------------------------------------- */}
      <section className="ns-preview">
        <h3>{t("netz.step3")}</h3>
        {!selected && <div className="muted">{t("netz.pickHint")}</div>}
        {selected && netPrev && (
          <div className="kpis">
            <Kpi k={t("netz.kConsumers")} v={`${netPrev.n_consumers}`} />
            <Kpi k={t("netz.kTrench")} v={`${fmt(netPrev.trench_km, 2)} km`} />
            <Kpi k={t("netz.kDesign")}
                 v={`${fmt(preview?.kpis.design_load_kw ?? netPrev.design_load_kw, 0)} kW`} />
            {lhd != null && (
              <Kpi k={t("netz.kLhd")} v={`${fmt(lhd, 2)}`} tone={lhdTone}
                   title={t("netz.lhdRule")} />
            )}
          </div>
        )}
        {selected && busy && !preview && <div className="spinner">…</div>}
        {selected && preview && (
          <>
            <div className="ns-chart">
              <Sparkline values={preview.duration_kw} width={640} height={280}
                         hourAxis={false} step fluid
                         marker={preview.kpis.design_load_kw}
                         xTitle={t("netz.axDuration")}
                         yTitle={t("netz.axLoad")} />
            </div>
            <p className="muted" style={{ fontSize: "0.78rem" }}>
              {t("netz.chartDesc", {
                peak: fmt(preview.kpis.peak_load_kw, 0),
                mean: fmt(preview.kpis.mean_load_kw, 0),
                annual: fmt(preview.kpis.annual_mwh, 0),
              })}
              {lhd != null && lhd < 1.0 && (
                <span className="note"> {t("netz.lhdWarn")}</span>
              )}
            </p>
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.6rem" }}>
              <button className="primary" onClick={() => apply(true)} disabled={busy}>
                {t("netz.applyRun")}
              </button>
              <button className="ghost" onClick={() => apply(false)} disabled={busy}
                      title={t("netz.applyPlainHint")}>
                {t("netz.applyPlain")}
              </button>
            </div>
            {error && <p className="note">{error}</p>}
          </>
        )}
        {selected && !available && netPrev && (
          <button className="primary" onClick={() => apply(false)} disabled={busy}>
            {t("netz.applyPlain")}
          </button>
        )}
      </section>
    </div>
  );
}

function NetworkRow({ n, selected, onClick }: {
  n: NetworkListItem; selected: boolean; onClick: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={`ns-row${selected ? " sel" : ""}`} onClick={onClick}>
      <div className="title">{n.name}</div>
      <div className="sub">
        {n.character && (
          <span className="tag">
            {t(`netz.ch_${n.character}`, { defaultValue: n.character })}
          </span>
        )}
        {n.nodes != null && (
          <span className="muted"> {t("netz.nodes", { count: n.nodes })}</span>
        )}
        {n.trench_km != null && (
          <span className="muted"> · {fmt(n.trench_km, 2)} km</span>
        )}
      </div>
    </div>
  );
}

function Kpi({ k, v, tone, title }: {
  k: string; v: string; tone?: "ok" | "warn" | "bad"; title?: string;
}) {
  const color = tone === "bad" ? "#ef4444" : tone === "warn" ? "#f59e0b"
    : tone === "ok" ? "#22c55e" : undefined;
  return (
    <div className="kpi" title={title}>
      <div className="v" style={color ? { color } : undefined}>{v}</div>
      <div className="k">{k}</div>
    </div>
  );
}
