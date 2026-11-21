"""Camera entity for a Vimar VIEW Door station."""

from __future__ import annotations

from homeassistant.components.camera import (
    Camera,
    CameraEntityCapabilities,
    CameraEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_HOST,
    CONF_NAME,
    CONF_STREAM_URL,
    CONF_TWO_WAY,
    DOMAIN,
)
from .api import VimarClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the camera entity."""
    client: VimarClient = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VimarDoorCamera(entry, client)])


class VimarDoorCamera(Camera):
    """Live view from the QVII -> RTSP bridge."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = "Vimar"
    _attr_model = "VIEW Door (Qualvision)"

    def __init__(self, entry: ConfigEntry, client: VimarClient) -> None:
        """Initialize the camera."""
        super().__init__()
        self._config = {**entry.data, **entry.options}
        self._client = client
        self._attr_name = self._config[CONF_NAME]
        self._attr_unique_id = f"{entry.entry_id}_camera"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._config[CONF_NAME],
            manufacturer="Vimar",
            model="VIEW Door (Qualvision)",
            configuration_url=f"http://{self._config[CONF_HOST]}/",
        )
        self._attr_camera_entity_capabilities = CameraEntityCapabilities(
            two_way_audio=bool(self._config.get(CONF_TWO_WAY, False))
        )
        self._stream_url = self._config[CONF_STREAM_URL]

    @property
    def available(self) -> bool:
        """The bridge is independent; keep the entity available."""
        return True

    async def stream_source(self) -> str | None:
        """Return the RTSP URL consumed by HA / go2rtc."""
        return self._stream_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image (not provided by the bridge)."""
        return None
