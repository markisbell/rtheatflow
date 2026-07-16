import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// Bilingual UI — German is the authoring language (Vorlauf/Rücklauf,
// Schlechtpunkt, Heizkurve...), English is the fallback. Feature-prefixed
// keys, one inline resource file (blueprint convention).

const de = {
  app: {
    consumersShort: "Abnehmer",
    trenchKm: "{{km}} km Trasse",
    noNetwork: "kein Netz",
  },

  mbar: {
    view: "Ansicht",
    layerHdr: "Kartenebene",
    basemapHdr: "Karte",
    sightHdr: "Sicht",
    segTruth: "Realität",
    segObserved: "Gemessen",
    segEst: "Schätzung",
    sightTruth: "Reale Systemsicht (Simulation)",
    sightObserved: "Nur was die Wärmemengenzähler liefern",
    estM7: "Zustandsschätzung folgt in einem späteren Meilenstein",
    help: "Hilfe",
    apiDocs: "API-Dokumentation (Swagger)",
    source: "Quellcode auf GitHub",
  },

  layer: {
    supply: "Vorlauf",
    return: "Rücklauf",
    velocity: "Tempo",
    dp: "Δp",
    supplyTitle: "Vorlauftemperatur je Trasse",
    returnTitle: "Rücklauftemperatur je Trasse",
    velocityTitle: "Strömungsgeschwindigkeit (Kapazität)",
    dpTitle: "Differenzdruck am Abnehmer (Schlechtpunkt)",
  },

  map: {
    dark: "🌙 Dunkel",
    light: "☀ Hell",
    cbSupply: "Vorlauf °C",
    cbReturn: "Rücklauf °C",
    cbVelocity: "m/s",
    cbDp: "Δp bar",
  },

  tip: {
    trench: "Trasse {{from}} → {{to}}",
    consumer: "Abnehmer {{name}}",
    plant: "🏭 {{name}}",
  },

  pop: {
    trench: "Trasse {{from}} → {{to}}",
    supplyPipe: "Vorlauf",
    returnPipe: "Rücklauf",
    mdot: "Massenstrom",
    v: "Geschwindigkeit",
    loss: "Verlust",
    dp: "Δp",
    q: "Wärmeleistung",
    tSupply: "Vorlauf",
    tReturn: "Rücklauf",
    qFeed: "Einspeisung",
    pumpEl: "Pumpe P&#8202;el",
    length: "Länge",
    designLoad: "Auslegungslast",
    unobserved: "keine Messung — unbekannt",
    noData: "noch keine Live-Daten",
  },

  live: {
    loadingNet: "Lade Netz…",
    failedNet: "Netz konnte nicht geladen werden:",
    day: "Tag {{day}} · {{time}}",
    netInfo: "{{name}} · {{consumers}} Abnehmer · WS: {{ws}}",
    play: "▶ Start",
    pause: "⏸ Pause",
    time: "Uhrzeit {{time}}",
    stepDur: "Takt",
    stepDurTitle: "Wanduhr-Sekunden pro Simulationsminute",
    dayLabel: "Tag",
    dayTitle: "Simulationstag wählen",
    notConverged: "⚠ nicht konvergiert",
    selectHint:
      "Klick auf Trasse, Abnehmer oder Erzeuger öffnet die Live-Werte. " +
      "Kartenebene über Ansicht oder den Umschalter rechts oben.",
    truthHidden: "Der Server verbirgt die Realität (Strict Mode) — Anzeige gemessener Werte.",
  },

  ov: {
    heading: "Übersicht",
    groundTruth: "Reale Systemsicht",
    observedCaption: "Messwerte (Wärmemengenzähler)",
    feedIn: "Einspeisung",
    demand: "Abnahme",
    demandMetered: "Abnahme (gemessen)",
    losses: "Wärmeverluste",
    pumpEl: "Pumpe P el",
    plantFlow: "Vorlauf Erzeuger",
    plantReturn: "Rücklauf Erzeuger",
    curveSetpoint: "Heizkurven-Soll",
    worstPoint: "Schlechtpunkt Δp",
    solver: "Solver",
    solverOk: "ok",
    solverDegraded: "degradiert",
    solverFailed: "ausgefallen",
    solveTime: "Rechenzeit",
    weather: "Außentemperatur",
    override: " (Override)",
    coverage: "Messabdeckung",
    na: "n. v.",
    observedNote: "Aggregiert nur über bemessene Elemente — die Sicht des Betreibers.",
  },
};

