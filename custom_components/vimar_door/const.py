"""Constants for the Vimar VIEW Door integration."""

from __future__ import annotations

DOMAIN = "vimar_door"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_AUTH_CODE = "auth_code"
CONF_NAME = "name"
CONF_STREAM_URL = "stream_url"
CONF_TWO_WAY = "two_way_audio"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_BRIDGE_MODE = "bridge_mode"

BRIDGE_MODE_EMBEDDED = "embedded"
BRIDGE_MODE_EXTERNAL = "external"

DEFAULT_PORT = 80
DEFAULT_STREAM_URL = "rtsp://127.0.0.1:8556/live"
DEFAULT_BRIDGE_PORT = 8556
DEFAULT_STATUS_PORT = 8558
DEFAULT_WEBHOOK_BASE = "http://127.0.0.1:8123"

# The local CGI command uses <door>0</door> for the door station channel and
# <locknumber>1|2</locknumber> for the two physical relays.
DOOR_CHANNEL = 0
