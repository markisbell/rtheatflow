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
    file: "Datei",
    view: "Ansicht",
    layerHdr: "Kartenebene",
    basemapHdr: "Karte",
    sightHdr: "Sicht",
    tabsHdr: "Bereich",
    tabLive: "Live",
    tabStudio: "NetzStudio",
    segTruth: "Realität",
    segObserved: "Gemessen",
    segEst: "Schätzung",
    sightTruth: "Reale Systemsicht (Simulation)",
    sightObserved: "Nur was die Wärmemengenzähler liefern",
    sightEst:
      "Berechnete Betreibersicht: Vorwärts-Beobachter aus Messwerten + " +
      "Erwartungsprofilen",
    help: "Hilfe",
    manual: "Benutzerhandbuch",
    apiDocs: "API-Dokumentation (Swagger)",
    source: "Quellcode auf GitHub",
  },

  file: {
    save: "Szenario speichern…",
    savePrompt: "Name des Szenarios:",
    scenariosHdr: "Szenarien",
    none: "keine gespeicherten Szenarien",
    delete: "Szenario löschen",
  },

  rec: {
    record: "Aufzeichnung starten",
    recordStop: "Aufzeichnung beenden",
    steps: "Schritte",
    recChip: "REC {{n}}",
    recordings: "Aufzeichnungen",
    none: "keine gespeicherten Aufzeichnungen",
    delete: "Aufzeichnung löschen",
    exportDaysDots: "Tage exportieren…",
    exportTitle: "Tage exportieren",
    exportRunning: "Export läuft",
    expChip: "Export {{pct}} %",
    days: "Anzahl Tage",
    exportHint:
      "Simuliert ganze Tage des aktuellen Aufbaus so schnell wie möglich " +
      "offline und legt sie als Aufzeichnung (CSV-Paket) ab — quasistatisch " +
      "wie der Live-Betrieb.",
    exportStart: "Export starten",
    cancel: "Abbrechen",
    error: "Fehler",
  },

  menu: {
    placeHdr: "Hier platzieren",
    addHx: "Einspeiser (20 kW)",
    addPump: "Netzpumpe (0,1 kg/s · 75 °C)",
    addStorage: "Pufferspeicher (100 kWh / 50 kW)",
    addConsumer: "Abnehmer",
    addBypass: "Bypass (Netzschluss)",
    consumerHdr: "Gebäudetyp",
    constConsumer: "Konstant 20 kW (Lehrprofil)",
    back: "Zurück",
    pin: "Details anheften",
    removeConsumer: "Abnehmer entfernen",
    removeBypass: "Bypass entfernen",
    removeProducer: "Erzeuger entfernen",
    removeStorage: "Speicher entfernen",
    plantKind: "Erzeugerart",
    plantHdr: "Erzeugerart wählen",
    plantBoiler: "Kessel",
    plantChp: "BHKW (KWK)",
    plantHpAir: "Wärmepumpe (Luft)",
    plantHpGround: "Wärmepumpe (Erdreich)",
    storageCharge: "Speicher laden",
    storageDischarge: "Speicher entladen",
    storageIdle: "Speicher in Bereitschaft",
    placeMeter: "Wärmemengenzähler setzen",
    removeMeter: "Wärmemengenzähler entfernen",
    placeSensor: "T/p-Sensor setzen",
    removeSensor: "T/p-Sensor entfernen",
  },

  meas: {
    heading: "Messstellen",
    modeHdr: "Zähler-Modus",
    modeFull: "Live",
    modeStd: "Standard 15 min",
    modeFullTitle: "Jeder Messwert in jedem Simulationsschritt (Telemetrie)",
    modeStdTitle:
      "Lastgang-Zähler: 15-Minuten-Mittelwerte, leer bis das erste Fenster " +
      "schließt — ehrlicher Kaltstart",
    meters: "Wärmemengenzähler",
    sensors: "T/p-Sensoren",
    none: "keine Messstellen — das Netz ist für den Betreiber unsichtbar",
    presetHdr: "Vorlagen",
    preset_all_consumers: "Alle Abnehmer",
    preset_plant_only: "Nur Erzeuger",
    preset_key_points: "Schlüsselstellen",
    preset_clear: "Alles entfernen",
    preset_all_consumersTitle: "Zähler an jeder Übergabestation",
    preset_plant_onlyTitle: "Nur die Erzeuger-SCADA + T/p am Erzeugerknoten",
    preset_key_pointsTitle:
      "Erzeuger + Netzenden + Zähler am aktuell bekannten Schlechtpunkt",
    preset_clearTitle: "Alle Messgeräte entfernen (Blindflug)",
    stdHint:
      "Standard-Lastgang: Werte erscheinen erst mit dem ersten vollen " +
      "15-min-Fenster und bleiben Fenstermittel.",
    placeHint:
      "Einzelne Zähler/Sensoren: Rechtsklick auf Abnehmer oder Knoten in " +
      "der Karte.",
    strict:
      "Strict Mode: der Server liefert nur die Messwerte — die Realität " +
      "bleibt verborgen.",
  },

  wp: {
    heading: "Schlechtpunkt (Δp-Regelung)",
    mode: "Pumpenmodus",
    controlled: "Geregelt",
    fixed: "Ungeregelt",
    controlledTitle: "Schlechtpunktregelung: Pumpe fährt den Δp-Sollwert an",
    fixedTitle: "Feste Förderhöhe — die Lehr-Referenz",
    setpoint: "Δp-Sollwert {{bar}} bar",
    plift: "Förderhöhe {{bar}} bar",
    yTitle: "Δp / bar",
    observed: "Schlechtpunkt (gemessen)",
    blind: "blind — kein Messwert",
    blindSpot:
      "Blinder Fleck: der wahre Schlechtpunkt hat keinen Zähler — geregelt " +
      "wird auf den schlechtesten GEMESSENEN Wert.",
    blindSpotNoMeter:
      "Blind: kein Δp-Messwert — die Pumpe hält ihre Förderhöhe.",
    pliftNow: "Förderhöhe aktuell",
    pumpEl: "Pumpe P el",
    hintControlled:
      "Ein Schritt pro Takt, begrenzt — die Pumpe nähert sich sichtbar über " +
      "mehrere Takte. Überhöhte Sollwerte kosten messbar Pumpstrom.",
    hintFixed:
      "Feste Förderhöhe: bei Schwachlast steigt der Differenzdruck — und " +
      "die Pumpenergie — unnötig an.",
  },

  hc: {
    heading: "Heizkurve",
    preset3g: "3. Generation (110/70)",
    preset4g: "4. Generation (70/65)",
    preset3gTitle: "Klassisches Hochtemperaturnetz",
    preset4gTitle: "Niedertemperaturnetz — Verluste sinken, Massenstrom steigt",
    t_flow_design_c: "Vorlauf Auslegung °C",
    t_flow_min_c: "Vorlauf minimal °C",
    t_amb_design_c: "Auslegungs-Außentemp. °C",
    t_room_c: "Raumtemperatur °C",
    n: "Krümmung n",
    setpoint: "Kurven-Soll aktuell",
    actual: "Vorlauf Ist",
    noCurve: "Keine Heizkurve aktiv (fester Vorlauf) — Preset wählen, um eine zu installieren.",
  },

  wx: {
    heading: "Wetter",
    knob: "Außentemperatur {{t}} °C",
    badge: "Override",
    release: "Override lösen — zurück zum Profil",
    ground: "Bodentemperatur",
    hint:
      "Der Regler überschreibt das Wetterprofil live: die Heizlast folgt " +
      "sofort über den Gradstunden-Faktor, Warmwasser bleibt unberührt.",
  },

  pin: {
    q: "Wärmeleistung",
    mdot: "Massenstrom",
    tSupply: "Vorlauf",
    tReturn: "Rücklauf",
    tFlow: "Vorlauf",
    plift: "Förderhöhe",
    pumpEl: "Pumpe P el",
    plantKind: "Erzeugerart",
    cop: "COP",
    tCold: "Quelle",
    pEl: "P el (Aufnahme)",
    pElChp: "P el (Erzeugung)",
    pFuel: "Brennstoffleistung",
    dispatch: "Einspeisung",
    soc: "Ladestand",
    active: "Aktiver Zweig",
    power: "Leistung",
    unpin: "Lösen",
    noData: "noch keine Live-Daten",
  },

  plant: {
    boiler: "Kessel",
    chp: "BHKW",
    heat_pump: "Wärmepumpe",
  },

  storage: {
    idle: "Bereitschaft",
    charge: "Laden",
    discharge: "Entladen",
    set_idle: "Aus",
    set_charge: "Laden",
    set_discharge: "Entladen",
  },

  netz: {
    loading: "Lade Netzbibliothek…",
    failed: "Netzbibliothek konnte nicht geladen werden:",
    step1: "1 · Netz wählen",
    step2: "2 · Lasten konfigurieren",
    step3: "3 · Prüfen & starten",
    library: "Bibliothek",
    pickHint: "Links ein Netz wählen.",
    nodes: "{{count}} Knoten",
    ch_rural: "ländlich",
    ch_suburban: "Vorort",
    ch_urban: "städtisch",
    ch_abstract: "abstrakt",
    own: "Eigene Netze",
    import: "Netz importieren…",
    importTitle:
      "Fünf-Dateien-Kontrakt hochladen: network_structure, pipes, consumers, " +
      "producers, weather (fünf .json-Dateien oder eine Bündel-Datei)",
    imported: "„{{name}}“ importiert.",
    importErr: "Import fehlgeschlagen:",
    importNeedFive:
      "Es fehlen Kontrakt-Dateien — erwartet werden network_structure, " +
      "pipes, consumers, producers und weather",
    noCache: "Kein Archetypen-Cache — scripts/generate_profiles.py ausführen.",
    tempPreset: "Temperaturniveau",
    preset3g: "3G (hoch)",
    preset4g: "4G (niedrig)",
    preset3gTitle: "110/70-Heizkurve, hohe Rücklauftemperaturen",
    preset4gTitle: "70/65-Heizkurve, niedrige Rücklauftemperaturen",
    tempPresetHint: "Setzt Heizkurve UND Rücklaufverhalten der Gebäude.",
    day: "Betrachtungstag",
    dayDesign: "Auslegungstag (sehr kalt)",
    dayWinter: "Wintertag (kalt)",
    dayShoulder: "Übergangstag",
    daySummer: "Sommertag (nur Warmwasser)",
    archetypes: "Gebäudetypen ({{count}} gewählt)",
    scale: "Skalierung ×{{scale}}",
    seed: "Zufalls-Seed",
    jitter: "Gebäude entzerren (Jitter)",
    jitterHint: "±30 min Zeitversatz + Amplitudenstreuung je Gebäude.",
    kConsumers: "Abnehmer",
    kTrench: "Trassenlänge",
    kDesign: "Auslegungslast",
    kLhd: "Wärmebelegung MWh/(m·a)",
    lhdRule: "Faustregel Wirtschaftlichkeit: ≥ 1–1,5 MWh/(m·a)",
    lhdWarn: "Unter der Faustregel ≥ 1–1,5 MWh/(m·a) — hohe relative Verluste.",
    axDuration: "Jahresdauerlinie (sortiert)",
    axLoad: "Last / kW",
    chartDesc:
      "Spitze {{peak}} kW · Mittel {{mean}} kW · {{annual}} MWh/a. " +
      "Rote Linie: Auslegungslast.",
    applyRun: "Übernehmen & Live",
    applyPlain: "Ohne Lastgenerator übernehmen",
    applyPlainHint: "Netz mit seinen Originalprofilen laden.",
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
    node: "Netzknoten {{name}} — Rechtsklick: Ausrüstung platzieren",
    bypass: "Bypass {{name}}",
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
    coldStart: "Standard-Zähler: warte auf das erste 15-min-Fenster",
    windowed: "15-min-Mittelwerte (Standard-Lastgang)",
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
    estCaption: "Schätzung (Vorwärts-Beobachter)",
    estQuality: "Schätzgüte",
    estErrTr: "max |ΔT Rücklauf| an Messstellen",
    estErrMdot: "max |Δṁ| an Messstellen",
    estErrDp: "max |ΔΔp| an Messstellen",
    estAge: "Stand der Schätzung",
    estAgeNow: "aktuell (#{{seq}})",
    estAgeMin: "vor {{min}} min (#{{seq}})",
    estSolve: "Beobachter-Rechenzeit",
    estNote:
      "Zweites Netzmodell, angetrieben nur von Messwerten und " +
      "Erwartungsprofilen — unbemessene Abnehmer zeigen die Erwartung, " +
      "nicht die Realität.",
  },
};

