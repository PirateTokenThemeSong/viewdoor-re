"""Constants for the Vimar VIEW Door integration."""

from __future__ import annotations

DOMAIN = "vimar_door"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_AUTH_CODE = "auth_code"
CONF_NAME = "name"
CONF_STREAM_URL = "stream_url"
CONF_TWO_WAY = "two_way_audio"

DEFAULT_PORT = 80
DEFAULT_NAME = "Cancelletto"
DEFAULT_STREAM_URL = "rtsp://127.0.0.1:8556/live"

# The local CGI command uses <door>0</door> for the door station channel and
# <locknumber>1|2</locknumber> for the two physical relays.
DOOR_CHANNEL = 0
