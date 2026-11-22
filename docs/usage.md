# Using the integration

Once the integration is installed, the doorbell appears as a normal Home
Assistant device. The one-step setup starts its bridge automatically; there
is no separate bridge container to manage.

## Entities

Entity IDs follow the doorbell name reported by the Vimar cloud, or the name
you enter during setup. They are not fixed in the integration.

| Entity | Purpose |
|--------|---------|
| `camera.<name>` | Live video and the two-way-audio dialog |
| `button.<name>` | Relay 2 (normally the pedestrian gate) |
| `button.<name>_relay_1` | Relay 1 (the second output) |

The relay mapping is the doorbell's physical output numbering; check which
gate opens before relying on a button.

## Live video

Open the camera entity, add it to a dashboard with a Picture Glance or camera
card, or use the companion app.

For a camera that supports two-way audio, the more-info dialog is the useful
view: it contains the native **Start talking** control. A plain
`custom:webrtc-camera` card is fine for listening, but its microphone button is
not the same as Home Assistant's native control.

Example dashboard card:

```yaml
type: picture-glance
title: <name>
entities: []
camera_image: camera.<name>
```

The doorbell allows one video stream at a time. If the video is open in the
Vimar app or a second dashboard, Home Assistant may wait or show a stopped
stream until the other viewer lets go. The managed bridge handles this
automatically.

## Gate relays

Press the relay button in Home Assistant to fire the corresponding physical
output. The two relays are separate; do not assume that relay 2 is always the
pedestrian gate on an installation you did not wire yourself.

The relays are physical outputs. Use deliberate taps, not accidental
automations.

## Two-way audio

Two-way audio is the native Home Assistant control, not a custom card button.
It needs:

1. The bridge running with audio and talk enabled (the default).
2. The integration option **Two-way audio** turned on.
3. Home Assistant opened over **HTTPS**.
4. **Settings → System → Labs → Two-way audio** enabled.

Then:

1. Open the camera's more-info dialog.
2. Wait for the live stream to start.
3. Press **Start talking**.
4. Allow microphone access when the browser asks.
5. Press **Stop talking** when finished.

If the button is missing, check the four requirements above. If the bridge or
network blocks the RTSP backchannel, the visitor audio may still work while
the microphone does not.

Visitor audio is part of the RTSP stream. The microphone path sends your voice
to the doorbell speaker; the speaker is a physical output in a public place.
Test the volume and delay before using it in a real conversation.

Starting a talk session can briefly interrupt a video stream or another
viewer. This is a doorbell limitation, not a dashboard bug.

## Doorbell rings

The camera stream itself does not carry the outdoor button press. The managed
bridge reads the same cloud call journal used by the Vimar app and fires the
Home Assistant event `vimar_door_ring`. See [Doorbell ring](ring.md) for the
event payload and automation examples.

A ring automation can do anything Home Assistant can do: send a notification,
turn on lights, play a TTS message, save a camera snapshot or start a script.
The bridge only reports the event; it does not answer the call or open the
gate.

## Automatic stream-key recovery

The doorbell's stream key can rotate. The managed bridge detects a session
that opens but produces no video, fetches a fresh key and retries. This
normally requires no action from you.

If video stops after a re-pairing, see
[Troubleshooting](troubleshooting.md#video-stops-working).

## Bridge ports

The bridge runs inside the Home Assistant container and uses five local TCP
ports starting at **8556**. If they are already taken, the integration moves
to the next free block on its own and logs the URL it settled on; you do not
have to free a port or publish anything. To start from a different port, open
the integration options and change **Bridge RTSP port** — the stream URL is
derived from it, and it moves up again if that port is busy too.

## Updating and removing

To update the integration, replace the `custom_components/vimar_door` folder
with the new version and restart Home Assistant. Your config entry and
credentials are preserved.

To remove the integration, delete the config entry under **Settings → Devices
& services**; the managed bridge stops automatically. Then remove the folder
if you want to uninstall it completely.

## Safety

- The relays operate a real gate or door. Test them only when it is safe.
- The doorbell is a public intercom; do not leave the microphone open.
- Keep auth codes, cloud account files and device keys private.
