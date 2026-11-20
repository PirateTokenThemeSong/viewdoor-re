# Home Assistant integration — notes

Branch `ha-integration` packages the HA-facing part of the project as a
HACS-style custom integration.

## Layout

```
custom_components/vimar_door/
  __init__.py        setup/unload, shared VimarClient
  api.py             async /tdkcgi client + XML envelope
  config_flow.py     config and options flows
  camera.py          camera entity + two_way_audio capability
  button.py          relay 1 / relay 2 buttons
  const.py
  manifest.json
  strings.json
  translations/en.json, it.json
homeassistant/
  vimar_bridge.py    QVII -> RTSP bridge (external process/container)
docs/
  PROTOCOL.md, STREAM.md, TEST-DOORS.md
tools/
  offline probes and reverse-engineering helpers
```

## Config entry

All values live in the config entry data and can be edited from
**Configure** (options flow):

| key | default | meaning |
|-----|---------|---------|
| `host` | — | doorbell IP/hostname |
| `port` | `80` | `/tdkcgi` HTTP port |
| `auth_code` | — | out-auth-code or label code |
| `name` | `Cancelletto` | device/entity name |
| `stream_url` | `rtsp://127.0.0.1:8556/live` | bridge RTSP output |
| `two_way_audio` | `false` | declare the HA capability |

The auth code is SHA-256 hashed when it is not already 64 hex, matching the
app’s `passwordencode=1` behavior. The token is stored in HA’s config entry,
not in this repo.

## Camera streaming

`camera.py` returns `stream_source()` = `stream_url`. HA’s camera platform
selects a WebRTC provider (the built-in go2rtc integration) for supported
schemes such as `rtsp://`. The go2rtc provider adds the source to its own
configuration under the camera identifier and handles HLS/WebRTC.

The entity does not implement a snapshot image; the live stream is the
primary view. Use the bridge poster for a dashboard placeholder.

## Two-way audio — what is still missing

The HA side is ready:

```python
_attr_camera_entity_capabilities = CameraEntityCapabilities(
    two_way_audio=bool(config[CONF_TWO_WAY])
)
```

For the native **Start talking** button HA also requires WebRTC, the Labs
preview feature, and HTTPS. Those are environment settings.

The **bridge side** still needs a backchannel. Candidate implementations:

1. **go2rtc `exec` backchannel** — run a small Python pipe in the go2rtc
   container that reads PCMA from stdin and forwards it to a TCP talk service
   in the bridge container (`LanTalkSession`). This avoids writing an RTSP
   server.
2. **RTSP backchannel** — add a `sendonly` PCMU/PCMA track to the bridge RTSP
   server and implement SETUP/PLAY/RTP receive, then feed `LanTalkSession`.

Either way, the actual talk path must be tested with the doorbell speaker;
the current handshake works (A-law, `ready`) but simultaneous preview + talk
audio has not been verified.

## Testing without the doorbell

* `tests/test_api.py` covers password encoding and the XML envelope.
* Import checks can be run inside the HA container:

  ```bash
  python -c "import sys; sys.path.insert(0, '/tmp/ha_test'); \
    import custom_components.vimar_door.camera"
  ```

* Never point the unverified talk path at the real doorbell; use a fake TCP
  listener until the user confirms.