const en: typeof de = {
  app: {
    consumersShort: "consumers",
    trenchKm: "{{km}} km trench",
    noNetwork: "no network",
  },

  mbar: {
    view: "View",
    layerHdr: "Map layer",
    basemapHdr: "Basemap",
    sightHdr: "Perspective",
    segTruth: "Reality",
    segObserved: "Measured",
    segEst: "Estimated",
    sightTruth: "Ground-truth system view (simulation)",
    sightObserved: "Only what the heat meters deliver",
    estM7: "State estimation ships in a later milestone",
    help: "Help",
    apiDocs: "API documentation (Swagger)",
    source: "Source on GitHub",
  },

  layer: {
    supply: "Supply",
    return: "Return",
    velocity: "Speed",
    dp: "Δp",
    supplyTitle: "Supply temperature per trench",
    returnTitle: "Return temperature per trench",
    velocityTitle: "Flow velocity (capacity)",
    dpTitle: "Differential pressure at consumers (worst point)",
  },

  map: {
    dark: "🌙 Dark",
    light: "☀ Light",
    cbSupply: "Supply °C",
    cbReturn: "Return °C",
    cbVelocity: "m/s",
    cbDp: "Δp bar",
  },

  tip: {
    trench: "Trench {{from}} → {{to}}",
    consumer: "Consumer {{name}}",
    plant: "🏭 {{name}}",
  },

  pop: {
    trench: "Trench {{from}} → {{to}}",
    supplyPipe: "Supply",
    returnPipe: "Return",
    mdot: "Mass flow",
    v: "Velocity",
    loss: "Loss",
    dp: "Δp",
    q: "Heat load",
    tSupply: "Supply",
    tReturn: "Return",
    qFeed: "Feed-in",
    pumpEl: "Pump P&#8202;el",
    length: "Length",
    designLoad: "Design load",
    unobserved: "no measurement — unknown",
    noData: "no live data yet",
  },

  live: {
    loadingNet: "Loading network…",
    failedNet: "Failed to load the network:",
    day: "Day {{day}} · {{time}}",
    netInfo: "{{name}} · {{consumers}} consumers · WS: {{ws}}",
    play: "▶ Start",
    pause: "⏸ Pause",
    time: "Time {{time}}",
    stepDur: "Tick",
    stepDurTitle: "Wall-clock seconds per simulated minute",
    dayLabel: "Day",
    dayTitle: "Pick the simulation day",
    notConverged: "⚠ not converged",
    selectHint:
      "Click a trench, consumer or producer for live values. " +
      "Pick the map layer via View or the switch at the top right.",
    truthHidden: "The server withholds ground truth (strict mode) — showing measured values.",
  },

  ov: {
    heading: "Overview",
    groundTruth: "Ground-truth system view",
    observedCaption: "Measured values (heat meters)",
    feedIn: "Feed-in",
    demand: "Demand",
    demandMetered: "Demand (metered)",
    losses: "Heat losses",
    pumpEl: "Pump P el",
    plantFlow: "Plant supply",
    plantReturn: "Plant return",
    curveSetpoint: "Heating-curve setpoint",
    worstPoint: "Worst-point Δp",
    solver: "Solver",
    solverOk: "ok",
    solverDegraded: "degraded",
    solverFailed: "failed",
    solveTime: "Solve time",
    weather: "Ambient temperature",
    override: " (override)",
    coverage: "Meter coverage",
    na: "n/a",
    observedNote: "Aggregated over metered elements only — the operator's view.",
  },
};

i18n.use(initReactI18next).init({
  resources: { de: { translation: de }, en: { translation: en } },
  lng: "de",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
