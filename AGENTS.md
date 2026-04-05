# AGENTS.md – lg-therma-mqtt

## Projektübersicht

Standalone Docker-Container, der alle 30s Daten von einer **LG Therma V R290 Wärmepumpe** über die LG ThinQ API abfragt und via **MQTT** mit **Home Assistant Auto-Discovery** veröffentlicht. 27 Sensoren (Temperaturen, Energie, COP, Betriebszustände).

## Herkunft & Entstehungsgeschichte

Dieses Projekt wurde aus dem Monorepo **pyThermaV** (`e:\Github\pyThermaV`) herausgelöst, in dem die MQTT-Komponente mit einer Home-Assistant-Custom-Integration (`ha-integration/smartthinq_sensors`) vermischt war.

### Chronologie der bisherigen Arbeit

1. **WideQ-Library adaptiert** – Die `wideq/` Library stammt aus dem Fork von [ollo69/ha-smartthinq-sensors](https://github.com/ollo69/ha-smartthinq-sensors). Sie wurde aus der HA-Integration extrahiert und eigenständig nutzbar gemacht (Login via `ClientAsync.from_user_login()`, Device-Abfrage, Snapshot-Abruf). Zusätzlich wurde die Library um Support für den **2. Heizkreis** und **ESS (Energy Storage System)** erweitert – siehe Abschnitt „WideQ-Library: Abweichungen vom Upstream".

2. **WideQ-HA-Client** – `wideq_ha_client.py` wurde als Wrapper um die Library geschrieben, mit Token-Persistenz, Device-Listing und Snapshot-Export. Dient als CLI-Tool und als Basis für das Dashboard.

3. **Dashboard** – `dashboard_wideq.py` zeigt live alle Sensordaten der Wärmepumpe im Terminal an (Heizkreise, Warmwasser, Energie, COP).

4. **MQTT Publisher** – `mqtt_publisher.py` wurde entwickelt, um alle Sensordaten periodisch via MQTT zu publizieren, inklusive HA Auto-Discovery-Messages. Läuft als Docker-Container.

5. **HA-Integration erweitert** – Parallel wurde die `smartthinq_sensors`-Integration in `ha-integration/` um 10 neue Sensoren für die Therma V erweitert (Heat Output, COP, Hot Water Target, Eco/Power Mode, Diagnostic Code, etc.). Änderungen in 3 Dateien: `wideq/const.py`, `wideq/devices/ac.py`, `sensor.py`.

6. **Projekttrennung** – Der MQTT-Teil wurde als eigenständiges Projekt `lg-therma-mqtt` herausgelöst, inklusive eigener Kopie der `wideq/`-Library, eigenem Docker-Setup und eigener Dokumentation.

### Was im pyThermaV-Repo verbleibt

- `ha-integration/` – Die erweiterte smartthinq_sensors HA-Custom-Integration
- `wideq_ha_client.py` – CLI-Client / Wrapper
- `dashboard_wideq.py` – Terminal-Dashboard
- `compare_sensors.py` – Sensor-Vergleichs-Tool
- `debug/` – Snapshots, State-Dumps, Config-Templates
- `api_responses/` – Gespeicherte API-Antworten
- Die MQTT-Dateien existieren dort noch (nicht gelöscht), können aber entfernt werden

## WideQ-Library: Abweichungen vom Upstream

Die `wideq/`-Library in diesem Repo ist **nicht identisch** mit dem Original von [ollo69/ha-smartthinq-sensors](https://github.com/ollo69/ha-smartthinq-sensors). Sie enthält eigene Erweiterungen, die als **PR [#916](https://github.com/ollo69/ha-smartthinq-sensors/pull/916)** upstream eingereicht, aber noch nicht gemergt wurden (Stand: April 2026).

### Geänderte Dateien gegenüber Upstream

| Datei | Änderungen |
|---|---|
| `wideq/const.py` | +6 `SECOND_CIRCUIT_*` Feature-Enums, +5 `ESS_*` Feature-Enums |
| `wideq/devices/ac.py` | +10 State-Keys (2nd Circuit + ESS), +4 Command-Keys, +3 Control-Methoden (`set_second_circuit_onoff`, `set_second_circuit_op_mode`, `set_second_circuit_target_temp`), +12 Status-Properties (2nd Circuit + ESS) |

### Neue Funktionalität

- **2. Heizkreis**: Ein/Aus, Betriebsmodus, Zieltemperatur, aktuelle Temperatur, Vorlauf-Ausgang, Min/Max-Temperaturen, Air/Water-Modus
- **ESS (Energy Storage System)**: Battery Remain, Battery Power, Solar Power, Grid Power, Consumed Power
- **Control-Methoden**: Zweiten Heizkreis ein/ausschalten, Modus setzen (HEAT/COOL/AUTO), Zieltemperatur setzen (mit Bereichsvalidierung)

### Upstream-Status

- **PR**: [ollo69/ha-smartthinq-sensors#916](https://github.com/ollo69/ha-smartthinq-sensors/pull/916) – „Heat Pump: Add support for 2nd heating circuit"
- **Status**: Open (eingereicht am 5. November 2025, keine Reaktion vom Maintainer)
- **Risiko**: Bei zukünftigen Updates von ollo69 können Merge-Konflikte in `const.py` und `devices/ac.py` auftreten

## Architektur

```
lg-therma-mqtt/
├── mqtt_publisher.py       # Hauptanwendung – MQTTPublisher-Klasse
│                           # - LG ThinQ Login (ClientAsync.from_user_login)
│                           # - MQTT Connect (paho-mqtt v2 API)
│                           # - HA Discovery Messages (27 Sensoren)
│                           # - Periodischer Daten-Poll + Publish
├── wideq/                  # LG ThinQ API Library (eigenständige Kopie)
│   ├── core_async.py       # Async HTTP Client, Auth, Token-Refresh
│   ├── device.py           # Device-Abstraction, State-Parsing
│   ├── device_info.py      # DeviceInfo-Model, Snapshot
│   ├── core_exceptions.py  # AuthenticationError, etc.
│   ├── const.py            # Enums, Feature-Definitionen
│   ├── factory.py          # Device-Factory
│   ├── model_info.py       # Model-Info-Parsing
│   ├── devices/            # Gerätetyp-spezifisch (ac.py = Wärmepumpe)
│   └── backports/          # Python-Backports
├── Dockerfile              # Python 3.12-slim, non-root user
├── docker-compose.yml      # Service mit .env, Logging, Resource Limits
├── requirements.txt        # aiohttp, xmltodict, paho-mqtt, python-dotenv
├── .env.example            # Vorlage für Credentials
├── setup.sh                # Setup für Proxmox LXC
└── README.md               # Vollständige Nutzerdokumentation
```

## Technische Details

### Abhängigkeiten
- `aiohttp` >= 3.13.0 – HTTP-Client für LG ThinQ API
- `xmltodict` >= 0.13.0 – XML-Parsing der API-Responses
- `paho-mqtt` >= 2.0.0 – MQTT Client (v2 Callback-API)
- `python-dotenv` >= 1.0.0 – .env Dateiunterstützung
- `charset-normalizer` >= 3.0.0 – Encoding-Erkennung

### Konfiguration
Ausschließlich über Umgebungsvariablen (`.env`):
- `LG_EMAIL`, `LG_PASSWORD`, `DEVICE_ID` – LG ThinQ Zugangsdaten
- `MQTT_BROKER`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD` – MQTT Broker
- `MQTT_TOPIC_PREFIX` (default: `homeassistant`) – Topic-Prefix
- `INTERVAL` (default: `30`) – Abfrageintervall in Sekunden
- `LG_COUNTRY` (default: `DE`), `LG_LANGUAGE` (default: `de-DE`)

### Datenfluss
1. `ClientAsync.from_user_login()` → LG ThinQ API Login
2. `client.refresh_devices()` → Geräteliste aktualisieren
3. `client.get_device(device_id).as_dict()['snapshot']` → Rohdaten
4. Mapping der `airState.*`-Keys auf Sensor-Werte
5. JSON-Payload auf MQTT State-Topic publizieren

### MQTT Topics
- Discovery: `{prefix}/sensor/{device_identifier}/{unique_id}/config` (retain=true)
- State: `{prefix}/sensor/{device_identifier}/state` (alle Werte als JSON)

### Bekannte Sensor-Keys (aus Snapshot)
```
airState.operation          – Betrieb EIN/AUS
airState.opMode             – Betriebsmodus
airState.diagCode           – Diagnosecode
airState.tempState.current  – Raumtemperatur Hauptkreis
airState.tempState.target   – Zieltemperatur Hauptkreis
airState.tempState.inWaterCurrent  – Vorlauf Eingang
airState.tempState.outWaterCurrent – Vorlauf Ausgang
airState.2ndCircuit.onOff   – 2. Heizkreis EIN/AUS
airState.2nd.operation      – 2. Heizkreis Betrieb
airState.2nd.tempState.*    – 2. Heizkreis Temperaturen
airState.miscFuncState.hotWater    – Warmwasser EIN/AUS
airState.miscFuncState.ecoHotWater – Eco-Modus
airState.miscFuncState.powerHotWater – Power-Modus
airState.tempState.hotWaterCurrent – Warmwasser Ist-Temperatur
airState.tempState.hotWaterTarget  – Warmwasser Soll-Temperatur
airState.energy.onCurrent   – Stromverbrauch (W)
airState.calorie.onCurrent  – Heizleistung (W)
airState.silentMode         – Silent Mode
airState.wUpSwitch.upSwitch – Temperature Switch
airState.wCtrl              – Water Control
```

## Offene Punkte / Nächste Schritte

- [x] Git-Repo initialisieren und auf GitHub pushen → [derolli1976/lg-therma-mqtt](https://github.com/derolli1976/lg-therma-mqtt)
- [x] Unit Tests erstellen (12 Tests: Init, Sensoren, Daten-Mapping, MQTT Publish)
- [ ] MQTT-Dateien aus pyThermaV-Repo entfernen (Cleanup)
- [ ] Ggf. Energy-Sensoren erweitern (ESS/Solar-Daten wenn vorhanden)
- [ ] Langzeit-Stabilität testen (Token-Refresh, Reconnect-Verhalten)
- [ ] Ggf. MQTT Command-Support (Temperatur setzen, Silent Mode schalten)
- [ ] Container auf Proxmox deployen und testen
- [ ] Upstream-PR #916 verfolgen – bei Merge die lokale wideq-Kopie synchronisieren

## Konventionen

- Python 3.12+
- Logging nach stdout (Docker) + optionales File-Logging
- Docker: non-root User (`pytherma`, UID 1000)
- Alle Konfiguration über Environment Variables, keine Config-Dateien
- Deutsche Kommentare/Docs, englische Code-Identifier
