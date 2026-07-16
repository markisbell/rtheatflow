import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type {
  ConsumerMeasurement,
  ConsumerState,
  PipeState,
  StepResult,
  Topology,
} from "../types";
import type { MapLayer } from "../App";
import type { MenuTarget } from "./ElementMenu";
import {
  DP_MIN_BAR,
  RETURN_GRADIENT,
  SUPPLY_GRADIENT,
  T_RETURN_HIGH,
  T_RETURN_LOW,
  T_SUPPLY_LOW,
  UNOBSERVED,
  UNOBSERVED_DASH,
  UNOBSERVED_LINE,
  V_MAX,
  VELOCITY_GRADIENT,
  consumerRadius,
  dpColor,
  fmt,
  mdotWidth,
  returnTempColor,
  supplyTempColor,
  velocityColor,
} from "../scales";

interface Props {
  topo: Topology;
  latest: StepResult | null;
  layer: MapLayer;
  /** the map-corner switch mirrors the Ansicht menu (shared lifted state) */
  onLayer: (layer: MapLayer) => void;
  /** measured view: color only sensored elements, grey/dash the rest */
  observedOnly: boolean;
  /** supply-ramp anchor: the active heating curve's design temperature */
  tFlowDesign: number;
  /** right-click context menu on elements/nodes (SPEC §8 grammar, M4) */
  onMenu?: (target: MenuTarget) => void;
  /** Ctrl-click pins an element details section (SPEC §8) */
  onPin?: (target: MenuTarget) => void;
}

const LAYERS: MapLayer[] = ["supply", "return", "velocity", "dp"];

const PLANT_COLOR = "#f2ae00"; // amber station marker (blueprint convention)

const TILES = {
  light: {
    url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    bg: "#e9eaec",
    stroke: "#3a3a3a",
  },
  dark: {
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    bg: "#0b0d11",
    stroke: "#0b0d11",
  },
};

interface TrenchLive { s?: PipeState; r?: PipeState }

/** Live district-heating net on OSM/CARTO tiles: ONE polyline per trench
 *  (shared supply/return geometry, SPEC §5), consumers as circle markers
 *  sized by design load, the plant as an amber station marker. All vector
 *  layers are created once and restyled in place per WS frame (`setStyle`
 *  only — never rebuilt). The unknown is styled as unknown: without a frame,
 *  or for unsensored elements in the measured view, elements render in the
 *  dedicated UNOBSERVED grey/dash — never in a healthy ramp color. */
