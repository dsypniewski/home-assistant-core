"""Define tests for the PurpleAir config flow."""
from unittest.mock import AsyncMock, patch

from aiopurpleair.errors import InvalidApiKeyError, PurpleAirError
import pytest

from homeassistant import data_entry_flow
from homeassistant.components.purpleair import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.helpers import device_registry as dr


async def test_duplicate_error(hass, config_entry, setup_purpleair):
    """Test that the proper error is shown when adding a duplicate config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"api_key": "abcde12345"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    "check_api_key_mock,check_api_key_errors",
    [
        (AsyncMock(side_effect=Exception), {"base": "unknown"}),
        (AsyncMock(side_effect=InvalidApiKeyError), {"base": "invalid_api_key"}),
        (AsyncMock(side_effect=PurpleAirError), {"base": "unknown"}),
    ],
)
@pytest.mark.parametrize(
    "get_nearby_sensors_mock,get_nearby_sensors_errors",
    [
        (AsyncMock(return_value=[]), {"base": "no_sensors_near_coordinates"}),
        (AsyncMock(side_effect=Exception), {"base": "unknown"}),
        (AsyncMock(side_effect=PurpleAirError), {"base": "unknown"}),
    ],
)
async def test_create_entry_by_coordinates(
    hass,
    api,
    check_api_key_errors,
    check_api_key_mock,
    get_nearby_sensors_errors,
    get_nearby_sensors_mock,
    setup_purpleair,
):
    """Test creating an entry by entering a latitude/longitude (including errors)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    # Test errors that can arise when checking the API key:
    with patch.object(api, "async_check_api_key", check_api_key_mock):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"api_key": "abcde12345"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == check_api_key_errors

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"api_key": "abcde12345"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "by_coordinates"

    # Test errors that can arise when searching for nearby sensors:
    with patch.object(api.sensors, "async_get_nearby_sensors", get_nearby_sensors_mock):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "latitude": 51.5285582,
                "longitude": -0.2416796,
                "distance": 5,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == get_nearby_sensors_errors

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "latitude": 51.5285582,
            "longitude": -0.2416796,
            "distance": 5,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "choose_sensor"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "sensor_index": "123456",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "abcde"
    assert result["data"] == {
        "api_key": "abcde12345",
    }
    assert result["options"] == {
        "sensor_indices": [123456],
    }


@pytest.mark.parametrize(
    "check_api_key_mock,check_api_key_errors",
    [
        (AsyncMock(side_effect=Exception), {"base": "unknown"}),
        (AsyncMock(side_effect=InvalidApiKeyError), {"base": "invalid_api_key"}),
        (AsyncMock(side_effect=PurpleAirError), {"base": "unknown"}),
    ],
)
async def test_reauth(
    hass, api, check_api_key_errors, check_api_key_mock, config_entry, setup_purpleair
):
    """Test re-auth (including errors)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": config_entry.entry_id,
            "unique_id": config_entry.unique_id,
        },
        data={"api_key": "abcde12345"},
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Test errors that can arise when checking the API key:
    with patch.object(api, "async_check_api_key", check_api_key_mock):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"api_key": "new_api_key"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == check_api_key_errors

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"api_key": "new_api_key"},
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert len(hass.config_entries.async_entries()) == 1


@pytest.mark.parametrize(
    "get_nearby_sensors_mock,get_nearby_sensors_errors",
    [
        (AsyncMock(return_value=[]), {"base": "no_sensors_near_coordinates"}),
        (AsyncMock(side_effect=Exception), {"base": "unknown"}),
        (AsyncMock(side_effect=PurpleAirError), {"base": "unknown"}),
    ],
)
async def test_options_add_sensor(
    hass,
    api,
    config_entry,
    get_nearby_sensors_errors,
    get_nearby_sensors_mock,
    setup_purpleair,
):
    """Test adding a sensor via the options flow (including errors)."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "add_sensor"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "add_sensor"

    # Test errors that can arise when searching for nearby sensors:
    with patch.object(api.sensors, "async_get_nearby_sensors", get_nearby_sensors_mock):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "latitude": 51.5285582,
                "longitude": -0.2416796,
                "distance": 5,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "add_sensor"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "latitude": 51.5285582,
            "longitude": -0.2416796,
            "distance": 5,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "choose_sensor"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "sensor_index": "567890",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "last_update_sensor_add": True,
        "sensor_indices": [123456, 567890],
    }

    assert config_entry.options["sensor_indices"] == [123456, 567890]


async def test_options_add_sensor_duplicate(hass, config_entry, setup_purpleair):
    """Test adding a duplicate sensor via the options flow."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "add_sensor"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "add_sensor"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "latitude": 51.5285582,
            "longitude": -0.2416796,
            "distance": 5,
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "choose_sensor"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "sensor_index": "123456",
        },
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_remove_sensor(hass, config_entry, setup_purpleair):
    """Test removing a sensor via the options flow."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "remove_sensor"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "remove_sensor"

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_device({(DOMAIN, "123456")})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"sensor_device_id": device_entry.id},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "last_update_sensor_add": False,
        "sensor_indices": [],
    }

    assert config_entry.options["sensor_indices"] == []
