# RTSP bridge

The bridge connects the doorbell to Home Assistant. It opens a QVII session to
the doorbell and publishes the video, visitor audio and microphone backchannel
as an RTSP stream.

In normal installations the integration starts and manages the bridge
internally; you do not need this page or a separate container. The manual
deployment below is for advanced users and legacy external-bridge setups.

The integration does not talk QVII directly. It only needs the bridge's RTSP
URL.

## Managed bridge ports

The integration gives the bridge a block of five consecutive TCP ports inside
the Home Assistant container, starting at the RTSP port (default **8556**):

| Port | Use |
|------|-----|
| base | RTSP listener (`rtsp://127.0.0.1:<base>/live`) |
| base + 1 | loopback RTSP server behind the front proxy |
| base + 2 | JSON status endpoint |
| base + 4 | talk backchannel |

If the block is taken — another add-on, a leftover process, a second
doorbell — the integration walks forward one port at a time and uses the
first free block (up to ten candidates). A conflict never blocks setup. The
URL actually in use is logged at `INFO` as
`Bridge RTSP URL for <name>: …` and is what the camera serves to Home
Assistant.

To start from a different port, use the integration options and change **Bridge
RTSP port**. That value is only a preference: when it is busy the bridge moves
up from there again, and the options form keeps the stored stream URL in sync
with the port you entered.

## What you need

- One host on the same LAN as the doorbell. It can be the Home Assistant host
  or another computer, Raspberry Pi or NAS.
- The bridge files from this repository:
  - `vimar_bridge.py`
  - `vimar_route.py`
  - `vimar_talk.py`
  - `vimar_cloud.py` (automatic stream-key refresh)
  - `vimar_ring.py` (optional doorbell-ring events)
- A `stream_credentials.json` file containing the doorbell's `device_id`,
  `stream_key` and `auth_code`.
- Optional: a `cloud-account.json` file for automatic key refresh and ring
  events.

Keep both credential files private. They should be readable only by the user
running the bridge.

## Docker Compose example

Create a directory for the bridge, copy the files above into it, and put the
two JSON credential files next to them. Then create a `compose.yaml` like
this:

```yaml
services:
  vimar-stream:
    image: python:3.12-slim
    container_name: vimar-stream
    restart: unless-stopped
    network_mode: host
    command:
      - sh
      - -c
      - |
        set -e
        python -m pip install -q --no-cache-dir quii-helper
        exec python /config/vimar/vimar_bridge.py
    environment:
      CREDENTIALS: /config/vimar/stream_credentials.json
      CLOUD_ACCOUNT: /config/vimar/cloud-account.json
      CAMERA_DEVICE_HOST: 192.168.1.5
      CAMERA_STREAM_PORT: "34567"
      RTSP_PORT: "8556"
      STATUS_PORT: "8558"
      AUDIO: "1"
      TALK: "1"
      KEY_REFRESH: "1"
      PYTHONUNBUFFERED: "1"
    volumes:
      - ./vimar:/config/vimar:ro
```

The paths on the left of `volumes` are relative to the compose file. If the
files live somewhere else, change that path.

Start it with:

```bash
docker compose up -d
```

Check that it is running and has opened the doorbell session:

```bash
curl -s http://127.0.0.1:8558/status
```

The status includes `session`, `clients`, `audio_frames`, `talk` and, when
ring detection is enabled, `ring`.

## Credentials

`stream_credentials.json` has this shape:

```json
{
  "device_id": "<doorbell UID>",
  "stream_key": "<32-byte stream key>",
  "auth_code": "<64-hex out-auth-code>"
}
```

If you already use the bridge, keep your existing file. The bridge refreshes
the stream key automatically when the cloud account is available; see
[Troubleshooting](troubleshooting.md#video-stops-working).

`cloud-account.json` is the Vimar app account used for stream-key refresh and
doorbell rings. It is optional for watching video and using the relays. Do
not reuse the doorbell auth code as the cloud account password.

## Audio

The bridge starts with visitor audio and the talk backchannel enabled by
default. Home Assistant only uses the microphone path when the integration
option **Two-way audio** is enabled and the browser has microphone permission.

Read [Using the integration](usage.md#two-way-audio) before enabling the
microphone; the speaker is a physical output at the gate.

## Ring events (external bridge only)

A managed bridge fires `vimar_door_ring` automatically. For a legacy external
bridge, set `RING_WATCH: "1"` and a webhook URL to receive doorbell rings:

```yaml
      RING_WATCH: "1"
      RING_WEBHOOK_URL: "http://127.0.0.1:8123/api/webhook/<webhook-id>"
      RING_POLL_SECONDS: "5"
```

Then follow [Doorbell ring](ring.md) to create the Home Assistant automation.

## One stream at a time

The doorbell accepts only one QVII video stream. The bridge serialises
viewers, closes idle sessions and reopens when a viewer returns. Do not run a
second bridge or point another integration at the same doorbell.

## Updating

Stop the bridge, replace the Python files with the newer versions, then start
it again. Your credential files do not need to change unless the doorbell was
re-paired or the cloud account was renewed.

## Advanced reference

The complete environment table, internal ports and implementation details are
in the [internal bridge reference](internal/bridge.md). Most users do not need
them.
