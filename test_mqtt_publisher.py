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

from mqtt_publisher import MQTTPublisher, _SKIP
from wideq.core_async import CoreAsync
from wideq.core_exceptions import APIError, OfficialApiNudgeError


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

    session_mock = MagicMock()
    session_mock.get_device_v2_settings = AsyncMock(return_value={"snapshot": SAMPLE_SNAPSHOT})

    publisher.lg_client = MagicMock()
    publisher.lg_client.session = session_mock
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

    session_mock = MagicMock()
    session_mock.get_device_v2_settings = AsyncMock(return_value={"snapshot": SAMPLE_SNAPSHOT})

    publisher.lg_client = MagicMock()
    publisher.lg_client.session = session_mock
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

    session_mock = MagicMock()
    session_mock.get_device_v2_settings = AsyncMock(return_value={"snapshot": snapshot})

    publisher.lg_client = MagicMock()
    publisher.lg_client.session = session_mock
    publisher.lg_client.get_device.return_value = device_info

    data = await publisher.collect_data()
    assert data["cop"] == 0.0


@pytest.mark.asyncio
async def test_collect_data_official_api_nudge_returns_skip(publisher):
    """Code 9006 ('use the official API') is transient -> skip, not an error."""
    session_mock = MagicMock()
    session_mock.get_device_v2_settings = AsyncMock(
        side_effect=OfficialApiNudgeError(
            "Please consider using the official API.", "9006"
        )
    )

    publisher.lg_client = MagicMock()
    publisher.lg_client.session = session_mock

    data = await publisher.collect_data()
    assert data is _SKIP


@pytest.mark.asyncio
async def test_collect_data_generic_api_error_returns_none(publisher):
    """An unknown/empty LG result code is a failed cycle -> None (not _SKIP)."""
    session_mock = MagicMock()
    session_mock.get_device_v2_settings = AsyncMock(
        side_effect=APIError("ThinQ APIv2 error", "")
    )

    publisher.lg_client = MagicMock()
    publisher.lg_client.session = session_mock

    data = await publisher.collect_data()
    assert data is None


# --- API error payload plumbing (diagnostics) ---

def test_manage_lge_result_attaches_payload_for_unknown_code():
    result = {"resultCode": "1234", "result": ""}
    with pytest.raises(APIError) as excinfo:
        CoreAsync._manage_lge_result(result, is_api_v2=True)
    assert excinfo.value.code == "1234"
    assert excinfo.value.payload == result


@pytest.mark.parametrize("code", ["9006", "9012"])
def test_manage_lge_result_maps_official_api_nudge_codes(code):
    """LG uses both 9006 and 9012 as 'use the official API' nudges."""
    result = {"resultCode": code, "result": "Please consider using the official API."}
    with pytest.raises(OfficialApiNudgeError) as excinfo:
        CoreAsync._manage_lge_result(result, is_api_v2=True)
    assert excinfo.value.code == code
    assert excinfo.value.payload == result


# --- Recovery backoff ---

def test_backoff_escalates_and_caps(publisher):
    publisher.interval = 30
    publisher.max_backoff = 300

    publisher.consecutive_failures = 1
    assert publisher._backoff_seconds() == 60
    publisher.consecutive_failures = 3
    assert publisher._backoff_seconds() == 240
    publisher.consecutive_failures = 10
    assert publisher._backoff_seconds() == 300  # capped at max_backoff


def test_backoff_exponent_is_bounded(publisher):
    """A very long outage must not blow up into a runaway integer."""
    publisher.interval = 30
    publisher.max_backoff = 600
    publisher.consecutive_failures = 100_000
    assert publisher._backoff_seconds() == 600


def test_recovery_defaults(publisher):
    assert publisher.max_backoff == 600
    assert publisher.exit_after == 4


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
