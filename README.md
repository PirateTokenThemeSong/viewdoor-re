<img src="assets/icon.svg" width="64" height="64" alt="">

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
│ button entities      │                └─────────────────────┘                 └──────────┘
│ go2rtc (WebRTC)      │   HTTP :80 /tdkcgi ───────────────────────────────────►
└──────────────────────┘
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

This integration provides step 1. Steps 2–4 are already available in the HA
instance. The missing piece is the **backchannel** on the bridge side: an
RTSP/exec audio path that receives PCMU/PCMA from go2rtc and forwards it to
`quii-helper`’s `LanTalkSession` → doorbell speaker.

The `two_way_audio` option is therefore **off by default** and marked
experimental. Enabling it before the talk path exists makes HA show a button
that cannot deliver audio.

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
