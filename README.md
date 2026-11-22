<p align="center">
  <img src="assets/icon.png" width="104" alt="Vimar VIEW Door">
</p>

<h1 align="center">Vimar VIEW Door</h1>

<p align="center">Your doorbell, in Home Assistant.</p>

A local-first Home Assistant integration for Vimar VIEW Door / Qualvision QVII
doorbells: live video, gate relays, optional two-way audio and doorbell-ring
automations, with no cloud dependency for watching or opening.

## What it does

- **Live camera** — the doorbell video in Home Assistant, dashboards and the
  companion app.
- **Gate relays** — both physical outputs as buttons.
- **Two-way audio** — the native Home Assistant *Start talking* control, when
  the bridge and browser support it.
- **Doorbell rings** — start any automation when someone presses the button:
  notifications, lights, TTS, snapshots, routines.
- **Automatic recovery** — the bridge refreshes a rotated stream key by itself
  when the cloud account is configured.

## Get started

1. Start the [RTSP bridge](docs/bridge.md) on the Home Assistant host or
   another machine on the same LAN.
2. Install the integration and add the doorbell — see
   [Installation](docs/installation.md).
3. Open the camera, test the relays and optionally enable
   [two-way audio](docs/usage.md#two-way-audio).
4. Set up [doorbell-ring automations](docs/ring.md) if you want Home Assistant
   to react when someone rings.

## Documentation

- [Installation](docs/installation.md) — integration, bridge and credentials
- [Using the integration](docs/usage.md) — camera, relays, audio and updates
- [RTSP bridge](docs/bridge.md) — run the bridge and configure the stream
- [Doorbell rings](docs/ring.md) — webhook and automation examples
- [Troubleshooting](docs/troubleshooting.md) — common problems and diagnostics
- [Internal docs](docs/internal/README.md) — protocol, architecture and
  developer notes

## Status

Tested on the Elvox `IDS9457W` with Home Assistant 2026.11. The camera, relays
and visitor audio are working; the native two-way audio is experimental and
depends on Home Assistant Labs and HTTPS. Ring detection uses the Vimar cloud
call journal and needs a valid Vimar app account.

The doorbell accepts one video stream at a time. The relay buttons control
physical outputs: use them deliberately.

This is an independent project, not affiliated with Vimar, Elvox or Qualvision.