const en: typeof de = {
  app: {
    consumersShort: "consumers",
    trenchKm: "{{km}} km trench",
    noNetwork: "no network",
  },

  mbar: {
    file: "File",
    view: "View",
    layerHdr: "Map layer",
    basemapHdr: "Basemap",
    sightHdr: "Perspective",
    tabsHdr: "Area",
    tabLive: "Live",
    tabStudio: "NetzStudio",
    segTruth: "Reality",
    segObserved: "Measured",
    segEst: "Estimated",
    sightTruth: "Ground-truth system view (simulation)",
    sightObserved: "Only what the heat meters deliver",
    sightEst:
      "Calculated operator view: forward observer from measurements + " +
      "expected profiles",
    help: "Help",
    manual: "User manual (German)",
    apiDocs: "API documentation (Swagger)",
    source: "Source on GitHub",
  },

  file: {
    save: "Save scenario…",
    savePrompt: "Scenario name:",
    scenariosHdr: "Scenarios",
    none: "no saved scenarios",
    delete: "Delete scenario",
  },

  rec: {
    record: "Start recording",
    recordStop: "Stop recording",
    steps: "steps",
    recChip: "REC {{n}}",
    recordings: "Recordings",
    none: "no stored recordings",
    delete: "Delete recording",
    exportDaysDots: "Export days…",
    exportTitle: "Export days",
    exportRunning: "Export running",
    expChip: "Export {{pct}} %",
    days: "Number of days",
    exportHint:
      "Simulates whole days of the current setup offline, as fast as " +
      "possible, and stores them as a recording (CSV pack) — quasi-static " +
      "like the live loop.",
    exportStart: "Start export",
    cancel: "Cancel",
    error: "Error",
  },

  menu: {
    placeHdr: "Place here",
    addHx: "Feed-in (20 kW)",
    addPump: "Network pump (0.1 kg/s · 75 °C)",
    addStorage: "Buffer storage (100 kWh / 50 kW)",
    addConsumer: "Consumer",
    addBypass: "Bypass (loop closure)",
    consumerHdr: "Building type",
    constConsumer: "Constant 20 kW (teaching profile)",
    back: "Back",
    pin: "Pin details",
    removeConsumer: "Remove consumer",
    removeBypass: "Remove bypass",
    removeProducer: "Remove producer",
    removeStorage: "Remove storage",
    plantKind: "Plant type",
    plantHdr: "Choose plant type",
    plantBoiler: "Boiler",
    plantChp: "CHP unit",
    plantHpAir: "Heat pump (air)",
    plantHpGround: "Heat pump (ground)",
    storageCharge: "Charge storage",
    storageDischarge: "Discharge storage",
    storageIdle: "Storage standby",
    placeMeter: "Install heat meter",
    removeMeter: "Remove heat meter",
    placeSensor: "Install T/p sensor",
    removeSensor: "Remove T/p sensor",
  },

  meas: {
    heading: "Measurement points",
    modeHdr: "Meter mode",
    modeFull: "Live",
    modeStd: "Standard 15 min",
    modeFullTitle: "Every reading at every simulation step (telemetry)",
    modeStdTitle:
      "Load-profile meters: 15-minute means, empty until the first window " +
      "closes — honest cold start",
    meters: "Heat meters",
    sensors: "T/p sensors",
    none: "no measurement points — the network is invisible to the operator",
    presetHdr: "Presets",
    preset_all_consumers: "All consumers",
    preset_plant_only: "Plant only",
    preset_key_points: "Key points",
    preset_clear: "Remove all",
    preset_all_consumersTitle: "A meter at every substation",
    preset_plant_onlyTitle: "Only the plant SCADA + T/p at the plant node",
    preset_key_pointsTitle:
      "Plant + network ends + a meter at the currently known worst point",
    preset_clearTitle: "Remove every device (flying blind)",
    stdHint:
      "Standard load profile: values appear only with the first complete " +
      "15-min window and stay window means.",
    placeHint:
      "Individual meters/sensors: right-click a consumer or node on the map.",
    strict:
      "Strict mode: the server serves only the measurements — reality stays " +
      "hidden.",
  },

  wp: {
    heading: "Worst point (Δp control)",
    mode: "Pump mode",
    controlled: "Controlled",
    fixed: "Fixed",
    controlledTitle: "Worst-point control: the pump chases the Δp setpoint",
    fixedTitle: "Fixed pump lift — the teaching reference",
    setpoint: "Δp setpoint {{bar}} bar",
    plift: "Pump lift {{bar}} bar",
    yTitle: "Δp / bar",
    observed: "Worst point (measured)",
    blind: "blind — no reading",
    blindSpot:
      "Blind spot: the TRUE worst point carries no meter — control acts on " +
      "the worst MEASURED value.",
    blindSpotNoMeter:
      "Blind: no Δp reading — the pump holds its lift.",
    pliftNow: "Pump lift now",
    pumpEl: "Pump P el",
    hintControlled:
      "One clamped step per tick — the pump visibly converges over several " +
      "ticks. Oversized setpoints cost measurable pump energy.",
    hintFixed:
      "Fixed lift: at low load the differential pressure — and the pump " +
      "energy — rise needlessly.",
  },

  hc: {
    heading: "Heating curve",
    preset3g: "3rd generation (110/70)",
    preset4g: "4th generation (70/65)",
    preset3gTitle: "Classic high-temperature network",
    preset4gTitle: "Low-temperature network — losses drop, mass flow rises",
    t_flow_design_c: "Design flow temp °C",
    t_flow_min_c: "Minimum flow temp °C",
    t_amb_design_c: "Design ambient temp °C",
    t_room_c: "Room temperature °C",
    n: "Curvature n",
    setpoint: "Curve setpoint now",
    actual: "Actual flow temp",
    noCurve: "No heating curve active (fixed flow temp) — pick a preset to install one.",
  },

  wx: {
    heading: "Weather",
    knob: "Ambient temperature {{t}} °C",
    badge: "Override",
    release: "Release override — back to the profile",
    ground: "Ground temperature",
    hint:
      "The knob overrides the weather profile live: space heating responds " +
      "instantly via the degree-hour factor, hot water stays untouched.",
  },

  pin: {
    q: "Heat flow",
    mdot: "Mass flow",
    tSupply: "Supply",
    tReturn: "Return",
    tFlow: "Flow temp",
    plift: "Pump lift",
    pumpEl: "Pump P el",
    plantKind: "Plant type",
    cop: "COP",
    tCold: "source",
    pEl: "P el (input)",
    pElChp: "P el (output)",
    pFuel: "Fuel input",
    dispatch: "Feed-in",
    soc: "State of charge",
    active: "Active branch",
    power: "Power",
    unpin: "Unpin",
    noData: "no live data yet",
  },

  plant: {
    boiler: "Boiler",
    chp: "CHP",
    heat_pump: "Heat pump",
  },

  storage: {
    idle: "Standby",
    charge: "Charging",
    discharge: "Discharging",
    set_idle: "Off",
    set_charge: "Charge",
    set_discharge: "Discharge",
  },

  netz: {
    loading: "Loading network library…",
    failed: "Failed to load the network library:",
    step1: "1 · Pick a network",
    step2: "2 · Configure loads",
    step3: "3 · Check & start",
    library: "Library",
    pickHint: "Pick a network on the left.",
    nodes: "{{count}} nodes",
    ch_rural: "rural",
    ch_suburban: "suburban",
    ch_urban: "urban",
    ch_abstract: "abstract",
    own: "Own networks",
    import: "Import network…",
    importTitle:
      "Upload the five-file contract: network_structure, pipes, consumers, " +
      "producers, weather (five .json files or one bundle file)",
    imported: "Imported “{{name}}”.",
    importErr: "Import failed:",
    importNeedFive:
      "Contract files missing — expected network_structure, pipes, " +
      "consumers, producers and weather",
    noCache: "No archetype cache — run scripts/generate_profiles.py.",
    tempPreset: "Temperature level",
    preset3g: "3G (high)",
    preset4g: "4G (low)",
    preset3gTitle: "110/70 heating curve, high return temperatures",
    preset4gTitle: "70/65 heating curve, low return temperatures",
    tempPresetHint: "Sets the heating curve AND the buildings' return behavior.",
    day: "Day under study",
    dayDesign: "Design day (very cold)",
    dayWinter: "Winter day (cold)",
    dayShoulder: "Shoulder-season day",
    daySummer: "Summer day (DHW only)",
    archetypes: "Building types ({{count}} selected)",
    scale: "Scaling ×{{scale}}",
    seed: "Random seed",
    jitter: "Desynchronize buildings (jitter)",
    jitterHint: "±30 min time shift + amplitude spread per building.",
    kConsumers: "Consumers",
    kTrench: "Trench length",
    kDesign: "Design load",
    kLhd: "Linear heat density MWh/(m·a)",
    lhdRule: "Viability rule of thumb: ≥ 1–1.5 MWh/(m·a)",
    lhdWarn: "Below the ≥ 1–1.5 MWh/(m·a) rule of thumb — high relative losses.",
    axDuration: "Load-duration curve (sorted)",
    axLoad: "Load / kW",
    chartDesc:
      "Peak {{peak}} kW · mean {{mean}} kW · {{annual}} MWh/a. " +
      "Red line: design load.",
    applyRun: "Apply & go live",
    applyPlain: "Apply without loadgen",
    applyPlainHint: "Load the network with its original profiles.",
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
    node: "Network node {{name}} — right-click: place equipment",
    bypass: "Bypass {{name}}",
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
    coldStart: "standard meter: waiting for the first 15-min window",
    windowed: "15-min means (standard load profile)",
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
    estCaption: "Estimate (forward observer)",
    estQuality: "Estimate quality",
    estErrTr: "max |ΔT return| at sensors",
    estErrMdot: "max |Δṁ| at sensors",
    estErrDp: "max |ΔΔp| at sensors",
    estAge: "Estimate age",
    estAgeNow: "current (#{{seq}})",
    estAgeMin: "{{min}} min ago (#{{seq}})",
    estSolve: "Observer solve time",
    estNote:
      "A second network model driven only by measurements and expected " +
      "profiles — unmetered consumers show the expectation, not reality.",
  },
};

i18n.use(initReactI18next).init({
  resources: { de: { translation: de }, en: { translation: en } },
  lng: "de",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
