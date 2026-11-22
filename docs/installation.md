# Installation

This guide installs the Home Assistant integration and connects it to the
RTSP bridge. If the bridge is already running, skip to
[Install the integration](#install-the-integration).

## Before you start

You need:

- Home Assistant **2026.11** or newer.
- A Vimar VIEW Door / QVII doorbell on the LAN.
- The doorbell's **authentication code** (out-auth-code, or the label code
  printed near the QR sticker).
- A host for the RTSP bridge — the Home Assistant host itself is fine.
- Optional for ring automations: a Vimar app account. See
  [Doorbell ring](ring.md).

The doorbell accepts only one QVII video stream at a time. The bridge manages
that limit; do not point another integration or a second bridge at the same
doorbell.

## Install the RTSP bridge

The bridge is the component that speaks to the doorbell and publishes an RTSP
stream. Follow the [RTSP bridge guide](bridge.md) to start it and confirm that
this address works from Home Assistant:

```text
rtsp://<bridge-host>:8556/live
```

If the bridge runs on the Home Assistant host, the usual address is
`rtsp://127.0.0.1:8556/live`.

## Install the integration

### Manual installation

1. Copy the `custom_components/vimar_door` folder from this repository into
   your Home Assistant `config/custom_components/` directory.
2. If you previously used the old YAML package, remove it first so the
   `button.cancelletto` entity is not created twice.
3. Restart Home Assistant.

### HACS

Add this repository as a custom **Integration** repository in HACS and install
the latest release. If HACS cannot install it, use the manual installation
above.

## Add the doorbell

1. Open **Settings → Devices & services**.
2. Choose **Add integration** and search for **Vimar VIEW Door**.
3. Fill in the form.

| Field | Example | Notes |
|-------|---------|-------|
| Host | `192.168.1.5` | Doorbell IP address or hostname on the LAN |
| Port | `80` | Local `/tdkcgi` HTTP port |
| Auth code | your doorbell code | 64-character out-auth-code, or the label code |
| Name | `Cancelletto` | Name used for the device and entities |
| RTSP stream URL | `rtsp://127.0.0.1:8556/live` | The bridge's RTSP output |
| Two-way audio | off | Turn on only after reading [Using the integration](usage.md#two-way-audio) |

The auth code is stored in the Home Assistant config entry. Do not add it to
`configuration.yaml` or commit it to a public repository.

After saving, Home Assistant creates:

- a camera entity with the live video;
- one button per gate relay.

The entity IDs depend on the name you choose. For the default name
`Cancelletto`, they are normally `camera.cancelletto`,
`button.cancelletto` and `button.cancelletto_relay_1`.

## Enable optional two-way audio

Two-way audio needs all of the following:

1. the bridge running with audio and talk enabled (the defaults);
2. the integration option **Two-way audio** enabled;
3. Home Assistant opened over **HTTPS**;
4. **Settings → System → Labs → Two-way audio** enabled.

Then open the camera's more-info dialog and press **Start talking**. The full
walkthrough is in [Using the integration](usage.md#two-way-audio).

## Set up ring automations

Doorbell rings are read from the Vimar cloud call journal by the bridge and
delivered to Home Assistant through a webhook. This is optional for watching
the camera and using the relays.

See [Doorbell ring](ring.md) for the account requirement, bridge setting and
an automation example.

## Next steps

- [Using the integration](usage.md)
- [Doorbell ring](ring.md)
- [Troubleshooting](troubleshooting.md)
