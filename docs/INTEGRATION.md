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
  vimar_talk.py      loopback PCMA -> LanTalkSession talk bridge
bridge_tests/        offline RTSP/audio/talk tests
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

## Two-way audio

The HA side declares the capability:

```python
_attr_camera_entity_capabilities = CameraEntityCapabilities(
    two_way_audio=bool(config[CONF_TWO_WAY])
)
```

For the native **Start talking** button HA also requires WebRTC, the Labs
preview feature, and HTTPS. Those are environment settings.

The backend implements both directions:

* **Listen** — `AlwaysAudioRtspServer` advertises PCMA from the first
  DESCRIBE, so go2rtc/HA can negotiate audio before the doorbell sends its
  first `E3` frame. `E3` A-law is forwarded; mu-law and PCM16 are transcoded
  to A-law. No `E3` means a silent track (normal idle preview).
* **Talk** — the bridge RTSP server advertises a `sendonly` PCMA track
  (`trackID=2`). go2rtc discovers it on the main session and opens a second
  RTSP session to the same bridge URL; the bridge strips the RTP header and
  forwards the PCMA payload to `vimar_talk.py` on `127.0.0.1:8560`. The talk
  server groups the bytes into 40 ms frames and sends them via
  `LanTalkSession` (selector 65535).

The integration stream URL stays the bridge URL
`rtsp://127.0.0.1:8556/live`; no extra go2rtc source is required. go2rtc is
only Home Assistant's WebRTC provider.

**Not verified on hardware yet.** The talk handshake works (A-law, `ready`)
and the offline tests cover framing/transcoding, but simultaneous preview +
talk to the real gate speaker still needs a controlled test. Keep
`two_way_audio` off until then.

## Testing without the doorbell

* `tests/test_api.py` covers password encoding and the XML envelope.
* `bridge_tests/` covers the always-on PCMA RTSP track and the loopback talk
  framing with a fake `LanTalkSession` (run `cd bridge_tests && pytest`).
* Import checks can be run inside the HA container:

  ```bash
  python -c "import sys; sys.path.insert(0, '/tmp/ha_test'); \
    import custom_components.vimar_door.camera"
  ```

* Never point the unverified talk path at the real doorbell; use a fake TCP
  listener until the user confirms.
