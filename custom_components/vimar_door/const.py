"""Constants for the Vimar VIEW Door integration."""

from __future__ import annotations

DOMAIN = "vimar_door"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_AUTH_CODE = "auth_code"
CONF_NAME = "name"
CONF_STREAM_URL = "stream_url"
CONF_STREAM_PORT = "stream_port"
CONF_TWO_WAY = "two_way_audio"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_BRIDGE_MODE = "bridge_mode"

BRIDGE_MODE_EMBEDDED = "embedded"
BRIDGE_MODE_EXTERNAL = "external"

DEFAULT_PORT = 80
DEFAULT_STREAM_HOST = "127.0.0.1"
DEFAULT_RTSP_PORT = 8556
DEFAULT_STREAM_URL = f"rtsp://{DEFAULT_STREAM_HOST}:{DEFAULT_RTSP_PORT}/live"
DEFAULT_BRIDGE_PORT = DEFAULT_RTSP_PORT

# The managed bridge opens one small block of TCP ports, relative to the base
# RTSP port: the RTSP listener (0), the loopback RTSP server behind it (1), the
# JSON status endpoint (2) and the talk backchannel (4). If the block is busy
# the integration walks forward to the next free one instead of failing.
BRIDGE_PORT_OFFSETS = (0, 1, 2, 4)
BRIDGE_PORT_SPAN = max(BRIDGE_PORT_OFFSETS)
PORT_OFFSET_RTSP = 0
PORT_OFFSET_INNER = 1
PORT_OFFSET_STATUS = 2
PORT_OFFSET_TALK = 4
STREAM_PORT_ATTEMPTS = 10
STREAM_PORT_MIN = 1024
STREAM_PORT_MAX = 65535 - BRIDGE_PORT_SPAN
DEFAULT_STATUS_PORT = DEFAULT_RTSP_PORT + PORT_OFFSET_STATUS
DEFAULT_TALK_PORT = DEFAULT_RTSP_PORT + PORT_OFFSET_TALK
DEFAULT_WEBHOOK_BASE = "http://127.0.0.1:8123"

# The local CGI command uses <door>0</door> for the door station channel and
# <locknumber>1|2</locknumber> for the two physical relays.
DOOR_CHANNEL = 0