export default function MapDiagram({
  topo, latest, layer, onLayer, observedOnly, tFlowDesign, onMenu, onPin,
}: Props) {
  const { t, i18n } = useTranslation();
  const elRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileRef = useRef<L.TileLayer | null>(null);
  const trenchRef = useRef<Map<number, L.Polyline>>(new Map());
  const consRef = useRef<Map<number, L.CircleMarker>>(new Map());
  const equipRef = useRef<Map<string, L.Marker>>(new Map());
  const plantRef = useRef<L.CircleMarker | null>(null);
  const [light, setLight] = useState(true);

  // popup content readers pull the CURRENT frame/layer state through refs,
  // so a popup opened once keeps updating while frames stream in
  const liveRef = useRef<{
    latest: StepResult | null;
    observedOnly: boolean;
  }>({ latest: null, observedOnly: false });
  liveRef.current = { latest, observedOnly };
  const cbRef = useRef<{ onMenu?: Props["onMenu"]; onPin?: Props["onPin"] }>({});
  cbRef.current = { onMenu, onPin };

  // right-click → ElementMenu; Ctrl-click → pinned section (SPEC §8)
  const wireInteractions = (
    lyr: L.Layer, target: Omit<MenuTarget, "x" | "y">,
  ) => {
    lyr.on("contextmenu", (e: L.LeafletMouseEvent) => {
      L.DomEvent.stop(e);
      const oe = e.originalEvent;
      cbRef.current.onMenu?.({ ...target, x: oe.clientX, y: oe.clientY });
    });
    lyr.on("click", (e: L.LeafletMouseEvent) => {
      if (!e.originalEvent.ctrlKey) return; // plain click keeps the popup
      L.DomEvent.stop(e);
      (lyr as L.Marker).closePopup?.();
      const oe = e.originalEvent;
      cbRef.current.onPin?.({ ...target, x: oe.clientX, y: oe.clientY });
    });
  };

  // ---- live-data lookups ----------------------------------------------------

  const trenchLive = (id: number): TrenchLive => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    const out: TrenchLive = {};
    if (!f || obs) return out; // pipes carry no heat meter (M3 sensor model)
    for (const p of f.pipes ?? []) {
      if (p.trench !== id) continue;
      if (p.side === "s") out.s = p;
      else out.r = p;
    }
    return out;
  };

  const consumerLive = (id: number): ConsumerState | ConsumerMeasurement | undefined => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    if (!f) return undefined;
    const src = obs ? f.measurements?.consumers : f.consumers;
    return src?.find((c) => c.id === id);
  };

  // ---- popup HTML (built lazily at open + refreshed per frame) --------------

  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const row = (k: string, v: string) =>
    `<span style="color:var(--muted)">${k}</span> ${v}`;

  const trenchPopup = (trench: Topology["trenches"][number]): string => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    const head = `<b>${esc(t("pop.trench", { from: trench.from_node, to: trench.to_node }))}</b>`
      + `<br><span style="color:var(--muted)">${esc(trench.std_type ?? `${fmt(trench.inner_diameter_mm, 0)} mm`)}`
      + ` · ${t("pop.length")} ${fmt(trench.length_km * 1000, 0)} m</span>`;
    if (!f) return `${head}<br>${t("pop.noData")}`;
    if (obs) return `${head}<br>${t("pop.unobserved")}`;
    const { s, r } = trenchLive(trench.id);
    const side = (label: string, p?: PipeState) => p
      ? `<br><b>${label}</b>: ${fmt(p.t_from_c, 1)} → ${fmt(p.t_to_c, 1)} °C · `
        + `${fmt(Math.abs(p.mdot_kg_per_s ?? NaN), 3)} kg/s · `
        + `${fmt(Math.abs(p.v_m_per_s ?? NaN), 2)} m/s · `
        + `${t("pop.loss")} ${fmt(p.q_loss_kw, 2)} kW`
      : "";
    return head + side(t("pop.supplyPipe"), s) + side(t("pop.returnPipe"), r);
  };

  const consumerPopup = (cons: Topology["consumers"][number]): string => {
    const { latest: f } = liveRef.current;
    const head = `<b>${esc(t("tip.consumer", { name: cons.name }))}</b>`
      + `<br><span style="color:var(--muted)">${t("pop.designLoad")} ${fmt(cons.q_design_w / 1000, 1)} kW</span>`;
    if (!f) return `${head}<br>${t("pop.noData")}`;
    const c = consumerLive(cons.id);
    if (!c) return `${head}<br>${t("pop.unobserved")}`;
    return `${head}<br>${row(t("pop.q"), `${fmt(c.q_kw, 1)} kW`)} · `
      + row(t("pop.mdot"), `${fmt(c.mdot_kg_per_s, 3)} kg/s`)
      + `<br>${row(t("pop.tSupply"), `${fmt(c.t_supply_c, 1)} °C`)} · `
      + row(t("pop.tReturn"), `${fmt(c.t_return_c, 1)} °C`)
      + `<br>${row(t("pop.dp"), `${fmt(c.dp_bar, 2)} bar`)}`;
  };

  const plantPopup = (): string => {
    const { latest: f } = liveRef.current;
    const plant = topo.producers.find((p) => p.kind === "slack");
    const head = `<b>${esc(t("tip.plant", { name: plant?.name ?? "?" }))}</b>`;
    const live = f?.producers.find((p) => p.kind === "slack");
    if (!live) return `${head}<br>${t("pop.noData")}`;
    return `${head}<br>${row(t("pop.qFeed"), `${fmt(live.q_kw, 1)} kW`)}`
      + `<br>${row(t("pop.tSupply"), `${fmt(live.t_flow_c, 1)} °C`)} · `
      + row("Δp", `${fmt(live.plift_bar, 2)} bar`)
      + `<br>${row(t("pop.pumpEl"), `${fmt(live.pump_el_kw, 2)} kW`)}`;
  };

  // ---- build map + static layers ONCE per topology (and language) -----------

  useEffect(() => {
    if (!elRef.current) return;
    const map = L.map(elRef.current, {
      preferCanvas: true,
      zoomSnap: 0.25,
      attributionControl: false,
    });
    mapRef.current = map;
    map.zoomControl.setPosition("topleft");
    L.control.attribution({ prefix: false, position: "bottomleft" }).addAttribution(
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    ).addTo(map);

    const nodeGeo = new Map<string, [number, number]>();
    for (const n of topo.nodes) nodeGeo.set(n.name, n.geo);

    trenchRef.current.clear();
    const allPts: [number, number][] = [];
    for (const tr of topo.trenches) {
      const latlngs: [number, number][] = tr.geometry.length >= 2
        ? tr.geometry
        : ([nodeGeo.get(tr.from_node), nodeGeo.get(tr.to_node)]
            .filter((p): p is [number, number] => !!p));
      if (latlngs.length < 2) continue;
      allPts.push(...latlngs);
      const pl = L.polyline(latlngs, {
        color: UNOBSERVED_LINE, weight: 2, opacity: 0.95,
      }).addTo(map);
      pl.bindTooltip(t("tip.trench", { from: tr.from_node, to: tr.to_node }));
      pl.bindPopup(() => trenchPopup(tr), { autoPan: false });
      trenchRef.current.set(tr.id, pl);
    }

    // plain trench nodes (small, always visible): the placement handles —
    // right-click opens the §8 node → add producer/storage/bypass/consumer
    for (const n of topo.nodes) {
      if (n.kind === "consumer" || n.kind === "plant") continue;
      const nm = L.circleMarker(n.geo, {
        radius: 3.5, color: "#5b6472", weight: 1,
        fillColor: "#39424f", fillOpacity: 0.9,
      }).addTo(map);
      nm.bindTooltip(t("tip.node", { name: n.name }));
      wireInteractions(nm, {
        kind: "node", id: n.name, name: n.name, node: n.name,
      });
    }

    consRef.current.clear();
    for (const c of topo.consumers) {
      const p = nodeGeo.get(c.node);
      if (!p) continue;
      allPts.push(p);
      if (c.kind === "bypass") {
        // §3.2 Netzschluss-Bypass: emoji marker, not a demand circle
        const bm = L.marker(p, {
          icon: L.divIcon({ className: "equip-icon", html: "🔀",
                            iconAnchor: [7, 7] }),
        }).addTo(map);
        bm.bindTooltip(t("tip.bypass", { name: c.name }));
        wireInteractions(bm, {
          kind: "consumer", id: c.id, name: c.name, node: c.node,
          consumerKind: "bypass",
        });
        continue;
      }
      const cm = L.circleMarker(p, {
        radius: consumerRadius(c.q_design_w),
        color: TILES[light ? "light" : "dark"].stroke,
        weight: 1,
        fillColor: UNOBSERVED,
        fillOpacity: 0.9,
      }).addTo(map);
      cm.bindTooltip(t("tip.consumer", { name: c.name }));
      cm.bindPopup(() => consumerPopup(c), { autoPan: false });
      wireInteractions(cm, {
        kind: "consumer", id: c.id, name: c.name, node: c.node,
        consumerKind: "consumer",
      });
      consRef.current.set(c.id, cm);
    }

    plantRef.current = null;
    const plant = topo.producers.find((p) => p.kind === "slack");
    const plantPos = plant ? nodeGeo.get(plant.node) : undefined;
    if (plant && plantPos) {
      allPts.push(plantPos);
      const cm = L.circleMarker(plantPos, {
        radius: 8, color: "#7a5400", weight: 1.5,
        fillColor: PLANT_COLOR, fillOpacity: 1,
      }).addTo(map);
      cm.bindTooltip(t("tip.plant", { name: plant.name }));
      cm.bindPopup(() => plantPopup(), { autoPan: false });
      wireInteractions(cm, {
        kind: "producer", id: plant.id, name: plant.name, node: plant.node,
        producerKind: "slack",
      });
      plantRef.current = cm;
      // decorative equipment glyph (never intercepts clicks)
      L.marker(plantPos, {
        icon: L.divIcon({ className: "plant-icon", html: "🏭", iconAnchor: [-6, 18] }),
        interactive: false, keyboard: false,
      }).addTo(map);
    }

    const fit = () => {
      if (allPts.length) map.fitBounds(L.latLngBounds(allPts).pad(0.08));
    };
    fit();
    // re-measure + re-fit once layout has settled: when the map mounts in
    // the same commit that lays out the grid, the container can still be
    // 0-sized at construction and fitBounds lands at world zoom
    const timer = setTimeout(() => {
      map.invalidateSize();
      fit();
    }, 80);

    return () => {
      clearTimeout(timer);
      map.remove();
      mapRef.current = null;
      tileRef.current = null;
      equipRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topo, i18n.language]); // rebuild (incl. tooltips) on language change

  // ---- live equipment markers (M4): placed producers & storages come and
  // go at runtime — driven from the frame's inventory, diffed per frame ----

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const nodeGeo = new Map<string, [number, number]>(
      topo.nodes.map((n) => [n.name, n.geo]));
    interface Want {
      emoji: string; pos: [number, number]; title: string;
      anchor: [number, number]; target: Omit<MenuTarget, "x" | "y">;
    }
    const want = new Map<string, Want>();
    for (const p of latest?.producers ?? []) {
      if (p.kind === "slack") continue;
      const pos = nodeGeo.get(p.node);
      if (!pos) continue;
      want.set(`p${p.id}`, {
        emoji: p.kind === "heat_exchanger" ? "☀️" : "⚙️", pos, title: p.name,
        anchor: p.kind === "heat_exchanger" ? [22, 10] : [22, 26],
        target: { kind: "producer", id: p.id, name: p.name, node: p.node,
                  producerKind: p.kind },
      });
    }
    for (const s of latest?.storages ?? []) {
      const pos = nodeGeo.get(s.node);
      if (!pos) continue;
      want.set(`s${s.id}`, {
        emoji: "🛢️", pos, title: s.name, anchor: [-8, 10],
        target: { kind: "storage", id: s.id, name: s.name, node: s.node },
      });
    }
    for (const [key, mk] of equipRef.current) {
      if (!want.has(key)) {
        map.removeLayer(mk);
        equipRef.current.delete(key);
      }
    }
    for (const [key, w] of want) {
      if (equipRef.current.has(key)) continue;
      const mk = L.marker(w.pos, {
        icon: L.divIcon({ className: "equip-icon", html: w.emoji,
                          iconAnchor: w.anchor }),
      }).addTo(map);
      mk.bindTooltip(w.title);
      wireInteractions(mk, w.target);
      equipRef.current.set(key, mk);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest, topo]);

  // ---- basemap (light/dark) --------------------------------------------------

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const theme = light ? TILES.light : TILES.dark;
    if (tileRef.current) map.removeLayer(tileRef.current);
    tileRef.current = L.tileLayer(theme.url, { maxZoom: 20 }).addTo(map);
    tileRef.current.bringToBack();
    map.getContainer().style.background = theme.bg;
    for (const cm of consRef.current.values()) cm.setStyle({ color: theme.stroke });
  }, [light, topo]);

  // ---- per-frame restyle: `.setStyle()` only, layers are never rebuilt -------

  useEffect(() => {
    if (!mapRef.current) return;
    const f = latest;
    const trenchData = new Map<number, TrenchLive>();
    let maxMdot = 0;
    if (f && !observedOnly) {
      for (const p of f.pipes ?? []) {
        const e = trenchData.get(p.trench) ?? {};
        if (p.side === "s") e.s = p;
        else e.r = p;
        trenchData.set(p.trench, e);
        maxMdot = Math.max(maxMdot, Math.abs(p.mdot_kg_per_s ?? 0));
      }
    }

    for (const [id, pl] of trenchRef.current) {
      const { s, r } = trenchData.get(id) ?? {};
      const known = observedOnly ? false : !!(s || r); // pipes carry no meter
      if (!known) {
        pl.setStyle({ color: UNOBSERVED_LINE, weight: 2, opacity: 0.9,
                      dashArray: UNOBSERVED_DASH });
      } else {
        const tMean = (p?: PipeState) =>
          p && p.t_from_c != null && p.t_to_c != null
            ? (p.t_from_c + p.t_to_c) / 2 : null;
        const vMax = Math.max(Math.abs(s?.v_m_per_s ?? 0), Math.abs(r?.v_m_per_s ?? 0));
        const color = layer === "supply" ? supplyTempColor(tMean(s), tFlowDesign)
          : layer === "return" ? returnTempColor(tMean(r))
          : layer === "velocity" ? velocityColor(vMax)
          : "#64748b"; // Δp layer: trenches recede, consumer markers carry it
        const weight = layer === "dp" ? 2
          : mdotWidth(Math.abs(s?.mdot_kg_per_s ?? r?.mdot_kg_per_s ?? 0), maxMdot);
        pl.setStyle({ color, weight, opacity: layer === "dp" ? 0.55 : 0.95,
                      dashArray: undefined });
      }
      if (pl.isPopupOpen()) {
        const tr = topo.trenches.find((x) => x.id === id);
        if (tr) pl.setPopupContent(trenchPopup(tr));
      }
    }

    for (const [id, cm] of consRef.current) {
      const c = f ? consumerLive(id) : undefined;
      if (!c) {
        cm.setStyle({ fillColor: UNOBSERVED, fillOpacity: 0.7 });
      } else {
        const fill = layer === "supply" ? supplyTempColor(c.t_supply_c, tFlowDesign)
          : layer === "return" ? returnTempColor(c.t_return_c)
          : layer === "dp" ? dpColor(c.dp_bar)
          : "#94a3b8"; // velocity is a pipe property — consumers stay neutral
        cm.setStyle({ fillColor: fill, fillOpacity: 0.95 });
      }
      if (cm.isPopupOpen()) {
        const cons = topo.consumers.find((x) => x.id === id);
        if (cons) cm.setPopupContent(consumerPopup(cons));
      }
    }

    if (plantRef.current?.isPopupOpen()) {
      plantRef.current.setPopupContent(plantPopup());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest, layer, observedOnly, tFlowDesign, topo]);

  // ---- colorbar legend per layer ----------------------------------------------

  const legend = layer === "supply" ? {
    gradient: SUPPLY_GRADIENT,
    top: `${fmt(tFlowDesign, 0)} °C`,
    bottom: `${T_SUPPLY_LOW} °C`,
    caption: t("map.cbSupply"),
  } : layer === "return" ? {
    gradient: RETURN_GRADIENT,
    top: `${T_RETURN_HIGH} °C`,
    bottom: `${T_RETURN_LOW} °C`,
    caption: t("map.cbReturn"),
  } : layer === "velocity" ? {
    gradient: VELOCITY_GRADIENT,
    top: `${V_MAX} m/s`,
    bottom: "0",
    caption: t("map.cbVelocity"),
  } : null;

  return (
    <div className="map-wrap">
      <div ref={elRef} className="map-canvas" />
      <button className="map-basemap" onClick={() => setLight((v) => !v)}>
        {light ? t("map.dark") : t("map.light")}
      </button>
      <div className="map-layers">
        {LAYERS.map((l) => (
          <button key={l} className={layer === l ? "on" : ""}
                  title={t(`layer.${l}Title`)} onClick={() => onLayer(l)}>
            {t(`layer.${l}`)}
          </button>
        ))}
      </div>
      <div className="map-colorbars">
        {legend ? (
          <Colorbar gradient={legend.gradient} top={legend.top}
                    bottom={legend.bottom} caption={legend.caption} />
        ) : (
          <div className="colorbar" style={{ alignItems: "flex-start", gap: 5 }}>
            {[
              [dpColor(DP_MIN_BAR - 0.01), `< ${DP_MIN_BAR} bar`],
              [dpColor(DP_MIN_BAR), `≈ ${DP_MIN_BAR} bar`],
              [dpColor(DP_MIN_BAR + 1), `> ${(DP_MIN_BAR + 0.15).toFixed(2)} bar`],
            ].map(([c, label]) => (
              <span key={label} style={{ display: "inline-flex", alignItems: "center",
                                          gap: 5, fontSize: "0.82rem", color: "#222" }}>
                <i className="swatch" style={{ background: c }} /> {label}
              </span>
            ))}
            <span className="cb-cap" style={{ marginTop: 2 }}>{t("map.cbDp")}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function Colorbar({ gradient, top, bottom, caption }: {
  gradient: string; top: string; bottom: string; caption: string;
}) {
  return (
    <div className="colorbar">
      <span className="cb-top">{top}</span>
      <div className="cb-ramp" style={{ background: gradient }} />
      <span className="cb-bot">{bottom}</span>
      <span className="cb-cap">{caption}</span>
    </div>
  );
}
