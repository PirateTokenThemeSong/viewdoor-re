# Home Assistant integration — internal notes

> Internal reference. User installation and usage are in
> [Installation](../installation.md), [Usage](../usage.md) and
> [Doorbell ring](../ring.md).

This repository packages the HA-facing part of the project as a HACS-style
custom integration.

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
  vimar_cloud.py     automatic rotated stream-key refresh
bridge_tests/        offline RTSP/audio/talk tests
docs/internal/
  PROTOCOL.md, STREAM.md, TEST-DOORS.md, bridge.md
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

Verified on HA 2026.11.1: the camera entity reports `two_way_audio: true`, an
HA camera WebRTC offer connects with audio and video, and the bridge reports
`talk.active`, `talk.mic_active` and `talk.route_ready` while the browser's
microphone track is connected.

The backend implements both directions:

* **Listen** — the bridge opens a talk channel and sends `type 13 disable`
  (`route_receive_to_video`). The doorbell then sends the visitor's voice as
  **E3 on the video session**; `AlwaysAudioRtspServer` publishes those E3
  frames as PCMA/8 kHz (A-law; mu-law/PCM16 are transcoded). Verified live:
  508 E3 frames in 20 s and a 10 s RTSP recording with signal.
* **Talk / microphone** — the RTSP server advertises a `sendonly` PCMA track
  (`trackID=2`); go2rtc opens the second session and the talk server forwards
  the PCMA as 512-sample A-law blocks on the routed talk channel. Verified
  with a deliberately silent browser track: 1007 outbound audio packets and
  the bridge's `mic_active`/`route_ready` true; no audible test signal was
  sent to the gate.

The integration stream URL stays the bridge URL
`rtsp://127.0.0.1:8556/live`; no extra go2rtc source is required. go2rtc is
only Home Assistant's WebRTC provider.

**Both directions verified at transport level.** With the routed talk session,
the doorbell sent 508 E3 frames in 20 s and the RTSP audio recording contained
signal (peak 3008, RMS 183.5). In the HA WebRTC session the browser received
153600 audio bytes and 131908 video bytes, while the silent microphone track
produced 1007 outbound packets; the bridge reported `talk.active=true`,
`mic_active=true` and `route_ready=true`. The only remaining physical check is
an audible microphone-to-gate-speaker test. Until you do that test yourself,
keep `two_way_audio` off on a production install.

## Dashboard card

The native **Start talking** control lives in the camera more-info dialog, not
in `custom:webrtc-camera`. Add a button card that opens it:

```yaml
- type: button
  entity: camera.cancelletto
  name: Parla / ascolta (2 vie)
  icon: mdi:phone-in-talk
  show_state: false
  tap_action:
    action: more-info
```

Tap the card, wait for the live stream, then press **Start talking**. The
browser needs microphone permission. The existing `custom:webrtc-camera` card
remains useful for listening; the native dialog carries both directions.

## Doorbell ring

This is a **cloud** event, not a local QVII video event.  The Android app's
`FcmPushService` receives the QV push branch with `AlarmEvent=19`
(`QvPushInfo.ALARM_TYPE_CALL`) and opens `PushCallActivity`.  The call journal
(`UserAlarm` / `client-query-recordlist`) contains the same `event=19` row.
Event 20 is an answered/handled call, not a new ring; 38 is an indoor call.

The bridge's `vimar_ring.py` polls the journal every five seconds, baselines
the existing rows on startup, and sends new CALL 19 rows to an HA webhook.
It is read-only: no answer, no read-state update, no door command, no media
download and no push-token registration.

Enable it in the bridge environment:

```yaml
RING_WATCH: "1"
RING_WEBHOOK_URL: "http://127.0.0.1:8123/api/webhook/<webhook-id>"
RING_POLL_SECONDS: "5"
CLOUD_AUTH_URL: "https://r1-2-sec.qvcloud.net/auth/user"
CLOUD_ALARM_URL: "https://r1-2-sec.qvcloud.net/UserAlarm"  # same host as auth
```

Create a **webhook** automation in HA (the bridge and HA share the host
network, so `127.0.0.1` is correct):

```yaml
- id: vimar_door_ring
  alias: Vimar - qualcuno ha suonato
  trigger:
    - platform: webhook
      webhook_id: <webhook-id>
      allowed_methods: [POST]
      local_only: true
  action:
    - service: notify.mobile_app_<phone>
      data:
        title: Cancelletto
        message: Qualcuno ha suonato.
```

Choose the action you want (notification, snapshot, light, TTS, ...).  The
webhook payload includes `channel`, `record_id`, `alarm_id`, `time` and
`ring_count`.  `/status` exposes `ring.enabled`, `ring.ok`, `ring.last_error`,
`ring.ring_count` and `ring.last_record` for diagnostics.

The account file must be a **fresh** QV account login.  An OAuth token alone
is not enough: the user login also needs a valid `password`; when it expires,
`ring.last_error` starts with `cloud-login-...`.  Do not put the account file
or the webhook id in this public repository.

## Testing without the doorbell

* `tests/test_api.py` covers password encoding and the XML envelope.
* `bridge_tests/` covers the always-on PCMA RTSP track, the route packet and
  the microphone forwarding (run `cd bridge_tests && pytest`).
* Import checks can be run inside the HA container:

  ```bash
  python -c "import sys; sys.path.insert(0, '/tmp/ha_test'); \
    import custom_components.vimar_door.camera"
  ```

* Never point the unverified talk path at the real doorbell; use a fake TCP
  listener until the user confirms.
