"""Config flow for the Vimar VIEW Door integration."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import VimarClient, VimarError
from .bootstrap import BootstrapError, BootstrapResult, bootstrap, new_client_id
from .const import (
    BRIDGE_MODE_EMBEDDED,
    CONF_AUTH_CODE,
    CONF_BRIDGE_MODE,
    CONF_EMAIL,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_STREAM_URL,
    CONF_TWO_WAY,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_STREAM_URL,
    DOMAIN,
)

CLOUD_ACCOUNT_FILE = "cloud-account.json"
STREAM_CREDENTIALS_FILE = "stream_credentials.json"
ERROR_MAP = {
    "sso_auth_failed": "invalid_auth",
    "qv_login_failed": "invalid_auth",
    "sso_form_changed": "cloud_error",
    "sso_login_page": "cloud_error",
    "sso_empty_credentials": "cloud_error",
    "sso_failed": "cloud_error",
    "cloud_failed": "cloud_error",
    "device_list_failed": "cloud_error",
    "device_token_failed": "cloud_error",
    "invalid_device_token": "cloud_error",
    "invalid_cloud_response": "cloud_error",
    "no_devices": "no_devices",
    "multiple_devices": "multiple_devices",
    "device_not_found": "multiple_devices",
    "missing_input": "invalid_input",
    "invalid_client": "cloud_error",
}


def _normalize_host(value: str) -> str:
    """Accept a host, URL-like input, or a copied http(s) address."""
    host = value.strip()
    for prefix in ("http://", "https://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host.split("/", 1)[0].strip()


def _auto_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the one-step email/password/IP form."""
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_EMAIL, default=values.get(CONF_EMAIL, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
            vol.Required(
                CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_HOST, default=values.get(CONF_HOST, "")
            ): TextSelector(),
            vol.Optional(
                CONF_NAME, default=values.get(CONF_NAME, DEFAULT_NAME)
            ): TextSelector(),
            vol.Optional(
                CONF_TWO_WAY, default=values.get(CONF_TWO_WAY, False)
            ): BooleanSelector(),
        }
    )


def _manual_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the legacy external-bridge form."""
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=values.get(CONF_HOST, "")
            ): TextSelector(),
            vol.Required(
                CONF_PORT, default=values.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_AUTH_CODE, default=values.get(CONF_AUTH_CODE, "")
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_NAME, default=values.get(CONF_NAME, DEFAULT_NAME)
            ): TextSelector(),
            vol.Required(
                CONF_STREAM_URL,
                default=values.get(CONF_STREAM_URL, DEFAULT_STREAM_URL),
            ): TextSelector(),
            vol.Required(
                CONF_TWO_WAY, default=values.get(CONF_TWO_WAY, False)
            ): BooleanSelector(),
        }
    )


async def _async_validate_device(
    hass,
    host: str,
    port: int,
    auth_code: str,
    two_way: bool,
    stream_url: str,
) -> dict[str, str]:
    """Validate the local device and the two-way-audio prerequisites."""
    if not host:
        return {"base": "invalid_host"}
    if not auth_code.strip():
        return {"base": "invalid_auth"}
    if not stream_url:
        return {CONF_STREAM_URL: "invalid_stream_url"}
    if two_way and not stream_url.startswith(("rtsp://", "rtsps://")):
        return {CONF_STREAM_URL: "two_way_requires_rtsp"}

    client = VimarClient(
        async_get_clientsession(hass),
        host,
        auth_code,
        int(port),
    )
    try:
        await client.async_validate()
    except VimarError as err:
        if err.code in ("401", "-1"):
            return {"base": "invalid_auth"}
        return {"base": "device_error"}
    except (aiohttp.ClientError, TimeoutError, OSError):
        return {"base": "cannot_connect"}
    return {}


def _paths(hass) -> tuple[str, str]:
    base = Path(hass.config.path("vimar_door"))
    return (
        str(base / CLOUD_ACCOUNT_FILE),
        str(base / STREAM_CREDENTIALS_FILE),
    )


class VimarDoorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a one-step Vimar account setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for Vimar email/password and the doorbell IP."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            name = user_input.get(CONF_NAME, "").strip() or DEFAULT_NAME
            two_way = bool(user_input.get(CONF_TWO_WAY, False))
            if not host:
                errors["base"] = "invalid_host"
            elif not email or not password:
                errors["base"] = "invalid_auth"
            else:
                client_id = new_client_id()
                cloud_path, credentials_path = _paths(self.hass)
                try:
                    result: BootstrapResult = (
                        await self.hass.async_add_executor_job(
                            functools.partial(
                                bootstrap,
                                email,
                                password,
                                host,
                                client_id,
                                cloud_path=cloud_path,
                                credentials_path=credentials_path,
                            )
                        )
                    )
                except BootstrapError as err:
                    errors["base"] = ERROR_MAP.get(err.code, "cloud_error")
                except Exception:  # noqa: BLE001 - don't expose request details
                    errors["base"] = "cloud_error"
                else:
                    errors = await _async_validate_device(
                        self.hass,
                        host,
                        DEFAULT_PORT,
                        result.auth_code,
                        two_way,
                        DEFAULT_STREAM_URL,
                    )
                    if not errors:
                        await self.async_set_unique_id(
                            f"{host}:{DEFAULT_PORT}"
                        )
                        self._abort_if_unique_id_configured()
                        data = {
                            CONF_HOST: host,
                            CONF_PORT: DEFAULT_PORT,
                            CONF_AUTH_CODE: result.auth_code,
                            CONF_NAME: name,
                            CONF_STREAM_URL: DEFAULT_STREAM_URL,
                            CONF_TWO_WAY: two_way,
                            CONF_BRIDGE_MODE: BRIDGE_MODE_EMBEDDED,
                        }
                        return self.async_create_entry(
                            title=name,
                            data=data,
                        )

        return self.async_show_form(
            step_id="user",
            data_schema=_auto_schema(user_input),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return VimarDoorOptionsFlow()


class VimarDoorOptionsFlow(OptionsFlow):
    """Edit advanced settings without re-running the cloud bootstrap."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the settings."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            errors = await _async_validate_device(
                self.hass,
                host,
                int(user_input[CONF_PORT]),
                user_input[CONF_AUTH_CODE],
                bool(user_input[CONF_TWO_WAY]),
                user_input[CONF_STREAM_URL].strip(),
            )
            if not errors:
                data = {
                    **user_input,
                    CONF_HOST: host,
                    CONF_NAME: user_input[CONF_NAME].strip() or DEFAULT_NAME,
                    CONF_STREAM_URL: user_input[CONF_STREAM_URL].strip(),
                }
                return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=_manual_schema(user_input or current),
            errors=errors,
        )
