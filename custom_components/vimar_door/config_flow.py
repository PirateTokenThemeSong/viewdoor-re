"""Config flow for the Vimar VIEW Door integration."""

from __future__ import annotations

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
from .const import (
    CONF_AUTH_CODE,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_STREAM_URL,
    CONF_TWO_WAY,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_STREAM_URL,
    DOMAIN,
)


def _normalize_host(value: str) -> str:
    """Accept `host`, `host:port`-free URLs, and pasted http(s) prefixes."""
    host = value.strip()
    for prefix in ("http://", "https://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host.split("/", 1)[0].strip()


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the config/options schema."""
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


async def _async_validate(hass, user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the device and the two-way-audio prerequisites."""
    errors: dict[str, str] = {}

    host = _normalize_host(user_input[CONF_HOST])
    if not host:
        errors["base"] = "invalid_host"
        return errors

    if not user_input[CONF_AUTH_CODE].strip():
        errors["base"] = "invalid_auth"
        return errors

    stream_url = user_input[CONF_STREAM_URL].strip()
    if not stream_url:
        errors[CONF_STREAM_URL] = "invalid_stream_url"
    elif user_input[CONF_TWO_WAY] and not stream_url.startswith(
        ("rtsp://", "rtsps://")
    ):
        errors[CONF_STREAM_URL] = "two_way_requires_rtsp"

    if errors:
        return errors

    client = VimarClient(
        async_get_clientsession(hass),
        host,
        user_input[CONF_AUTH_CODE],
        int(user_input[CONF_PORT]),
    )
    try:
        await client.async_validate()
    except VimarError as err:
        if err.code in ("401", "-1"):
            errors["base"] = "invalid_auth"
        else:
            errors["base"] = "device_error"
    except (aiohttp.ClientError, TimeoutError, OSError):
        errors["base"] = "cannot_connect"
    return errors


class VimarDoorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for one doorbell."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the doorbell and bridge details."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _async_validate(self.hass, user_input)
            if not errors:
                host = _normalize_host(user_input[CONF_HOST])
                await self.async_set_unique_id(
                    f"{host}:{int(user_input[CONF_PORT])}"
                )
                self._abort_if_unique_id_configured()
                data = {
                    **user_input,
                    CONF_HOST: host,
                    CONF_NAME: user_input[CONF_NAME].strip() or DEFAULT_NAME,
                    CONF_STREAM_URL: user_input[CONF_STREAM_URL].strip(),
                }
                return self.async_create_entry(
                    title=data[CONF_NAME], data=data
                )

        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return VimarDoorOptionsFlow()


class VimarDoorOptionsFlow(OptionsFlow):
    """Edit host/auth/bridge settings without re-adding the device."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the settings."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _async_validate(self.hass, user_input)
            if not errors:
                data = {
                    **user_input,
                    CONF_HOST: _normalize_host(user_input[CONF_HOST]),
                    CONF_NAME: user_input[CONF_NAME].strip() or DEFAULT_NAME,
                    CONF_STREAM_URL: user_input[CONF_STREAM_URL].strip(),
                }
                return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or current),
            errors=errors,
        )
