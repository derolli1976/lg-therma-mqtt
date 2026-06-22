"""
LG Therma V MQTT Publisher für Home Assistant
Sammelt alle 30 Sekunden Daten von der LG Therma V Wärmepumpe
und veröffentlicht sie via MQTT mit Home Assistant Auto-Discovery.
"""

import asyncio
import hashlib
import os
import sys
import logging
import json
from datetime import datetime
from typing import Optional, Dict, Any
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import wideq
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wideq.core_async import ClientAsync
from wideq.core_exceptions import APIError, OfficialApiNudgeError
from wideq.device import Device
from wideq.devices.ac import ACMode
from wideq.model_info import ModelInfo

# Sentinel returned by collect_data when LG sent a transient "use the official
# API" nudge (code 9006). The cycle should be skipped without counting as an
# error or triggering a re-login.
_SKIP = object()

# Setup logging
handlers = [logging.StreamHandler(sys.stdout)]

# Add file handler if possible (Windows or writable log directory)
if os.name == 'nt':
    log_file = os.path.join(os.path.dirname(__file__), 'mqtt_publisher.log')
    handlers.append(logging.FileHandler(log_file))
else:
    # In Docker, try to write to /app/logs if writable
    log_dir = '/app/logs'
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, 'pytherma_mqtt.log')
        # Test if writable
        with open(log_file, 'a'):
            pass
        handlers.append(logging.FileHandler(log_file))
    except (PermissionError, OSError) as e:
        print(f"WARNING: Cannot write to {log_dir}: {e} - logging to stdout only", file=sys.stderr)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger(__name__)



