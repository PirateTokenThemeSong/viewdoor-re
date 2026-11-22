# Vimar VIEW Door for Home Assistant

An unofficial Home Assistant integration for **Vimar VIEW Door** doorbells
based on the Qualvision QVII platform, including the Elvox `IDS9457W`.

It brings the doorbell into Home Assistant as a normal camera device with
the gate relays, optional two-way audio, and a trigger for automations when
someone rings.

## What it provides

- **Live camera** in Home Assistant, dashboards and the companion app.
- **Two gate relays** as buttons.
- **Optional two-way audio** through Home Assistant's native *Start talking*
  control.
- **Doorbell ring events** that can start any Home Assistant automation:
  notifications, lights, TTS, snapshots, presence routines and more.
- **Automatic recovery** when the doorbell rotates its stream key.

## Requirements

- Home Assistant **2026.11** or newer.
- A Vimar VIEW Door / QVII doorbell on the same network as Home Assistant.
- The small **RTSP bridge** that connects the doorbell to Home Assistant.
- The doorbell's authentication code.
- Optionally, the Vimar app account for ring events and automatic stream-key
  recovery.

The bridge can run on the Home Assistant host or on another machine on the
same LAN.

## Documentation

The [user documentation](docs/README.md) explains installation, daily use and
troubleshooting:

- [Installation](docs/installation.md)
- [RTSP bridge](docs/bridge.md)
- [Using the integration](docs/usage.md)
- [Doorbell ring automations](docs/ring.md)
- [Troubleshooting](docs/troubleshooting.md)

Technical reference for developers — protocol, bridge internals, test
procedures and reverse-engineering notes — is in
[docs/internal](docs/internal/README.md).

## Status

The camera, both relays, visitor audio and the microphone/backchannel are
implemented and have been tested on the `IDS9457W`. The native two-way audio
control depends on Home Assistant's experimental *Two-way audio* Labs feature
and on HTTPS. The microphone-to-gate-speaker path should still be checked
physically before relying on it.

Doorbell ring detection uses the same cloud call journal as the Vimar app.
It needs a valid Vimar app account; it is not a local button event from the
video stream.

## Safety

- The relays are physical outputs. Only press them deliberately.
- The doorbell accepts only one QVII video stream at a time.
- Keep authentication codes, cloud account files and webhook IDs private.
- This is an independent project, not affiliated with Vimar, Elvox or
  Qualvision.
