# lg-therma-mqtt

Standalone MQTT Publisher für **LG Therma V** Wärmepumpen mit **Home Assistant Auto-Discovery**.

Sammelt alle 30 Sekunden Daten von der LG Therma V Wärmepumpe über die LG ThinQ API und veröffentlicht sie via MQTT. Die Sensoren werden automatisch über MQTT Discovery erkannt und als eigenständiges Gerät mit 27 Sensoren in Home Assistant hinzugefügt.

## Quick Start

### 1. Konfiguration erstellen

```bash
cp .env.example .env
nano .env
```

Trage deine Daten ein:
```env
LG_EMAIL=deine-email@example.com
LG_PASSWORD=dein-passwort
DEVICE_ID=deine-device-id

MQTT_BROKER=192.168.1.10
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
```

### 2. Container starten

```bash
docker compose build
docker compose up -d
```

### 3. Logs prüfen

```bash
docker compose logs -f
```

Du solltest sehen:
```
Publishing Home Assistant Auto-Discovery messages...
Published 27 sensor discovery messages
Published state: Power=2608W, Heat=7403W, COP=2.84
```

### 4. Home Assistant prüfen

Nach wenigen Sekunden erscheint in Home Assistant unter:
- **Einstellungen → Geräte & Dienste → Integrationen → MQTT**
- Ein neues Gerät: **"LG Therma V R290"**
- Mit 27 Sensoren

## Verfügbare Sensoren

### System Status (4 Sensoren)
| Sensor | Beschreibung |
|--------|-------------|
| Status | Online/Offline |
| Operation | EIN/AUS |
| Operation Mode | AUTO/HEAT/COOL/etc. |
| Diagnostic Code | Fehlercodes |

### Hauptheizkreis (4 Sensoren)
| Sensor | Beschreibung |
|--------|-------------|
| Main Current Temperature | Aktuelle Raumtemperatur (°C) |
| Main Target Temperature | Zieltemperatur (°C) |
| Main In Water Temperature | Vorlauftemperatur Eingang (°C) |
| Main Out Water Temperature | Vorlauftemperatur Ausgang (°C) |

### Zweiter Heizkreis (5 Sensoren)
| Sensor | Beschreibung |
|--------|-------------|
| Second Circuit Active | ON/OFF |
| Second Circuit Operation | EIN/AUS |
| Second Current Temperature | Aktuelle Raumtemperatur (°C) |
| Second Target Temperature | Zieltemperatur (°C) |
| Second Out Water Temperature | Vorlauftemperatur (°C) |

### Warmwasser (5 Sensoren)
| Sensor | Beschreibung |
|--------|-------------|
| Hot Water Active | ON/OFF |
| Hot Water Current Temperature | Aktuelle Temperatur (°C) |
| Hot Water Target Temperature | Zieltemperatur (°C) |
| Hot Water Eco Mode | ON/OFF |
| Hot Water Power Mode | ON/OFF |

### Energie (3 Sensoren)
| Sensor | Beschreibung |
|--------|-------------|
| Power Consumption | Stromverbrauch (W) |
| Heat Output | Heizleistung (W) |
| COP | Leistungszahl (Coefficient of Performance) |

### Funktionen (3 Sensoren)
| Sensor | Beschreibung |
|--------|-------------|
| Silent Mode | Leiser Modus ON/OFF |
| Temperature Switch | Temperaturumschaltung |
| Water Control | Wassersteuerung |

## MQTT Topics

### Discovery Topics
```
homeassistant/sensor/<device_id>/<sensor_unique_id>/config
```

### State Topic
```
homeassistant/sensor/<device_id>/state
```

Payload (JSON):
```json
{
  "status": "Online",
  "operation": "EIN",
  "operation_mode": "AUTO",
  "main_current_temp": 20.5,
  "main_target_temp": 17.0,
  "main_out_water_temp": 46.5,
  "power_consumption": 2608,
  "heat_output": 7403,
  "cop": 2.84
}
```

## Home Assistant Konfiguration

### Automatische Erkennung

Nach dem Start des Containers wird das Gerät automatisch in Home Assistant erkannt. Keine manuelle Konfiguration nötig!

### Lovelace Dashboard Beispiel

```yaml
type: entities
title: LG Therma V R290
entities:
  - entity: sensor.lg_therma_v_r290_status
  - entity: sensor.lg_therma_v_r290_operation
  - entity: sensor.lg_therma_v_r290_operation_mode
  - type: divider
  - entity: sensor.lg_therma_v_r290_main_current_temperature
  - entity: sensor.lg_therma_v_r290_main_target_temperature
  - entity: sensor.lg_therma_v_r290_main_out_water_temperature
  - type: divider
  - entity: sensor.lg_therma_v_r290_power_consumption
  - entity: sensor.lg_therma_v_r290_heat_output
  - entity: sensor.lg_therma_v_r290_cop_coefficient_of_performance
```

### Energie Dashboard

**Einstellungen → Dashboards → Energie → Individuelles Gerät hinzufügen**
- **Energieverbrauch**: `sensor.lg_therma_v_r290_power_consumption`
- **Wärmeerzeugung**: `sensor.lg_therma_v_r290_heat_output`

## Umgebungsvariablen

| Variable | Pflicht | Standard | Beschreibung |
|----------|---------|----------|-------------|
| `LG_EMAIL` | Ja | - | LG ThinQ Account E-Mail |
| `LG_PASSWORD` | Ja | - | LG ThinQ Account Passwort |
| `DEVICE_ID` | Ja | - | Geräte-ID der Wärmepumpe |
| `LG_COUNTRY` | Nein | `DE` | Ländercode |
| `LG_LANGUAGE` | Nein | `de-DE` | Sprachcode |
| `MQTT_BROKER` | Ja | `localhost` | MQTT Broker IP/Hostname |
| `MQTT_PORT` | Nein | `1883` | MQTT Broker Port |
| `MQTT_USERNAME` | Nein | - | MQTT Benutzername |
| `MQTT_PASSWORD` | Nein | - | MQTT Passwort |
| `MQTT_TOPIC_PREFIX` | Nein | `homeassistant` | MQTT Topic Prefix |
| `INTERVAL` | Nein | `30` | Abfrageintervall in Sekunden |

## Ohne Docker ausführen

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\Activate.ps1  # Windows

pip install -r requirements.txt

cp .env.example .env
# .env anpassen

python mqtt_publisher.py
```

## Proxmox LXC Container

Alternativ kann die Anwendung direkt in einem LXC Container laufen:

```bash
# Setup
git clone <repo-url> /opt/lg-therma-mqtt
cd /opt/lg-therma-mqtt
./setup.sh
```

## Architektur

```
lg-therma-mqtt/
├── mqtt_publisher.py      # Haupt-Applikation
├── wideq/                 # LG ThinQ API Client Library
│   ├── core_async.py      # Async API Client
│   ├── device.py          # Device Abstraction
│   ├── device_info.py     # Device Info Model
│   ├── devices/           # Gerätetyp-spezifische Module
│   └── ...
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── setup.sh
```

## Lizenz

Die `wideq/` Library basiert auf der [ha-smartthinq-sensors](https://github.com/ollo69/ha-smartthinq-sensors) Integration von ollo69.
