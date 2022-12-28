"""Config flow for Switchbot."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

import boto3
import requests
from switchbot import (
    SwitchBotAdvertisement,
    SwitchbotLock,
    SwitchbotModel,
    parse_advertisement_data,
)
import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_PASSWORD,
    CONF_SENSOR_TYPE,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult

from .const import (
    CONF_ENCRYPTION_KEY,
    CONF_KEY_ID,
    CONF_RETRY_COUNT,
    CONNECTABLE_SUPPORTED_MODEL_TYPES,
    DEFAULT_RETRY_COUNT,
    DOMAIN,
    NON_CONNECTABLE_SUPPORTED_MODEL_TYPES,
    SUPPORTED_MODEL_TYPES,
)

_LOGGER = logging.getLogger(__name__)

SWITCHBOT_INTERNAL_API_BASE_URL = (
    "https://l9ren7efdj.execute-api.us-east-1.amazonaws.com"
)
SWITCHBOT_COGNITO_POOL = {
    "PoolId": "us-east-1_x1fixo5LC",
    "AppClientId": "66r90hdllaj4nnlne4qna0muls",
    "AppClientSecret": "1v3v7vfjsiggiupkeuqvsovg084e3msbefpj9rgh611u30uug6t8",
    "Region": "us-east-1",
}


def format_unique_id(address: str) -> str:
    """Format the unique ID for a switchbot."""
    return address.replace(":", "").lower()


def short_address(address: str) -> str:
    """Convert a Bluetooth address to a short address."""
    results = address.replace("-", ":").split(":")
    return f"{results[-2].upper()}{results[-1].upper()}"[-4:]


def name_from_discovery(discovery: SwitchBotAdvertisement) -> str:
    """Get the name from a discovery."""
    return f'{discovery.data["modelFriendlyName"]} {short_address(discovery.address)}'


class SwitchbotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Switchbot."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SwitchbotOptionsFlowHandler:
        """Get the options flow for this handler."""
        return SwitchbotOptionsFlowHandler(config_entry)

    def __init__(self):
        """Initialize the config flow."""
        self._discovered_adv: SwitchBotAdvertisement | None = None
        self._discovered_advs: dict[str, SwitchBotAdvertisement] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered bluetooth device: %s", discovery_info.as_dict())
        await self.async_set_unique_id(format_unique_id(discovery_info.address))
        self._abort_if_unique_id_configured()
        parsed = parse_advertisement_data(
            discovery_info.device, discovery_info.advertisement
        )
        if not parsed or parsed.data.get("modelName") not in SUPPORTED_MODEL_TYPES:
            return self.async_abort(reason="not_supported")
        model_name = parsed.data.get("modelName")
        if (
            not discovery_info.connectable
            and model_name in CONNECTABLE_SUPPORTED_MODEL_TYPES
        ):
            # Source is not connectable but the model is connectable
            return self.async_abort(reason="not_supported")
        self._discovered_adv = parsed
        data = parsed.data
        self.context["title_placeholders"] = {
            "name": data["modelFriendlyName"],
            "address": short_address(discovery_info.address),
        }
        if self._discovered_adv.data["isEncrypted"]:
            return await self.async_step_password()
        return await self.async_step_confirm()

    async def _async_create_entry_from_discovery(
        self, user_input: dict[str, Any]
    ) -> FlowResult:
        """Create an entry from a discovery."""
        assert self._discovered_adv is not None
        discovery = self._discovered_adv
        name = name_from_discovery(discovery)
        model_name = discovery.data["modelName"]
        return self.async_create_entry(
            title=name,
            data={
                **user_input,
                CONF_ADDRESS: discovery.address,
                CONF_SENSOR_TYPE: str(SUPPORTED_MODEL_TYPES[model_name]),
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm a single device."""
        assert self._discovered_adv is not None
        if user_input is not None:
            return await self._async_create_entry_from_discovery(user_input)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv)
            },
        )

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the password step."""
        assert self._discovered_adv is not None
        if user_input is not None:
            # There is currently no api to validate the password
            # that does not operate the device so we have
            # to accept it as-is
            return await self._async_create_entry_from_discovery(user_input)

        return self.async_show_form(
            step_id="password",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv)
            },
        )

    async def async_step_lock_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the SwitchBot API auth step."""
        errors = {}
        assert self._discovered_adv is not None
        if user_input is not None:
            try:
                key_details = await self.hass.async_add_executor_job(
                    retrieve_lock_key,
                    self._discovered_adv.address,
                    user_input.get(CONF_USERNAME),
                    user_input.get(CONF_PASSWORD),
                )
                return await self.async_step_lock_key(key_details)
            except RuntimeError:
                errors = {
                    CONF_USERNAME: "auth_failed",
                }

        return self.async_show_form(
            step_id="lock_auth",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv),
            },
        )

    async def async_step_lock_chose_method(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the SwitchBot API chose method step."""
        assert self._discovered_adv is not None
        if user_input is not None:
            method = user_input.get("method")
            if method == "login_password":
                return await self.async_step_lock_auth()
            if method == "encryption_key":
                return await self.async_step_lock_key()

        return self.async_show_form(
            step_id="lock_chose_method",
            errors=None,
            data_schema=vol.Schema(
                {
                    vol.Required("method"): vol.In(
                        {
                            "login_password": "SwitchBot App login and password",
                            "encryption_key": "Lock encryption key",
                        }
                    ),
                }
            ),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv),
            },
        )

    async def async_step_lock_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the encryption key step."""
        errors = {}
        assert self._discovered_adv is not None
        if user_input is not None:
            if not await SwitchbotLock.verify_encryption_key(
                self._discovered_adv.device,
                user_input.get(CONF_KEY_ID),
                user_input.get(CONF_ENCRYPTION_KEY),
            ):
                errors = {
                    CONF_KEY_ID: "key_id_invalid",
                    CONF_ENCRYPTION_KEY: "encryption_key_invalid",
                }
            else:
                return await self._async_create_entry_from_discovery(user_input)

        return self.async_show_form(
            step_id="lock_key",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KEY_ID): str,
                    vol.Required(CONF_ENCRYPTION_KEY): str,
                }
            ),
            description_placeholders={
                "name": name_from_discovery(self._discovered_adv),
            },
        )

    @callback
    def _async_discover_devices(self) -> None:
        current_addresses = self._async_current_ids()
        for connectable in (True, False):
            for discovery_info in async_discovered_service_info(self.hass, connectable):
                address = discovery_info.address
                if (
                    format_unique_id(address) in current_addresses
                    or address in self._discovered_advs
                ):
                    continue
                parsed = parse_advertisement_data(
                    discovery_info.device, discovery_info.advertisement
                )
                if not parsed:
                    continue
                model_name = parsed.data.get("modelName")
                if (
                    discovery_info.connectable
                    and model_name in CONNECTABLE_SUPPORTED_MODEL_TYPES
                ) or model_name in NON_CONNECTABLE_SUPPORTED_MODEL_TYPES:
                    self._discovered_advs[address] = parsed

        if not self._discovered_advs:
            raise AbortFlow("no_unconfigured_devices")

    async def _async_set_device(self, discovery: SwitchBotAdvertisement) -> None:
        """Set the device to work with."""
        self._discovered_adv = discovery
        address = discovery.address
        await self.async_set_unique_id(
            format_unique_id(address), raise_on_progress=False
        )
        self._abort_if_unique_id_configured()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}
        device_adv: SwitchBotAdvertisement | None = None
        if user_input is not None:
            device_adv = self._discovered_advs[user_input[CONF_ADDRESS]]
            await self._async_set_device(device_adv)
            if device_adv.data.get("modelName") == SwitchbotModel.LOCK:
                return await self.async_step_lock_chose_method()
            if device_adv.data["isEncrypted"]:
                return await self.async_step_password()
            return await self._async_create_entry_from_discovery(user_input)

        self._async_discover_devices()
        if len(self._discovered_advs) == 1:
            # If there is only one device we can ask for a password
            # or simply confirm it
            device_adv = list(self._discovered_advs.values())[0]
            await self._async_set_device(device_adv)
            if device_adv.data.get("modelName") == SwitchbotModel.LOCK:
                return await self.async_step_lock_chose_method()
            if device_adv.data["isEncrypted"]:
                return await self.async_step_password()
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: name_from_discovery(parsed)
                            for address, parsed in self._discovered_advs.items()
                        }
                    ),
                }
            ),
            errors=errors,
        )