class MQTTPublisher:
    """MQTT Publisher für LG Therma V mit Home Assistant Auto-Discovery"""
    
    def __init__(self):
        self.lg_client: Optional[ClientAsync] = None
        self.device: Optional[Device] = None
        self.model_info: Optional[ModelInfo] = None
        self.mqtt_client: Optional[mqtt.Client] = None
        self.running = True
        # Number of consecutive failed cycles (a nudge, a collection error or a
        # failed MQTT publish). Drives the escalating backoff and the re-login
        # cadence; reset to 0 after any fully successful cycle.
        self.consecutive_failures = 0
        self.discovery_sent = False
        self.connected = False
        
        # Environment variables
        self.lg_email = os.getenv('LG_EMAIL')
        self.lg_password = os.getenv('LG_PASSWORD')
        self.lg_country = os.getenv('LG_COUNTRY', 'DE')
        self.lg_language = os.getenv('LG_LANGUAGE', 'de-DE')
        self.device_id = os.getenv('DEVICE_ID')
        
        self.mqtt_broker = os.getenv('MQTT_BROKER', 'localhost')
        self.mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
        self.mqtt_username = os.getenv('MQTT_USERNAME', '')
        self.mqtt_password = os.getenv('MQTT_PASSWORD', '')
        self.mqtt_topic_prefix = os.getenv('MQTT_TOPIC_PREFIX', 'homeassistant')
        
        self.interval = int(os.getenv('INTERVAL', '30'))

        # Recovery tuning. When a cycle fails we back off with an escalating
        # delay (interval, 2x, 4x, ...) capped at MAX_BACKOFF, instead of
        # hammering at the normal interval. LG throttles aggressive polling on
        # the unofficial API; backing off *quietly* is what lets the rate
        # window recover.
        self.max_backoff = int(os.getenv('MAX_BACKOFF', '600'))
        # A fresh in-process login does not clear LG's account/IP throttle;
        # only a full process restart does (confirmed via logs). After this many
        # consecutive failures we exit so the container restart policy brings us
        # back fresh. 0 disables (keep backing off in place forever).
        self.exit_after = int(os.getenv('EXIT_AFTER_FAILURES', '4'))
        
        # Device info
        self.device_name = "LG Therma V R290"
        self.device_model = "AWHP_019101_WW"
        self.device_manufacturer = "LG Electronics"
        
        # Create unique device identifier from device_id (shortened)
        self.device_identifier = f"lg_therma_{self.device_id[:8]}" if self.device_id else "lg_therma_unknown"
        
        self._validate_environment()
    
    def _validate_environment(self):
        """Validate all required environment variables are set"""
        required_vars = {
            'LG_EMAIL': self.lg_email,
            'LG_PASSWORD': self.lg_password,
            'DEVICE_ID': self.device_id,
            'MQTT_BROKER': self.mqtt_broker
        }
        
        missing = [var for var, value in required_vars.items() if not value]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    async def initialize_lg(self):
        """Initialize LG ThinQ connection"""
        logger.info("Initializing LG ThinQ connection...")
        
        try:
            # Login to LG ThinQ (use keyword arguments)
            self.lg_client = await ClientAsync.from_user_login(
                username=self.lg_email,
                password=self.lg_password,
                country=self.lg_country,
                language=self.lg_language
            )
            logger.info("Successfully logged in to LG ThinQ")
            
            # Find device
            devices = self.lg_client.devices
            self.device = next((d for d in devices if d.device_id == self.device_id), None)
            
            if not self.device:
                available_ids = [d.device_id for d in devices]
                raise ValueError(
                    f"Device {self.device_id} not found. "
                    f"Available devices: {', '.join(available_ids)}"
                )
            
            logger.info(f"Found device: {self.device.name} ({self.device.device_id})")

            # Log the session identity so we can confirm, across reconnects,
            # whether LG's throttle follows the session or the account/IP. The
            # client_id is locally generated and rotates every login; the token
            # fingerprint reveals whether LG actually issues a *new* token on a
            # fresh login or hands back the same (possibly throttled) one.
            try:
                refresh_token = self.lg_client.auth.refresh_token or ""
                token_fp = hashlib.sha256(refresh_token.encode()).hexdigest()[:12]
                logger.info(
                    "Session identity: client_id=%s, user_number=%s, token_fp=%s",
                    self.lg_client.client_id,
                    self.lg_client.auth.user_number,
                    token_fp,
                )
            except Exception as e:
                logger.debug(f"Could not read session identity: {e}")
            
            # Update device name if available
            if self.device.name:
                self.device_name = self.device.name
            
            # Load model_info to translate enum codes (e.g. opMode 3 -> HEAT)
            try:
                model_data = await self.lg_client.model_url_info(
                    self.device.model_info_url
                )
                self.model_info = ModelInfo.get_model_info(model_data)
                if self.model_info is None:
                    logger.warning("Could not parse model_info; enum sensors may show raw values")
            except Exception as e:
                logger.warning(f"Failed to load model_info: {e}")
                self.model_info = None
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize LG ThinQ: {e}")
            raise
    
    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        """MQTT connection callback for API v2"""
        if reason_code == 0:
            logger.info(f"Connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            self.connected = True
            self.discovery_sent = False  # Re-send discovery on reconnect
        else:
            logger.error(f"MQTT connection failed with code {reason_code}")
            self.connected = False
    
    def on_mqtt_disconnect(self, client, userdata, flags, reason_code, properties):
        """MQTT disconnection callback for API v2"""
        logger.warning(f"Disconnected from MQTT broker (code {reason_code})")
        self.connected = False
    
    def initialize_mqtt(self):
        """Initialize MQTT connection"""
        logger.info(f"Connecting to MQTT broker {self.mqtt_broker}:{self.mqtt_port}...")
        
        try:
            self.mqtt_client = mqtt.Client(client_id=f"lg_therma_{self.device_id[:8]}", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
            
            # Set callbacks
            self.mqtt_client.on_connect = self.on_mqtt_connect
            self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
            
            # Set credentials if provided
            if self.mqtt_username:
                self.mqtt_client.username_pw_set(self.mqtt_username, self.mqtt_password)
            
            # Connect
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            
            # Wait for connection (sync wait)
            import time
            timeout = 10
            while not self.connected and timeout > 0:
                time.sleep(0.5)
                timeout -= 0.5
            
            if not self.connected:
                raise ConnectionError("Failed to connect to MQTT broker")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise
    
    def get_sensor_definitions(self) -> list:
        """
        Define all sensors for Home Assistant Auto-Discovery
        Returns list of sensor configurations
        """
        base_topic = f"{self.mqtt_topic_prefix}/sensor/{self.device_identifier}"
        state_topic = f"{base_topic}/state"
        
        device_info = {
            "identifiers": [self.device_identifier],
            "name": self.device_name,
            "model": self.device_model,
            "manufacturer": self.device_manufacturer,
            "sw_version": "1.0.0"
        }
        
        sensors = [
            # System Status
            {
                "name": "Status",
                "unique_id": f"{self.device_identifier}_status",
                "state_topic": state_topic,
                "value_template": "{{ value_json.status }}",
                "icon": "mdi:information-outline"
            },
            {
                "name": "Operation",
                "unique_id": f"{self.device_identifier}_operation",
                "state_topic": state_topic,
                "value_template": "{{ value_json.operation }}",
                "icon": "mdi:power"
            },
            {
                "name": "Operation Mode",
                "unique_id": f"{self.device_identifier}_operation_mode",
                "state_topic": state_topic,
                "value_template": "{{ value_json.operation_mode }}",
                "device_class": "enum",
                "options": ["COOL", "DRY", "FAN", "HEAT", "ACO", "AI", "AIRCLEAN", "AROMA", "ENERGY_SAVING", "ENERGY_SAVER", "UNKNOWN"],
                "icon": "mdi:cog"
            },
            {
                "name": "Diagnostic Code",
                "unique_id": f"{self.device_identifier}_diag_code",
                "state_topic": state_topic,
                "value_template": "{{ value_json.diag_code }}",
                "icon": "mdi:alert-circle"
            },
            
            # Main Heating Circuit
            {
                "name": "Main Current Temperature",
                "unique_id": f"{self.device_identifier}_main_current_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.main_current_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Main Target Temperature",
                "unique_id": f"{self.device_identifier}_main_target_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.main_target_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Main In Water Temperature",
                "unique_id": f"{self.device_identifier}_main_in_water_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.main_in_water_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Main Out Water Temperature",
                "unique_id": f"{self.device_identifier}_main_out_water_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.main_out_water_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            
            # Second Heating Circuit
            {
                "name": "Second Circuit Active",
                "unique_id": f"{self.device_identifier}_second_active",
                "state_topic": state_topic,
                "value_template": "{{ value_json.second_active }}",
                "icon": "mdi:radiator"
            },
            {
                "name": "Second Circuit Operation",
                "unique_id": f"{self.device_identifier}_second_operation",
                "state_topic": state_topic,
                "value_template": "{{ value_json.second_operation }}",
                "icon": "mdi:power"
            },
            {
                "name": "Second Current Temperature",
                "unique_id": f"{self.device_identifier}_second_current_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.second_current_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Second Target Temperature",
                "unique_id": f"{self.device_identifier}_second_target_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.second_target_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Second Out Water Temperature",
                "unique_id": f"{self.device_identifier}_second_out_water_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.second_out_water_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            
            # Hot Water
            {
                "name": "Hot Water Active",
                "unique_id": f"{self.device_identifier}_hotwater_active",
                "state_topic": state_topic,
                "value_template": "{{ value_json.hotwater_active }}",
                "icon": "mdi:water-boiler"
            },
            {
                "name": "Hot Water Current Temperature",
                "unique_id": f"{self.device_identifier}_hotwater_current_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.hotwater_current_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Hot Water Target Temperature",
                "unique_id": f"{self.device_identifier}_hotwater_target_temp",
                "state_topic": state_topic,
                "value_template": "{{ value_json.hotwater_target_temp }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement"
            },
            {
                "name": "Hot Water Eco Mode",
                "unique_id": f"{self.device_identifier}_hotwater_eco_mode",
                "state_topic": state_topic,
                "value_template": "{{ value_json.hotwater_eco_mode }}",
                "icon": "mdi:leaf"
            },
            {
                "name": "Hot Water Power Mode",
                "unique_id": f"{self.device_identifier}_hotwater_power_mode",
                "state_topic": state_topic,
                "value_template": "{{ value_json.hotwater_power_mode }}",
                "icon": "mdi:flash"
            },
            
            # Energy
            {
                "name": "Power Consumption",
                "unique_id": f"{self.device_identifier}_power_consumption",
                "state_topic": state_topic,
                "value_template": "{{ value_json.power_consumption }}",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement"
            },
            {
                "name": "Heat Output",
                "unique_id": f"{self.device_identifier}_heat_output",
                "state_topic": state_topic,
                "value_template": "{{ value_json.heat_output }}",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement"
            },
            {
                "name": "COP (Coefficient of Performance)",
                "unique_id": f"{self.device_identifier}_cop",
                "state_topic": state_topic,
                "value_template": "{{ value_json.cop }}",
                "icon": "mdi:gauge",
                "state_class": "measurement"
            },
            
            # Functions
            {
                "name": "Silent Mode",
                "unique_id": f"{self.device_identifier}_silent_mode",
                "state_topic": state_topic,
                "value_template": "{{ value_json.silent_mode }}",
                "icon": "mdi:volume-off"
            },
            {
                "name": "Temperature Switch",
                "unique_id": f"{self.device_identifier}_temp_switch",
                "state_topic": state_topic,
                "value_template": "{{ value_json.temp_switch }}",
                "icon": "mdi:thermometer"
            },
            {
                "name": "Water Control",
                "unique_id": f"{self.device_identifier}_water_control",
                "state_topic": state_topic,
                "value_template": "{{ value_json.water_control }}",
                "icon": "mdi:water-pump"
            }
        ]
        
        # Add device info to all sensors
        for sensor in sensors:
            sensor["device"] = device_info
        
        return sensors
    
    def publish_discovery(self):
        """Publish Home Assistant MQTT Discovery messages"""
        if not self.connected:
            logger.warning("Not connected to MQTT broker, skipping discovery")
            return False
        
        logger.info("Publishing Home Assistant Auto-Discovery messages...")
        
        sensors = self.get_sensor_definitions()
        
        for sensor in sensors:
            topic = f"{self.mqtt_topic_prefix}/sensor/{self.device_identifier}/{sensor['unique_id']}/config"
            payload = json.dumps(sensor)
            
            result = self.mqtt_client.publish(topic, payload, retain=True)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to publish discovery for {sensor['name']}")
                return False
        
        logger.info(f"Published {len(sensors)} sensor discovery messages")
        self.discovery_sent = True
        return True
    
    def _decode_op_mode(self, raw_value: Any) -> str:
        """Convert raw airState.opMode value to ACMode enum name (e.g. 'HEAT').

        The LG snapshot delivers opMode as a numeric code (e.g. 3.0). HA's
        enum sensor expects one of the configured option strings.
        """
        if raw_value is None or raw_value == "":
            return "UNKNOWN"
        # Already a friendly string?
        if isinstance(raw_value, str) and not raw_value.replace(".", "", 1).isdigit():
            return raw_value
        # Translate via model_info if available
        if self.model_info is not None:
            try:
                code = str(int(float(raw_value)))
                lang_key = self.model_info.enum_name("airState.opMode", code)
                if lang_key:
                    try:
                        return ACMode(lang_key).name
                    except ValueError:
                        pass
            except (ValueError, TypeError):
                pass
        logger.debug(f"Could not decode opMode value: {raw_value!r}")
        return "UNKNOWN"

    async def collect_data(self) -> Optional[Dict[str, Any]]:
        """Collect data from LG device via direct device query.
        
        Uses get_device_v2_settings() to fetch fresh data directly from
        the device, instead of relying on cached dashboard data which
        may be stale (the LG cloud only updates the dashboard cache
        infrequently unless triggered by an app or direct query).
        """
        try:
            # Query device directly for fresh data (like the ThinQ app does)
            result = await self.lg_client.session.get_device_v2_settings(self.device_id)
            snapshot = result.get('snapshot', {})
            
            if not snapshot:
                logger.warning("Direct device query returned empty snapshot, falling back to dashboard")
                await self.lg_client.refresh_devices()
                device_info = self.lg_client.get_device(self.device_id)
                if not device_info:
                    logger.error("Device not found")
                    return None
                snapshot = device_info.as_dict().get('snapshot', {})
            
            # Get online status from device list (dashboard data)
            device_info = self.lg_client.get_device(self.device_id)
            is_online = device_info.isonline if device_info else False
            
            # Extract all values from snapshot
            data = {
                # System
                "status": "Online" if is_online else "Offline",
                "operation": snapshot.get("airState.operation", "UNKNOWN"),
                "operation_mode": self._decode_op_mode(snapshot.get("airState.opMode")),
                "diag_code": snapshot.get("airState.diagCode", "0000"),
                
                # Main Circuit (corrected keys from dashboard)
                "main_current_temp": float(snapshot.get("airState.tempState.current", 0)),
                "main_target_temp": float(snapshot.get("airState.tempState.target", 0)),
                "main_in_water_temp": float(snapshot.get("airState.tempState.inWaterCurrent", 0)),
                "main_out_water_temp": float(snapshot.get("airState.tempState.outWaterCurrent", 0)),
                
                # Second Circuit (corrected keys from dashboard)
                "second_active": "ON" if snapshot.get("airState.2ndCircuit.onOff", 0.0) == 1.0 else "OFF",
                "second_operation": "ON" if snapshot.get("airState.2nd.operation", 0.0) == 1.0 else "OFF",
                "second_current_temp": float(snapshot.get("airState.2nd.tempState.current", 0)),
                "second_target_temp": float(snapshot.get("airState.2nd.tempState.target", 0)),
                "second_out_water_temp": float(snapshot.get("airState.2nd.tempState.outWaterCurrent", 0)),
                
                # Hot Water (corrected keys from dashboard)
                "hotwater_active": "ON" if snapshot.get("airState.miscFuncState.hotWater", 0.0) == 1.0 else "OFF",
                "hotwater_current_temp": float(snapshot.get("airState.tempState.hotWaterCurrent", 0)),
                "hotwater_target_temp": float(snapshot.get("airState.tempState.hotWaterTarget", 0)),
                "hotwater_eco_mode": "ON" if snapshot.get("airState.miscFuncState.ecoHotWater", 0.0) == 1.0 else "OFF",
                "hotwater_power_mode": "ON" if snapshot.get("airState.miscFuncState.powerHotWater", 0.0) == 1.0 else "OFF",
                
                # Energy
                "power_consumption": float(snapshot.get("airState.energy.onCurrent", 0)),
                "heat_output": float(snapshot.get("airState.calorie.onCurrent", 0)),
                "cop": 0.0,  # Will calculate below
                
                # Functions
                "silent_mode": "ON" if snapshot.get("airState.silentMode", "OFF") == "ON" else "OFF",
                "temp_switch": snapshot.get("airState.wUpSwitch.upSwitch", "OFF"),
                "water_control": snapshot.get("airState.wCtrl", "OFF")
            }
            
            # Calculate COP
            if data["power_consumption"] > 0 and data["heat_output"] > 0:
                data["cop"] = round(data["heat_output"] / data["power_consumption"], 2)
            
            return data

        except OfficialApiNudgeError as e:
            # LG intermittently returns code 9006 ("Please consider using the
            # official API") from the legacy ThinQ v2 endpoint. Treat it as a
            # failed cycle (the caller backs off); log the raw body so we can
            # see exactly what LG sends when it throttles us.
            logger.warning(f"LG nudged us toward the official API: {e}")
            if e.payload is not None:
                logger.warning(f"  9006 raw response: {e.payload}")
            return _SKIP
        except APIError as e:
            # Unknown / empty LG result codes surface here (e.g. the bare
            # "ThinQ APIv2 error"). Log the code and full payload -- without it
            # we are blind to what LG actually returns when polling degrades.
            logger.error(f"Failed to collect data (APIError code={e.code}): {e}")
            if e.payload is not None:
                logger.error(f"  raw response: {e.payload}")
            return None
        except Exception as e:
            logger.error(f"Failed to collect data: {e}")
            return None
    
    def publish_state(self, data: Dict[str, Any]):
        """Publish device state to MQTT"""
        if not self.connected:
            logger.warning("Not connected to MQTT broker")
            return False
        
        try:
            state_topic = f"{self.mqtt_topic_prefix}/sensor/{self.device_identifier}/state"
            payload = json.dumps(data)
            
            result = self.mqtt_client.publish(state_topic, payload, retain=False)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"Published state: Power={data['power_consumption']}W, Heat={data['heat_output']}W, COP={data['cop']}")
                return True
            else:
                logger.error("Failed to publish state")
                return False
                
        except Exception as e:
            logger.error(f"Failed to publish state: {e}")
            return False
    
    def _backoff_seconds(self) -> int:
        """Escalating backoff for failed cycles, capped at ``max_backoff``.

        Grows as interval * 2**failures. The exponent is bounded so a very long
        outage cannot produce a runaway integer; ``max_backoff`` caps the result
        well before that anyway.
        """
        exponent = min(self.consecutive_failures, 16)
        return min(self.interval * (2 ** exponent), self.max_backoff)

    async def run(self):
        """Main loop"""
        logger.info("Starting LG Therma V MQTT Publisher...")
        logger.info(f"Publishing interval: {self.interval} seconds")

        try:
            # Initialize LG connection
            await self.initialize_lg()

            # Initialize MQTT connection
            self.initialize_mqtt()

            # Send discovery messages
            self.publish_discovery()

            # Main loop
            while self.running:
                try:
                    # Re-send discovery if reconnected
                    if not self.discovery_sent and self.connected:
                        self.publish_discovery()

                    # Collect data and decide whether this cycle succeeded.
                    data = await self.collect_data()

                    if data is _SKIP:
                        fail_reason = "official-API nudge (code 9006)"
                    elif data is None:
                        fail_reason = "collection error"
                    elif self.publish_state(data):
                        fail_reason = None
                    else:
                        fail_reason = "MQTT publish failed"

                    if fail_reason is None:
                        # Success: resume the normal cadence.
                        self.consecutive_failures = 0
                        await asyncio.sleep(self.interval)
                        continue

                    # Failed cycle: back off quietly instead of hammering. A
                    # short burst of retries is exactly what keeps LG's rate
                    # window hot, so we never poll faster than the backoff.
                    self.consecutive_failures += 1
                    backoff = self._backoff_seconds()
                    logger.warning(
                        f"Cycle failed ({fail_reason}); backing off {backoff}s "
                        f"(consecutive failures: {self.consecutive_failures})"
                    )
                    await asyncio.sleep(backoff)

                    # A fresh in-process login does NOT clear LG's throttle:
                    # logs confirm the client_id rotates on every login yet the
                    # 9006 nudge persists across a re-login, so the throttle is
                    # keyed to the account/IP, not the session. The only
                    # recovery observed to work is a full process restart. So
                    # once we are clearly stuck, exit and let the container
                    # restart policy (restart: unless-stopped) bring us back
                    # fresh -- the backoff above is the quiet pause LG needs
                    # before the new start. Set EXIT_AFTER_FAILURES=0 to disable
                    # (e.g. when not running under a restart policy).
                    if self.exit_after and self.consecutive_failures >= self.exit_after:
                        logger.error(
                            f"Throttle persists after {self.consecutive_failures} "
                            "failed cycles; exiting for a clean restart "
                            "(requires a container restart policy)."
                        )
                        sys.exit(1)

                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                    self.consecutive_failures += 1
                    await asyncio.sleep(self._backoff_seconds())
            
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            raise
        finally:
            if self.mqtt_client:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            logger.info("Publisher stopped")


async def main():
    """Entry point"""
    publisher = MQTTPublisher()
    
    try:
        await publisher.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
        publisher.running = False
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
