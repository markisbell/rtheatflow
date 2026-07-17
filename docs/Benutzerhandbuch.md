# rtheatflow — Benutzerhandbuch

rtheatflow ist eine Lehr- und Forschungsplattform, die ein Fernwärmenetz in
**beschleunigter Echtzeit** simuliert: ein Simulationsschritt entspricht
einer Minute Netzzeit. Auf einer Karte entwickeln sich Vorlauf- und
Rücklauftemperaturen, Massenströme, Drücke und Wärmeverluste; Ausrüstung
lässt sich im laufenden Betrieb platzieren. Die Kernidee sind **drei
Sichten** auf dasselbe Netz: was physikalisch passiert, was der Betreiber
messen kann — und was er aus seinen Messwerten berechnen kann.

> Dieses Handbuch wird unter `GET /manual` direkt aus dem Repository
> ausgeliefert (`docs/Benutzerhandbuch.md`; `?format=md` liefert die
> Markdown-Quelle).

## 1. Schnellstart

### Windows (Entwicklung, ein Klick)

```
start_rtheatflow.bat
```

Der Launcher startet Backend (Port 8001) und Vite-UI (Port 5174) in eigenen
Konsolen, wartet auf `/health` (der allererste Rechenschritt kompiliert
numba vor — bis ~60 s) und öffnet den Browser. **Beenden:**
`stop_rtheatflow.bat` schließt beide Server samt eventuell verwaister
Hintergrundprozesse. Die Ports 8001/5174 sind bewusst gewählt: netzsim/
rtpowerflow belegt 8000/5173 — beide Plattformen laufen so parallel auf
derselben Maschine. Einmalige Vorbereitung:

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

`ui\node_modules` wird beim ersten Start automatisch installiert.

### Docker Compose (Gesamtstack)

```
docker compose up --build
```

| Dienst | Adresse | Inhalt |
|---|---|---|
| ui | http://localhost:8081 | Die Karten-Oberfläche (nginx) |
| backend | http://localhost:8001 | REST + WebSocket, Swagger unter `/docs`, Monitor unter `/`, dieses Handbuch unter `/manual` |
| grafana | http://localhost:3001 | Vorkonfiguriertes Fernwärme-Dashboard (admin / admin) |
| influxdb | http://localhost:8087 | Zeitreihenspeicher (Entwicklungs-Zugangsdaten) |

(Host-Ports im Geschwister-Schema: der netzsim-Compose-Stack behält
8000/8080/8086/3000.)

## 2. Arbeitsablauf: Netz — Lasten — Live