# This maybe should be moved to the PySwitchbot library
def retrieve_lock_key(device_mac: str, username: str, password: str):
    """Retrieve lock key from internal SwitchBot API."""
    msg = bytes(username + SWITCHBOT_COGNITO_POOL["AppClientId"], "utf-8")
    secret_hash = base64.b64encode(
        hmac.new(
            SWITCHBOT_COGNITO_POOL["AppClientSecret"].encode(),
            msg,
            digestmod=hashlib.sha256,
        ).digest()
    ).decode()

    cognito_idp_client = boto3.client(
        "cognito-idp", region_name=SWITCHBOT_COGNITO_POOL["Region"]
    )
    try:
        auth_response = cognito_idp_client.initiate_auth(
            ClientId=SWITCHBOT_COGNITO_POOL["AppClientId"],
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
                "SECRET_HASH": secret_hash,
            },
        )
    except cognito_idp_client.exceptions.NotAuthorizedException as err:
        raise RuntimeError("Failed to authenticate") from err
    except BaseException as err:
        raise RuntimeError("Unexpected error during authentication") from err

    if (
        auth_response is None
        or "AuthenticationResult" not in auth_response
        or "AccessToken" not in auth_response["AuthenticationResult"]
    ):
        raise RuntimeError("Unexpected authentication response")

    access_token = auth_response["AuthenticationResult"]["AccessToken"]
    key_response = requests.post(
        url=SWITCHBOT_INTERNAL_API_BASE_URL + "/developStage/keys/v1/communicate",
        headers={"authorization": access_token},
        json={
            "device_mac": device_mac.replace(":", "").replace("-", "").upper(),
            "keyType": "user",
        },
        timeout=10,
    )
    key_response_content = json.loads(key_response.content)
    if key_response_content["statusCode"] != 100:
        raise RuntimeError(
            f"Unexpected status code returned byt SwitchBot API: {key_response_content['statusCode']}"
        )

    return {
        CONF_KEY_ID: key_response_content["body"]["communicationKey"]["keyId"],
        CONF_ENCRYPTION_KEY: key_response_content["body"]["communicationKey"]["key"],
    }


class SwitchbotOptionsFlowHandler(OptionsFlow):
    """Handle Switchbot options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage Switchbot options."""
        if user_input is not None:
            # Update common entity options for all other entities.
            return self.async_create_entry(title="", data=user_input)

        options = {
            vol.Optional(
                CONF_RETRY_COUNT,
                default=self.config_entry.options.get(
                    CONF_RETRY_COUNT, DEFAULT_RETRY_COUNT
                ),
            ): int
        }

        return self.async_show_form(step_id="init", data_schema=vol.Schema(options))
