<img src="assets/icon.png" width="64" height="64" alt="">

# Vimar VIEW Door — Home Assistant integration

Custom integration for **Vimar VIEW Door** (Qualvision QVII) doorbells such as
the Elvox `IDS9457W`. It exposes the live camera and the two door relays as
native Home Assistant entities, with an optional **two-way audio capability**
for the HA 2026.11 “Start talking” UI.

> This branch (`ha-integration`) is the integration-first rewrite. The
> reverse-engineering write-up, protocol notes and standalone tools remain on
> the [`main`](https://github.com/PirateTokenThemeSong/viewdoor-re/tree/main)
> branch and in [`docs/`](docs/).

## Entities

| entity | what it does |
|--------|--------------|
| `camera.cancelletto` | live RTSP stream from the QVII → RTSP bridge |
| `button.cancelletto` | fires relay **2** (pedestrian gate) |
| `button.cancelletto_relay_1` | fires relay **1** (second output) |

The camera declares `CameraEntityCapabilities(two_way_audio=True)` only when
the option is enabled. The actual microphone path still depends on a
backchannel-capable bridge; see [Two-way audio](#two-way-audio).

## Architecture

```
Home Assistant                         LAN
┌──────────────────────┐   RTSP :8556   ┌─────────────────────┐   QVII :34567   ┌──────────┐
│ camera entity        │ ─────────────► │ vimar-stream bridge │ ──────────────► │ doorbell │
│ button entities      │                │  + vimar_talk :8560 │ ◄── talk ────── │ speaker  │
│ go2rtc (WebRTC)      │   HTTP :80 /tdkcgi ───────────────────────────────────► │          │
└──────────────────────┘                └─────────────────────┘                 └──────────┘
```

* The **bridge** is the existing single-session QVII → RTSP service
  ([`homeassistant/vimar_bridge.py`](homeassistant/vimar_bridge.py)).
* The **integration** never speaks QVII itself. It creates the entities, calls
  `/tdkcgi` for the relays, and gives HA an RTSP source it can stream.
* `go2rtc` (shipped with HA 2026.11) provides WebRTC when available. The
  integration adds the RTSP source to go2rtc automatically through HA’s camera
  platform / go2rtc WebRTC provider.

## Requirements

* Home Assistant **2026.11** or newer (for `CameraEntityCapabilities`).
* The **vimar-stream bridge** reachable from HA, e.g.
  `rtsp://127.0.0.1:8556/live`.
* The doorbell’s **out-auth-code** (64 hex) from the Qualvision cloud, or the
  label code printed under the QR sticker.
* HTTPS for microphone access; enable **Settings → System → Labs → Two-way
  audio** when you want the native talk button.

## Install (manual)

1. Copy the integration folder:

   ```bash
   cp -r custom_components/vimar_door /config/custom_components/
   ```

2. If you used the old YAML package (`homeassistant/vimar_doors.yaml`), remove it
   first so `button.cancelletto` does not exist twice. Restart Home Assistant.

3. **Settings → Devices & Services → Add integration → Vimar VIEW Door**.

4. Fill in:

   | field | example | notes |
   |-------|---------|-------|
   | host | `192.168.1.50` | doorbell IP or hostname |
   | port | `80` | LAN CGI port |
   | auth code | `<OUT_AUTH_CODE>` | 64-hex out-auth-code, or label code |
   | name | `Cancelletto` | entity/device name |
   | RTSP stream URL | `rtsp://127.0.0.1:8556/live` | output of the bridge |
   | two-way audio | off | see below |

The token is stored in HA’s config entry (`.storage`), never in this repo.

## HACS

This integration lives on the `ha-integration` branch. HACS normally installs
from the repository’s default branch; either merge this branch or create a
release/tag from it and point HACS at that version. A tagged release is the
cleanest option once the branch is stable.

## Two-way audio

Home Assistant 2026.11 can show a **Start talking** button when:

1. the camera entity declares two-way audio;
2. the stream is served over **WebRTC** (go2rtc);
3. the **Two-way audio** Labs feature is enabled;
4. HA is opened over **HTTPS**.

This integration provides step 1; the bridge now provides both audio paths:

* **Listen** — the bridge opens a talk channel and sends `type 13 disable`
  (`route_receive_to_video`). The doorbell then routes the visitor's voice to
  the **video session as E3**; `AlwaysAudioRtspServer` publishes it as
  PCMA/8 kHz on the RTSP audio track. Verified live: 508 E3 frames in 20 s
  and a 10 s recording with signal.
* **Talk** — `vimar_route.py` sends 512-sample A-law blocks on the same
  routed talk channel. The RTSP `sendonly` track receives the browser
  microphone PCMA and forwards it here.

Wiring:

1. Run the bridge with `AUDIO=1 TALK=1` (default).
2. The integration’s stream URL stays the bridge URL
   `rtsp://127.0.0.1:8556/live`; the bridge itself advertises the
   `sendonly` PCMA track and accepts the second RTSP session.
3. Enable **two-way audio** in the integration options.
4. Enable **Settings → System → Labs → Two-way audio** and open HA over HTTPS.
   go2rtc is only HA’s WebRTC provider; no extra go2rtc source is needed.

The **Start talking** button appears in the camera entity’s more-info dialog;
the existing `custom:webrtc-camera` card receives audio but does not send the
microphone.

The `two_way_audio` option is **off by default** and still experimental.
Visitor audio is verified live. The microphone → gate speaker path uses the
same routed talk channel and the RTSP backchannel, but an audible speaker test
is still required. Keep it off until you test the gate speaker.

## Bridge

The bridge is documented in [`docs/STREAM.md`](docs/STREAM.md). It enforces:

* one QVII session at a time (the doorbell allows only one);
* lazy open/close based on viewers;
* a hard viewing cap and black-frame recovery.

The integration can use a bridge on the same host (`127.0.0.1`) or another
machine; just set the RTSP URL accordingly.

## Reverse engineering

* [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — `/tdkcgi`, auth, relays, cloud flow.
* [`docs/STREAM.md`](docs/STREAM.md) — QVII handshake, AES key, RTSP bridge.
* [`tools/`](tools/) — doorbell discovery, CGI probe, audio probe, QR decode.

## Safety

* Door relays are physical outputs; only press the buttons deliberately.
* The doorbell allows **one concurrent video stream**.
* Keep the out-auth-code out of public configs and screenshots.
* This is an unofficial integration, not affiliated with Vimar or Qualvision.
