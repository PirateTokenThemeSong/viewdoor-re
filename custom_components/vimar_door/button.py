"""Door relay buttons for a Vimar VIEW Door station."""

from __future__ import annotations

import aiohttp
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VimarClient, VimarError
from .const import CONF_HOST, CONF_NAME, DOMAIN

# The pedestrian gate is relay 2 on the IDS9457W; relay 1 is the other output.
RELAY_2 = 2
RELAY_1 = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the relay buttons."""
    config = {**entry.data, **entry.options}
    client: VimarClient = hass.data[DOMAIN][entry.entry_id]
    base_name = config[CONF_NAME]
    async_add_entities(
        [
            VimarDoorButton(
                entry,
                client,
                locknumber=RELAY_2,
                name=base_name,
                icon="mdi:gate",
            ),
            VimarDoorButton(
                entry,
                client,
                locknumber=RELAY_1,
                name=f"{base_name} (relay 1)",
                icon="mdi:door-closed-lock",
            ),
        ]
    )


class VimarDoorButton(ButtonEntity):
    """Fire one physical door relay."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        client: VimarClient,
        *,
        locknumber: int,
        name: str,
        icon: str,
    ) -> None:
        """Initialize the button."""
        config = {**entry.data, **entry.options}
        self._client = client
        self._locknumber = locknumber
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_relay_{locknumber}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=config[CONF_NAME],
            manufacturer="Vimar",
            model="VIEW Door (Qualvision)",
            configuration_url=f"http://{config[CONF_HOST]}/",
        )

    async def async_press(self) -> None:
        """Fire the relay once."""
        try:
            await self._client.async_open_door(self._locknumber)
        except VimarError as err:
            raise HomeAssistantError(
                f"Door relay {self._locknumber} failed (device error {err.code})"
            ) from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise HomeAssistantError(
                f"Door relay {self._locknumber} failed: {err}"
            ) from err