1. **NetzStudio** (Reiter oben): links ein Netz aus dem Katalog wählen
   (z. B. „Demo-Dorf") oder ein eigenes Fünf-Dateien-Bundle importieren.
2. **Lastpolitik** (mittlere Spalte): Gebäudemix (EFH alt/saniert, MFH),
   Seed, Skalierung, Tag-Perzentil, DHW-Varianten und das
   Temperaturniveau (3G/4G). Die Vorschau rechts zeigt Auslegungslast,
   Jahresenergie, Trassenlänge und die **lineare Wärmedichte** — die
   Faustregel ≥ 1–1,5 MWh/(m·a) entscheidet über die Wirtschaftlichkeit.
3. **Anwenden** — der Live-Reiter öffnet sich mit dem neuen Netz; die Uhr
   läuft, jede Minute ein Rechenschritt.

Unten im Live-Reiter: Start/Pause, Tageszeit-Regler, Tag-Regler (bei
mehrtägigen Wetterhorizonten) und die Schrittdauer (0,1–1 s echte Zeit pro
Simulationsminute).

## 3. Die drei Sichten — und was sie lehren

Das Segment oben in der Mitte schaltet die Perspektive um:

| Sicht | Was sie zeigt | Was sie lehrt |
|---|---|---|
| 👁 **Realität** | Den vollständigen physikalischen Zustand jeder Leitung, jedes Knotens | Wie das Netz wirklich reagiert — die Referenz |
| 📟 **Gemessen** | Nur die Werte der platzierten Messgeräte; alles andere ist grau/gestrichelt („unbekannt ist unbekannt") | Wie wenig ein Betreiber wirklich sieht — und was Messlücken kosten |
| 🧮 **Schätzung** | Die *berechnete* Betreibersicht: ein zweites Netzmodell, angetrieben nur von Messwerten und Erwartungsprofilen | Was sich aus Messungen rekonstruieren lässt — und was prinzipiell nicht |

Wichtige Ehrlichkeitsregeln:

- In der Sicht **Gemessen** ist ein unbemessener Abnehmer grau — niemals
  in einer „gesunden" Farbe.
- In der Sicht **Schätzung** zeigt ein unbemessener Abnehmer die
  **Erwartung** (sein typisches Profil), nicht die Realität. Eine Störung
  an einem Abnehmer ohne Zähler ist in der Schätzung **unsichtbar** —
  genau das ist die Lektion.
- Im Strict-Modus (`RTHEATFLOW_EXPOSE_GROUND_TRUTH=false`) liefert der
  Server die Realitätssicht gar nicht aus; Gemessen und Schätzung bleiben
  verfügbar.

## 4. Die Karte

Menü **Ansicht** (oder der Umschalter auf der Karte) wählt die Farbebene:

- **Vorlauftemperatur** (Standard): warme Rampe, „ganz heiß" genau bei der
  Auslegungs-Vorlauftemperatur der aktiven Heizkurve.
- **Rücklauftemperatur**: kühle Rampe — hohe Rückläufe (schlechte
  Auskühlung, „Low-ΔT-Syndrom") fallen sofort auf.
- **Geschwindigkeit**: Warnfarbe ab 1,5 m/s, rot ab 3 m/s (Kapazität).
- **Differenzdruck**: Abnehmermarker; rot unter dem Mindest-Δp.

Ein Klick auf Trassen, Abnehmer oder Erzeuger öffnet ein Popup mit den
Livewerten (es aktualisiert sich bei geöffnetem Zustand). Strg-Klick heftet
ein Element als eigenen Abschnitt in der Seitenleiste an.

## 5. Ausrüstung platzieren

Rechtsklick auf ein Element oder einen Knoten öffnet das Kontextmenü:

- **Einspeiser** (Solarthermie/Abwärme als Wärmetauscher, 20 kW Vorgabe),
- **Netzpumpe** (Massenstrompumpe),
- **Pufferspeicher** (100 kWh / 50 kW; Laden/Entladen/Bereitschaft über
  das Marker-Menü, SoC-Balken im angehefteten Abschnitt),
- **Abnehmer** (Gebäudetyp aus dem Archetyp-Katalog oder konstantes
  Lehrprofil),
- **Bypass** (Netzschluss — hält Endstränge durchströmt),
- am Erzeuger: die **Erzeugerart** (Kessel, BHKW, Wärmepumpe Luft/Erdreich)
  mit Brennstoff-/Strom-Kennzahlen im Rahmen.

Alles wirkt sofort im laufenden Netz; ein einzelner nicht konvergierter
Schritt heilt sich im nächsten Takt selbst.

## 6. Messstellen & Abdeckung

Der Abschnitt **Messstellen** in der Seitenleiste verwaltet die Sicht
„Gemessen":

- **Wärmemengenzähler** an Übergabestationen (Rechtsklick auf einen
  Abnehmer): liefern Leistung, Massenstrom, Vor-/Rücklauftemperatur, Δp.
- **T/p-Sensoren** an Trassenknoten: Druck und Temperatur in Vor- und
  Rücklauf.
- Die **Erzeuger-SCADA** ist immer da — echte Heizwerke messen sich selbst.
- Vorlagen: *Alle Abnehmer* · *Nur Erzeuger* · *Schlüsselstellen*
  (Erzeuger + Netzenden + Zähler am aktuell bekannten Schlechtpunkt) ·
  *Alles entfernen* (Blindflug).
- **Zähler-Modus**: *Live* (jeder Schritt) oder *Standard 15 min*
  (Lastgang-Mittelwerte; bis zum ersten vollen Fenster zeigt ein neuer
  Zähler ehrlich **nichts**).

## 7. Heizkurve & Schlechtpunktregelung

**Heizkurve**: die Vorlauftemperatur des Erzeugers folgt der
Außentemperatur (gleitender Betrieb). Die Voreinstellungen **3G**
(110/60 °C) und **4G** (70/40 °C) machen die Temperaturabsenkungs-Geschichte
mit einem Klick sichtbar: niedrigere Netztemperatur senkt die Verluste etwa
proportional zu (T_Netz − T_Boden) — aber sinkt der Rücklauf nicht mit,
explodieren Massenstrom und Pumpenstrom (Low-ΔT-Syndrom).

**Schlechtpunktregelung** (Abschnitt „Schlechtpunkt"): die Netzpumpe regelt
den Differenzdruck am ungünstigsten Abnehmer auf den Sollwert — ein
begrenzter Schritt pro Takt, sichtbar über mehrere Minuten. Sie liest
**nur Messwerte**: ohne verwertbaren Δp-Messwert hält sie die Förderhöhe
(Blindflug); trägt der wahre Schlechtpunkt keinen Zähler, regelt sie den
besten *gemessenen* Punkt und die Oberfläche zeigt den **blinden Fleck**.
Der Schlechtpunkt wandert mit der Lastverteilung — mit der Vorlage
„Schlüsselstellen" lässt sich beobachten, wie die Flagge kommt und geht.
Ein überhöhter Sollwert kostet messbar Pumpenstrom; der Modus „ungeregelte
Pumpe" (feste Förderhöhe) zeigt den Unterschied.

## 8. Wetter-Regler

Der Abschnitt **Außentemperatur** zieht die Außentemperatur live (−30 bis
+45 °C). Nur die Raumheizlast skaliert (Gradstunden-Logik), Warmwasser
bleibt unberührt. Loslassen kehrt zum Wetterprofil zurück. Damit lassen
sich Kälteeinbruch und Sommerbetrieb im Zeitraffer durchspielen — im
Sommerbetrieb springt der *relative* Verlust sichtbar hoch, obwohl der
absolute kaum sinkt.

## 9. Die Schätzung im Detail

Die Sicht **Schätzung** ist ein *Vorwärts-Beobachter*: ein zweites
pandapipes-Netz, das pro Takt nur mit Betreiberwissen gefüttert wird —
SCADA-Vorlauftemperatur und Pumpen-Sollwert, den Messwerten der
platzierten Zähler (im Standard-Modus deren 15-min-Mittel) und für alle
**unbemessenen** Abnehmer einem **Erwartungsprofil** (deterministisches
Archetyp-Profil, wettergestützt gewählt; Warmwasser als Mittel über die
stochastischen Varianten). Die Übersicht zeigt dazu die **Schätzgüte**:
die Abweichung des Modells von den Messwerten an den Messstellen
(Innovation), das Alter der Schätzung und die Rechenzeit des Beobachters.

Damit lässt sich der Wert von Messstellen quantifizieren:

- Vorlage *Alle Abnehmer*: die Schätzung trifft die Realität praktisch
  exakt — volle Beobachtbarkeit.
- Vorlage *Alles entfernen*: die Schätzung **ist** das Erwartungsprofil;
  die Schätzgüte (z. B. Abweichung des Erzeuger-Massenstroms) zeigt
  ehrlich, wie weit die Erwartung daneben liegt.
- Dazwischen: mit jeder Messstelle sinkt die Innovation sichtbar.

Konfiguration über `GET/POST /estimation/config`: `enabled` (Standard an),
`prior_basis` (`archetype` oder das bewusst grobe `design` =
Auslegungslast × Gradstundenfaktor) und `throttle_factor` (der Beobachter
gönnt sich standardmäßig das Doppelte seiner eigenen Rechenzeit Pause).

## 10. Szenarien

Menü **Datei → Szenario speichern…** legt den kompletten Aufbau als
**Rezept** ab (Netz, Lastpolitik, platzierte Ausrüstung, Messstellen,
Regler, Wetter-Override, Uhrzeit) — als handeditierbares JSON unter
`data/scenarios/`. Laden spielt das Rezept deterministisch nach.
Mitgeliefert: „Demo-Dorf 3G Winter" und „Demo-Dorf 4G Vergleich" (gleicher
Seed — direkter Vergleich der Temperaturniveaus).

## 11. Aufzeichnung & Export

- **Datei → Aufzeichnung starten**: jeder veröffentlichte Schritt wandert
  in ein CSV-Paket (`data/recordings/<id>/` mit `metadata.json` als
  Reproduktions-Rezept). Beenden, dann als ZIP herunterladen.
- **Datei → Tage exportieren…**: simuliert ganze Tage des aktuellen
  Aufbaus offline so schnell wie möglich — das Paket ist byte-kompatibel
  zu einer Live-Aufzeichnung. Fortschritt und Abbruch im Datei-Menü.
- Im Strict-Modus enthalten auch die CSV-Pakete keine Realitätsdaten.
- Experimentell: `RTHEATFLOW_TRANSIENT=true` rechnet den **Export** (nur
  ihn) mit thermischer Trägheit des Wassers; bei Problemen fällt jeder
  Schritt automatisch auf quasistatisch zurück und das Paket wird in den
  Metadaten markiert (`transient_fallback`). Siehe Abschnitt 13.

## 12. Grafana

`docker compose up` startet InfluxDB, einen Kollektor (liest `/state` und
schreibt pro Simulationsschritt einen Punkt) und Grafana mit einem fertig
provisionierten Dashboard: Vor-/Rücklauf gegen Heizkurven-Soll,
Schlechtpunkt-Δp gegen Sollwert und Förderhöhe, Erzeuger-Dispatch,
Speicher-SoC, Verlustquote, Solverstatus und Rechenzeit.

## 13. Grenzen des Modells — bitte lesen

**Quasistatisch:** Der Live-Betrieb löst pro Minute einen
*eingeschwungenen* Zustand. Das bildet Betriebspunkte korrekt ab (Verluste,
Drücke, Massenströme), aber **nicht die Laufzeit von Temperaturfronten**:
Wird die Vorlauftemperatur am Erzeuger angehoben, sehen alle Abnehmer die
neue Temperatur im selben Schritt — im echten Netz bräuchte die Front bei
0,5–1,5 m/s Minuten bis Stunden. Übergänge zwischen Betriebspunkten sind
also idealisiert. Die Plattform täuscht hier nichts vor; der experimentelle
Transient-Modus (Abschnitt 11) modelliert im Offline-Export immerhin die
Trägheit der Wassersäule (nicht aber Rohrwand und Erdreich).

Weitere bewusste Vereinfachungen:

- **Keine Druckdynamik** — die Hydraulik ist immer stationär (bei
  Minutenauflösung angemessen).
- **Ideale Messgeräte** — Zähler messen exakt (im Standard-Modus als
  Fenstermittel); Messrauschen und Ausfälle sind nicht modelliert.
- **Skalare Erzeugermodelle** — Kessel/BHKW/Wärmepumpe sind
  Kennzahlmodelle (COP, Wirkungsgrad), keine Anlagensimulation.
- **Zweileiternetz mit einem Druckhalter** — genau eine druckhaltende
  Umwälzpumpe; weitere Erzeuger speisen als Wärmetauscher oder
  Massenstrompumpen ein.
- **Lehrbetrieb, keine Sicherheit** — der Strict-Modus ist Didaktik, keine
  Zugriffskontrolle; die API hat bewusst keine Authentifizierung
  (Standardbindung 127.0.0.1).

## 14. Wichtige Einstellungen

Alle Einstellungen als Umgebungsvariablen mit Präfix `RTHEATFLOW_`
(vollständig dokumentiert in `.env.example`):

| Variable | Standard | Wirkung |
|---|---|---|
| `RTHEATFLOW_DEFAULT_NETWORK` | `demo_dorf` | Netz beim Start |
| `RTHEATFLOW_STEP_INTERVAL_SECONDS` | `1.0` | Echte Sekunden pro Simulationsminute |
| `RTHEATFLOW_EXPOSE_GROUND_TRUTH` | `true` | `false` = Strict-Modus (Realität bleibt im Server) |
| `RTHEATFLOW_RECORD` | `false` | `true` = Daueraufzeichnung, ein Paket pro Konfiguration |
| `RTHEATFLOW_TRANSIENT` | `false` | `true` = Offline-Export mit thermischer Trägheit (experimentell) |
| `RTHEATFLOW_MIN_QEXT_W` | `500` | Untergrenze der Abnehmerlast (Null-Durchfluss ist singulär) |
| `RTHEATFLOW_SOLVER_ITER` | `100` | Basis-Iterationen der Solver-Kaskade |

---

*rtheatflow — MIT-Lizenz. Simulationskern: pandapipes 0.14.0 (Fraunhofer
IEE / Universität Kassel, BSD-3). Schwesterprojekt für Stromnetze:
[rtpowerflow](https://github.com/markisbell/rtpowerflow).*
