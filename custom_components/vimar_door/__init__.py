"""Vimar VIEW Door integration for Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VimarClient
from .bridge_process import BridgeProcess
from .const import (
    BRIDGE_MODE_EMBEDDED,
    CONF_AUTH_CODE,
    CONF_BRIDGE_MODE,
    CONF_HOST,
    CONF_PORT,
    CONF_STREAM_PORT,
    DEFAULT_RTSP_PORT,
    DEFAULT_WEBHOOK_BASE,
    DOMAIN,
    STREAM_PORT_ATTEMPTS,
    STREAM_PORT_MAX,
    STREAM_PORT_MIN,
)
from .ports import (
    async_find_stream_port,
    stream_url as bridge_stream_url,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON, Platform.CAMERA]

CLOUD_ACCOUNT_FILE = "cloud-account.json"
STREAM_CREDENTIALS_FILE = "stream_credentials.json"


def credentials_paths(hass: HomeAssistant) -> tuple[Path, Path]:
    """Return the private bridge credential paths in the HA config dir."""
    base = Path(hass.config.path("vimar_door"))
    return base / CLOUD_ACCOUNT_FILE, base / STREAM_CREDENTIALS_FILE


def preferred_stream_port(config: dict) -> int:
    """Return the configured bridge base port, falling back to the default."""
    try:
        port = int(config.get(CONF_STREAM_PORT) or DEFAULT_RTSP_PORT)
    except (TypeError, ValueError):
        return DEFAULT_RTSP_PORT
    if not STREAM_PORT_MIN <= port <= STREAM_PORT_MAX:
        return DEFAULT_RTSP_PORT
    return port


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one doorbell."""
    config = {**entry.data, **entry.options}
    client = VimarClient(
        async_get_clientsession(hass),
        config[CONF_HOST],
        config[CONF_AUTH_CODE],
        int(config[CONF_PORT]),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client

    webhook_id: str | None = None
    bridge: BridgeProcess | None = None
    if config.get(CONF_BRIDGE_MODE) == BRIDGE_MODE_EMBEDDED:
        cloud_path, credentials_path = credentials_paths(hass)
        if not cloud_path.is_file() or not credentials_path.is_file():
            raise ConfigEntryNotReady("Vimar cloud credentials are missing")

        reserved = {
            int(value)
            for key, value in hass.data[DOMAIN].items()
            if key.startswith("stream_port:")
            and key != f"stream_port:{entry.entry_id}"
        }
        preferred = preferred_stream_port(config)
        port = await async_find_stream_port(hass, preferred, reserved=reserved)
        if port is None:
            raise ConfigEntryNotReady(
                "No free TCP port block for the bridge in "
                f"{preferred}-{preferred + STREAM_PORT_ATTEMPTS - 1}; "
                "change the bridge RTSP port in the integration options"
            )
        stream_url = bridge_stream_url(port)
        hass.data[DOMAIN][f"stream_port:{entry.entry_id}"] = port
        hass.data[DOMAIN][f"stream_url:{entry.entry_id}"] = stream_url
        if port != preferred:
            _LOGGER.warning(
                "Bridge port block starting at %s is busy; using %s",
                preferred,
                port,
            )
        _LOGGER.info("Bridge RTSP URL for %s: %s", entry.title, stream_url)

        webhook_id = webhook.async_generate_id()
        webhook.async_register(
            hass,
            DOMAIN,
            entry.title,
            webhook_id,
            _async_ring_webhook,
            local_only=True,
            allowed_methods=("POST",),
        )
        bridge = BridgeProcess(
            hass,
            host=config[CONF_HOST],
            credentials_path=str(credentials_path),
            cloud_path=str(cloud_path),
            ring_webhook_url=(
                f"{DEFAULT_WEBHOOK_BASE}/api/webhook/{webhook_id}"
            ),
            rtsp_port=port,
        )
        await bridge.start()
        hass.data[DOMAIN][f"bridge:{entry.entry_id}"] = bridge
        _LOGGER.info("Embedded Vimar bridge started for %s", entry.title)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    if webhook_id is not None:
        entry.async_on_unload(
            lambda: webhook.async_unregister(hass, webhook_id)
        )
    return True


async def _async_ring_webhook(
    hass: HomeAssistant, webhook_id: str, request
) -> None:
    """Forward a bridge ring to the HA event bus."""
    del webhook_id
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed webhook body
        return
    if isinstance(payload, dict):
        hass.bus.async_fire("vimar_door_ring", payload)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one doorbell and stop its embedded bridge."""
    bridge: BridgeProcess | None = hass.data[DOMAIN].pop(
        f"bridge:{entry.entry_id}", None
    )
    if bridge is not None:
        await bridge.stop()
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unloaded:
        data = hass.data.get(DOMAIN, {})
        data.pop(entry.entry_id, None)
        data.pop(f"stream_port:{entry.entry_id}", None)
        data.pop(f"stream_url:{entry.entry_id}", None)
    return unloaded


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
