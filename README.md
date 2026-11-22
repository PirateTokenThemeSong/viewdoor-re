<p align="center">
  <img src="assets/icon.png" width="104" alt="Vimar VIEW Door">
</p>

<h1 align="center">Vimar VIEW Door</h1>

<p align="center">Your doorbell, in Home Assistant.</p>

A one-step Home Assistant integration for Vimar VIEW Door / Qualvision QVII
doorbells: live video, gate relays, optional two-way audio and doorbell-ring
automations. The integration signs in with your Vimar account, creates the
local credentials and runs its bridge inside the Home Assistant container —
no separate bridge container or manual auth-code setup.

## What it does

- **Live camera** — the doorbell video in Home Assistant, dashboards and the
  companion app.
- **Gate relays** — both physical outputs as buttons.
- **Two-way audio** — the native Home Assistant *Start talking* control, when
  the bridge and browser support it.
- **Doorbell rings** — start any automation when someone presses the button:
  notifications, lights, TTS, snapshots, routines.
- **Automatic recovery** — the bridge refreshes a rotated stream key by itself.

## Get started

1. Install the custom integration from this repository or HACS — see
   [Installation](docs/installation.md).
2. Add **Vimar VIEW Door** and enter your Vimar email, password and the
   doorbell IP. The integration discovers the device and starts its bridge
   automatically.
3. Open the camera, test the relays and optionally enable
   [two-way audio](docs/usage.md#two-way-audio).
4. Set up [doorbell-ring automations](docs/ring.md) if you want Home Assistant
   to react when someone rings.

## Documentation

- [Installation](docs/installation.md) — one-step integration setup
- [Using the integration](docs/usage.md) — camera, relays, audio and updates
- [RTSP bridge](docs/bridge.md) — how the managed bridge works; advanced external setup
- [Doorbell rings](docs/ring.md) — `vimar_door_ring` event and automation examples
- [Troubleshooting](docs/troubleshooting.md) — common problems and diagnostics
- [Internal docs](docs/internal/README.md) — protocol, architecture and developer notes

## Status

Tested on the Elvox `IDS9457W` with Home Assistant 2026.11. The camera, relays
and visitor audio are working; the native two-way audio is experimental and
depends on Home Assistant Labs and HTTPS. Ring detection uses the Vimar cloud
call journal and needs a valid Vimar app account.

The doorbell accepts one video stream at a time. The relay buttons control
physical outputs: use them deliberately.

This is an independent project, not affiliated with Vimar, Elvox or Qualvision.
