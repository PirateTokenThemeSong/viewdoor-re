# Vimar VIEW Door backend

`vimar_bridge.py` is the QVII → RTSP bridge. It now also starts the loopback
PCMA → QVII talk bridge (`vimar_talk.py`), so one process provides both
directions:

```
go2rtc / HA                      bridge container
  RTSP :8556  ────────────────►  vimar_bridge.py ── QVII :34567 ──► doorbell
  mic (RTSP trackID=2) ───────►  vimar_talk.py   ── talk :34567 ──► speaker
                                 (loopback 8560)
```

* **Listen** — the RTSP server always advertises a PCMA 8 kHz mono track.
  E3 frames from the doorbell are forwarded (A-law; mu-law/PCM16 are
  transcoded). Without E3 the track is silent, which is normal in idle
  preview.
* **Talk** — the RTSP server advertises a `sendonly` PCMA track
  (`trackID=2`). When go2rtc/HA starts the microphone it opens a second RTSP
  session, sends RTP to the bridge, and the bridge forwards the payload to
  `vimar_talk.py` on loopback. The talk server groups the bytes into 40 ms
  frames and sends them on `LanTalkSession`.
* **Listen while talking** — the bridge opens a talk channel and sends
  `type 13 disable` (`route_receive_to_video`). The doorbell then sends the
  visitor's voice as **E3 on the video session**, which the bridge publishes
  on its RTSP audio track. This is the app's `QVTalkListen` path.
  `/status` exposes `talk.active`, `talk.route_ready`, `talk.route_error`
  and the E3 `audio_frames`.
* **Microphone** — the RTSP `sendonly` track receives PCMA; the talk server
  forwards it to the routed talk channel as 512-sample A-law blocks. The
  speaker test still needs care.

## Run

```bash
python -m pip install quii-helper
CREDENTIALS=/config/vimar/stream_credentials.json \
CAMERA_DEVICE_HOST=192.168.1.50 \
python vimar_bridge.py
```

Environment:

| variable | default | meaning |
|----------|---------|---------|
| `CREDENTIALS` | `/config/vimar/stream_credentials.json` | device id + stream key + auth code |
| `RTSP_PORT` | `8556` | front RTSP listener |
| `INNER_PORT` | `8557` | loopback quii-helper RTSP server |
| `AUDIO` | `1` | always advertise/fill the PCMA track |
| `TALK` | `1` | start the loopback talk server |
| `TALK_PORT` | `8560` | loopback PCMA input |
| `CLOUD_ACCOUNT` | `/config/vimar/cloud-account.json` | account JSON for automatic key refresh |
| `KEY_REFRESH` | `1` | enable automatic stream-key refresh |
| `KEY_REFRESH_AFTER` | `1` | consecutive silent sessions before refreshing |
| `KEY_REFRESH_COOLDOWN` | `600` | seconds between cloud refreshes |
| `CAMERA_DEVICE_HOST` | `192.168.1.50` | doorbell IP |

## go2rtc

No extra source is needed. go2rtc’s RTSP client sees the sendonly track in
the bridge SDP and opens the backchannel session itself; it is only used as
Home Assistant’s WebRTC provider:

```yaml
streams:
  cancelletto: rtsp://127.0.0.1:8556/live
```

## Home Assistant two-way audio

1. Run this bridge with `AUDIO=1` and `TALK=1`.
2. Keep the integration stream URL at `rtsp://127.0.0.1:8556/live`.
3. Enable **two-way audio** in the integration options.
4. Enable **Settings → System → Labs → Two-way audio** and open HA over HTTPS.

The “Start talking” button appears in the camera entity's more-info dialog.
The existing `custom:webrtc-camera` card only receives audio; it does not send
the microphone.

## If video stops but the app works

The 32-byte stream key rotated.  The bridge now recovers automatically when
`/config/vimar/cloud-account.json` exists:

* a session that opens but never produces video is closed after `START_TIMEOUT`
  (20 s);
* after `KEY_REFRESH_AFTER` (default 1) consecutive failures it calls
  `get-device-token`, updates the credentials and retries;
* if the config directory is mounted read-only it writes the fresh copy to
  `/tmp/vimar_credentials.json` and uses that for both video and talk;
* `/status` reports `credentials_path` and `key_refresh` (`last`, `error`).

Manual refresh is still available:

```bash
python tools/refresh_credentials.py   --account-json work/cloud-account.json   --out work/stream_credentials.json
```

The cloud account file is secret; keep it mode 600 and out of the repo.

## Safety

* One QVII video stream at a time; talk is a separate TCP connection.
* Do not enable the talk path until you can hear the doorbell speaker: it is
  a physical output in a public space.
* The talk server binds loopback only; do not expose `8560` to the LAN.
