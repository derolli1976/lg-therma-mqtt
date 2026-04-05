"""Unit tests for LG Therma V MQTT Publisher"""

import os
import json
import pytest


from unittest.mock import AsyncMock, MagicMock, patch

# Set required env vars before importing
os.environ.update({
    "LG_EMAIL": "test@example.com",
    "LG_PASSWORD": "testpass",
    "DEVICE_ID": "abcdef1234567890",
    "MQTT_BROKER": "localhost",
})

from mqtt_publisher import MQTTPublisher


@pytest.fixture
def publisher():
    return MQTTPublisher()


# --- Environment & Init ---

def test_missing_env_raises():
    with patch.dict(os.environ, {"LG_EMAIL": ""}, clear=False):
        with pytest.raises(ValueError, match="LG_EMAIL"):
            MQTTPublisher()


def test_defaults(publisher):
    assert publisher.lg_country == "DE"
    assert publisher.lg_language == "de-DE"
    assert publisher.mqtt_port == 1883
    assert publisher.interval == 30
    assert publisher.mqtt_topic_prefix == "homeassistant"


def test_device_identifier(publisher):
    assert publisher.device_identifier == "lg_therma_abcdef12"


# --- Sensor Definitions ---

def test_sensor_count(publisher):
    sensors = publisher.get_sensor_definitions()
    assert len(sensors) == 24  # all defined sensors


def test_sensors_have_required_fields(publisher):
    for sensor in publisher.get_sensor_definitions():
        assert "name" in sensor
        assert "unique_id" in sensor
        assert "state_topic" in sensor
        assert "device" in sensor
        assert sensor["device"]["manufacturer"] == "LG Electronics"


def test_temperature_sensors_have_unit(publisher):
    temp_sensors = [
        s for s in publisher.get_sensor_definitions()
        if s.get("device_class") == "temperature"
    ]
    assert len(temp_sensors) > 0
    for s in temp_sensors:
        assert s["unit_of_measurement"] == "°C"


# --- Data Collection / Mapping ---

SAMPLE_SNAPSHOT = {
    "airState.operation": 1,
    "airState.opMode": 4,
    "airState.diagCode": "0000",
    "airState.tempState.current": 22.5,
    "airState.tempState.target": 24.0,
    "airState.tempState.inWaterCurrent": 30.0,
    "airState.tempState.outWaterCurrent": 35.0,
    "airState.2ndCircuit.onOff": 1.0,
    "airState.2nd.operation": 1.0,
    "airState.2nd.tempState.current": 20.0,
    "airState.2nd.tempState.target": 22.0,
    "airState.2nd.tempState.outWaterCurrent": 28.0,
    "airState.miscFuncState.hotWater": 1.0,
    "airState.tempState.hotWaterCurrent": 48.0,
    "airState.tempState.hotWaterTarget": 55.0,
    "airState.miscFuncState.ecoHotWater": 0.0,
    "airState.miscFuncState.powerHotWater": 1.0,
    "airState.energy.onCurrent": 1200,
    "airState.calorie.onCurrent": 4800,
    "airState.silentMode": "ON",
    "airState.wUpSwitch.upSwitch": "ON",
    "airState.wCtrl": "OFF",
}


@pytest.mark.asyncio
async def test_collect_data_mapping(publisher):
    device_info = MagicMock()
    device_info.isonline = True
    device_info.as_dict.return_value = {"snapshot": SAMPLE_SNAPSHOT}

    publisher.lg_client = MagicMock()
    publisher.lg_client.refresh_devices = AsyncMock()
    publisher.lg_client.get_device.return_value = device_info

    data = await publisher.collect_data()

    assert data["status"] == "Online"
    assert data["main_current_temp"] == 22.5
    assert data["main_target_temp"] == 24.0
    assert data["second_active"] == "ON"
    assert data["hotwater_active"] == "ON"
    assert data["hotwater_current_temp"] == 48.0
    assert data["hotwater_eco_mode"] == "OFF"
    assert data["hotwater_power_mode"] == "ON"
    assert data["silent_mode"] == "ON"


@pytest.mark.asyncio
async def test_cop_calculation(publisher):
    device_info = MagicMock()
    device_info.isonline = True
    device_info.as_dict.return_value = {"snapshot": SAMPLE_SNAPSHOT}

    publisher.lg_client = MagicMock()
    publisher.lg_client.refresh_devices = AsyncMock()
    publisher.lg_client.get_device.return_value = device_info

    data = await publisher.collect_data()

    assert data["cop"] == 4.0  # 4800 / 1200
    assert data["power_consumption"] == 1200
    assert data["heat_output"] == 4800


@pytest.mark.asyncio
async def test_cop_zero_when_no_power(publisher):
    snapshot = {**SAMPLE_SNAPSHOT, "airState.energy.onCurrent": 0}
    device_info = MagicMock()
    device_info.isonline = True
    device_info.as_dict.return_value = {"snapshot": snapshot}

    publisher.lg_client = MagicMock()
    publisher.lg_client.refresh_devices = AsyncMock()
    publisher.lg_client.get_device.return_value = device_info

    data = await publisher.collect_data()
    assert data["cop"] == 0.0


# --- MQTT Publish ---

def test_publish_state_not_connected(publisher):
    publisher.connected = False
    assert publisher.publish_state({"test": 1}) is False


def test_publish_state_success(publisher):
    publisher.connected = True
    mock_result = MagicMock()
    mock_result.rc = 0  # MQTT_ERR_SUCCESS
    publisher.mqtt_client = MagicMock()
    publisher.mqtt_client.publish.return_value = mock_result

    data = {"power_consumption": 100, "heat_output": 400, "cop": 4.0}
    assert publisher.publish_state(data) is True

    call_args = publisher.mqtt_client.publish.call_args
    assert "state" in call_args[0][0]
    assert json.loads(call_args[0][1]) == data


def test_publish_discovery_not_connected(publisher):
    publisher.connected = False
    assert publisher.publish_discovery() is False
