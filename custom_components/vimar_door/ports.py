"""Free TCP port selection for the managed bridge.

The embedded bridge needs a small block of ports (RTSP listener, inner RTSP
server, status endpoint, talk channel). Another Home Assistant add-on, a
leftover bridge process or a second doorbell can take them; instead of failing
to set up, the integration scans forward from the configured base port and
uses the first free block it finds.
"""

from __future__ import annotations

import functools
import socket
import sys
from typing import TYPE_CHECKING

from .const import (
    BRIDGE_PORT_OFFSETS,
    BRIDGE_PORT_SPAN,
    CONF_STREAM_URL,
    DEFAULT_STREAM_HOST,
    DOMAIN,
    STREAM_PORT_ATTEMPTS,
    STREAM_PORT_MAX,
    STREAM_PORT_MIN,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# On Linux the bridge listens with SO_REUSEADDR, so probing the same way keeps
# a port in TIME_WAIT usable after a reload. On Windows SO_REUSEADDR lets a
# socket steal a port that is still in use, so the probe must bind plainly.
PROBE_REUSE_ADDR = sys.platform != "win32"


def stream_url(port: int, host: str = DEFAULT_STREAM_HOST) -> str:
    """Return the RTSP URL served by a bridge on ``port``."""
    return f"rtsp://{host}:{port}/live"


def port_free(port: int) -> bool:
    """Return True when a TCP listener can bind ``port`` on every interface."""
    if not STREAM_PORT_MIN <= port <= STREAM_PORT_MAX + BRIDGE_PORT_SPAN:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if PROBE_REUSE_ADDR:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.listen(1)
    except OSError:
        return False
    finally:
        sock.close()
    return True


def block_free(port: int) -> bool:
    """Return True when the whole bridge port block at ``port`` is free."""
    if not STREAM_PORT_MIN <= port <= STREAM_PORT_MAX:
        return False
    return all(port_free(port + offset) for offset in BRIDGE_PORT_OFFSETS)


def _overlaps_reserved(port: int, reserved: set[int]) -> bool:
    """True when ``port``'s block would collide with a reserved block."""
    return any(abs(port - other) <= BRIDGE_PORT_SPAN for other in reserved)


def find_stream_port(
    preferred: int,
    *,
    attempts: int = STREAM_PORT_ATTEMPTS,
    reserved: set[int] | None = None,
) -> int | None:
    """Return the first free bridge base port at or above ``preferred``."""
    used = reserved or set()
    for step in range(max(attempts, 1)):
        candidate = preferred + step
        if _overlaps_reserved(candidate, used):
            continue
        if block_free(candidate):
            return candidate
    return None


async def async_find_stream_port(
    hass: HomeAssistant,
    preferred: int,
    *,
    attempts: int = STREAM_PORT_ATTEMPTS,
    reserved: set[int] | None = None,
) -> int | None:
    """Run the blocking port scan in the executor."""
    return await hass.async_add_executor_job(
        functools.partial(
            find_stream_port,
            preferred,
            attempts=attempts,
            reserved=reserved,
        )
    )


def entry_stream_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the RTSP URL actually served for a set-up entry.

    The bridge may have moved off the configured port, so the runtime value
    wins over the stored one.
    """
    return str(
        hass.data.get(DOMAIN, {}).get(f"stream_url:{entry.entry_id}")
        or {**entry.data, **entry.options}.get(CONF_STREAM_URL, "")
    )
