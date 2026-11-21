# Getting the video stream without the emulator

Short answer: **yes** — the Android emulator was only needed to bootstrap cloud
credentials from the app UI. Everything it did is a plain HTTPS flow we can
reproduce from any machine.

The missing piece is **not** tooling, it is the 32-byte **stream key**.

---

## 1. What was verified live (2026-11-14)

Full TCP scan of the doorbell (`192.168.1.50`), all 65535 ports:

| port  | what |
|-------|------|
| 80    | `Qualvision -HTTPServer`, CGI `/tdkcgi` |
| 443   | same over TLS |
| 6987  | QV internal, silent on every preamble |
| 34567 | **QVII media stream** (`SDKConst.DEVICE_DEFAULT_STEAM_PORT`) |

There is still **no RTSP server** and no ONVIF.

## 2. The LAN media handshake (verified)

`quii_helper.protocols.quii.live_packets.build_live_setup_packet()` is the
32-byte `CQUIIStreamBase::OnRecvSetup` packet. The device answers:

```
request : a900000000…0000                       (32 bytes, all zero)
reply   : a9 00 00 00 00 00 00 00 00 00 02 01 …  (32 bytes)
           ^^                          ^^ ^^
           0xA9 envelope ok            |  |  sha_mode = 1  (SHA256)
                                       status   crypto_mode = 2  (AES-256)
                                       0 = ok
```

So the doorbell **accepts** the envelope with only the auth code we already
have, but it forces `crypto_mode = 2`: the play request has to be encrypted
with the device's **32-byte stream key**.

## 3. What was tried and failed

Against a real play (`CQUIIStreamLive::OnSendPlay`, `packet_type=0x01`,
`path=/mode=real&idc=1&ids=<stream>&ap=2`), on one socket
(setup → reply → play), these keys produced **no reply at all** (the device
stays silent instead of rejecting):

```
auth_code hex[:32]  auth_code hex[32:]   sha256(auth_code)
sha256(auth_code) hex[:32]               md5(auth_code)*2
sha256(label_code)  sha256(uid)          uid/label padded
mac without colons  "adminapp2" padded   zero key
base64(auth_code)[:32]                   sha256(sha256(auth_code))
```

A play with `crypto_mode=0` (no key) is silently dropped as well. Conclusion:
the key cannot be derived from anything we already hold — it is issued by the
Qualvision cloud per device.

`get.device.streamkey` (the CGI command that should expose it) answers
`error -1` on this firmware, in both the plain `username` header and the
encrypted `usernametoken` (AES-CBC) variant, over HTTP:80 and HTTPS:443.

## 4. Where the key comes from — and it needs no emulator

```
MyVIMAR email+password
        │  (plain HTML form POST: hash, email, passw)
        ▼
https://oauth.vimar.com/…/authorization-code/authorize
        │  302 redirect with ?code=
        ▼
https://sso.qvcloud.net/vimar/callback        -> {"account":"[vimar]…","password":"<64 hex>"}
        │
        ▼
https://oauth2r1.qvcloud.net/qvoauthv2/token  -> access_token / refresh_token
        │  client_id = 00<client_type>-<app_id>-<client_id>
        ▼
POST https://r1-2-sec.qvcloud.net/auth/user
        ├─ command=login              -> <session><id>
        └─ command=get-device-token   -> <stream-key> (32 bytes) + <out-auth-code>
```

Verified from this machine **without any Android device**:

* `GET https://sso.qvcloud.net/vimar/usrpwd/authorize?oem=G0078&client_id=…&rgn=1`
  → `302` to the MyVIMAR login form (we can POST `hash`/`email`/`passw` to it).
* `GET https://oauth2r1.qvcloud.net/qvoauthv2/token` with a **self-generated**
  `client_id` → vendor error `-20 password error`, i.e. the client id was
  accepted. The app's registered id (`003-2078-<android_id>`) is therefore not
  a secret we must harvest from the APK.

So the emulator is replaceable by ~40 lines of `requests`. What cannot be
replaced is the **MyVIMAR account password**.

## 5. QVII → RTSP

The protocol on 34567 is fully implemented — no need to write it from scratch —
by [`quii-helper`](https://github.com/xaer981/quii-helper) (MIT, Python,
ARM64 wheels available), which is exactly this vendor/platform and is
**community-confirmed for LAN RTSP on a Vimar device** (`K40955 / vEye Pro`).

```bash
python -m pip install quii-helper
```

```dotenv
CAMERA_CONNECTION_MODE=lan
CAMERA_DEVICE_HOST=192.168.1.50
CAMERA_STREAM_PORT=34567
CAMERA_CHANNEL=1
CAMERA_STREAM=low          # high = main stream
```

```python
from quii_helper import Camera, CameraStreamCredentials, load_config

saved = CameraStreamCredentials.load("data/stream_credentials.json")
camera = Camera(
    config=load_config(),
    connection_mode="lan",
    device_id=saved.device_id,
    stream_key=saved.stream_key,
    auth_code=saved.auth_code,
)
with camera.serve_rtsp(port=8554) as session:
    session.wait()
```

Result: `rtsp://127.0.0.1:8554/live` (unauthenticated, loopback only — never
expose it). The same library also renders JPEG/MP4 and does two-way audio.

## 6. Delivery into Home Assistant

The HA host (`<HA_HOST>`) is `aarch64`, which `quii-helper` supports. The clean
wiring is next to the existing `go2rtc` service in the `homeassistant` stack:

```yaml
  vimar-stream:
    image: python:3.12-slim
    restart: unless-stopped
    network_mode: host
    command: >
      sh -c "pip install --no-cache-dir quii-helper &&
             quii-helper rtsp --credentials /data/stream_credentials.json"
    volumes:
      - /opt/homeassistant/vimar:/data:ro
```

then add it to `go2rtc.yaml` like the other four cameras:

```yaml
  cancelletto: rtsp://127.0.0.1:8554/live
```

and the camera shows up in **Casa → Telecamere** with the WebRTC card.

## Stream-key rotation

The 32-byte `data-encode-key` is **not** the same as the `/tdkcgi`
`out-auth-code`, and it can rotate when the cloud token is renewed, the
device is re-paired, or the app re-authenticates.  Symptom:

- the Vimar app still shows live video;
- the bridge opens TCP 34567 and the QVII setup succeeds;
- the play receives **no reply at all** (not even a rejection);
- no media frames arrive.

Fetch a fresh key (the account file is local and gitignored):

```bash
python tools/refresh_credentials.py   --account-json work/cloud-account.json   --out work/stream_credentials.json
```

The tool logs in, calls `get-device-token`, updates `stream_key` (and
`auth_code` if it changed), and prints only booleans/lengths.  Restart the
bridge (or wait for its next lazy session) after updating the live
`/config/vimar/stream_credentials.json`.

The cloud record also carries `password-expired`; the current key tends to be
valid for that window.  If video stops after a renewal, run the tool again.

## 7. What is still missing

| item | state |
|------|-------|
| `out-auth-code` (CGI + door relays) | ✅ have it, working |
| device is reachable, QVII listens | ✅ verified |
| handshake accepted, crypto profile | ✅ AES-256 / SHA256 |
| **32-byte stream key** | ⛔ needs the MyVIMAR account password (or a dump of the phone app) |
| QVII → RTSP implementation | ✅ `quii-helper` |

Two ways forward:

1. **MyVIMAR login from here** — give the account e-mail + password once, we run
   the SSO → `get-device-token` flow, save `stream_credentials.json`, and the
   stream never needs the emulator again.
2. **Copy the key out of the phone app** — with the app installed on a rooted
   device (or a reused emulator snapshot), the 32-byte key sits in the app's
   saved device record next to the auth code. Slower, no password shared.


---

## 8. The deployed bridge: one QVII session, lazy, fan-out

The doorbell accepts **one** concurrent QVII stream. The naïve bridge held a
session 24/7, so the Vimar app could never view and a reconnect could overlap
with the old session (black frames).

Deployed design (`homeassistant/vimar_bridge.py`, running as the
`vimar-stream` container):

```
go2rtc / VLC / any RTSP client
        |  N TCP connections (N viewers)
        v
  front :8556  (proxy + viewer counter)
        |  N loopback RTSP sessions
        v
  inner :8557  (quii-helper RtspH264Server, max 32 clients)
        |  exactly ONE QVII session
        v
  192.168.1.50:34567
```

Rules enforced by the supervisor:

* **one upstream** — a lock + `stopping` gate; a new session can only start
  after the previous one has closed and a cooldown has passed;
* **lazy** — the QVII session starts on the first viewer and closes 12 s after
  the last one leaves (configurable: `LINGER_SECONDS`);
* **no overlap** — the cooldown runs while the gate is closed, so the doorbell
  never sees two sessions;
* **fan-out** — any number of RTSP clients share the one session
  (`inner_clients` in the status);
* **restart on failure** — a dead session or >12 s without video triggers a
  clean close/reopen (`starts`/`stops` counters).

Status (loopback only):

```bash
curl http://127.0.0.1:8558/status
```

Verified 2026-11-14:

| test | result |
|------|--------|
| 4 viewers at once | `clients=4`, `starts=1` → one doorbell session |
| all viewers gone | after 12 s: `session=false`, `last_reason=idle` |
| cold frame via go2rtc | HTTP 200, 23 045 bytes, valid JPEG |
| cold start | `starts` +1, then back to idle (`stops` +1) |

Note: go2rtc keeps its producer while it has WebRTC consumers. Stale
consumers (old dashboard reloads) can keep the producer alive; a go2rtc
restart clears them, after which the bridge closes the doorbell session.

### Dashboard card

The Casa -> Cancelletto view uses `webrtc-camera` with:

```yaml
poster: /local/cancelletto-poster.jpg   # neutral placeholder (replace with your own)
# background: true  <- removed: it started the stream even when the card was
#                      not visible, defeating the idle close
```

The repo ships a neutral 1100x900 (11:9) placeholder, not a frame from the
real installation. It is shown instantly while go2rtc/bridge connect; the
card's own `ha-circular-progress` spinner appears on the video `waiting` event.
When the stream plays, the video covers the poster. For your own instance,
generate a poster from your camera frame with
`homeassistant/make_blur_poster.py frame.jpg cancelletto-poster.jpg`.

go2rtc timeout note: the front proxy connects go2rtc to the inner RTSP server
immediately; the inner server answers `503 Service Unavailable` + `Retry-After: 1`
until SPS/PPS exist. Earlier the front held the connection until the first
keyframe (up to 20 s), which made go2rtc's WebRTC offer fail with
`read tcp 127.0.0.1:...->127.0.0.1:8556: i/o timeout`.

Disabled screen: while blocked, the bridge still serves the same RTSP URL but
through a local `RtspH264Server` that loops a pre-rendered H.264 frame — a
neutral **"STREAM DISATTIVATO"** screen — so the HA card shows a proper screen
instead of freezing on the last frame. No doorbell traffic. Source image
`homeassistant/cancelletto-disabled.jpg` (placeholder; replace it with your own),
encoded to `/config/vimar/disabled.h264` (ffmpeg, 1 frame IDR, repeated at 1 fps).

Hard viewing cap: a viewer can hold the doorbell for at most
`MAX_SESSION_SECONDS` (default **60**) of continuous video. At the cap the
session is closed and new sessions are refused until viewers have been gone
for `UNBLOCK_IDLE_SECONDS` (default 15). This keeps a forgotten dashboard tab
from blocking the doorbell call button. Implemented in the bridge, so it works
even if the browser never closes the card. Verified with a 5 s cap: blocked at
8 s, session closed, unblocked 15 s after the client left.

Black-frame recovery: the doorbell can go black on a long-lived session (the
device blanks it, or the Vimar app takes the single stream). Every 10 s, while
viewers are connected, the bridge asks go2rtc for a frame; two consecutive
frames under 2 KB (the black JPEG is ~800 B, a real one ~24 KB) trigger a clean
session cycle. Recovery takes ~20-30 s, no restart of the container needed.

Error handling: if the doorbell refuses a reconnect (`LAN TCP startup failed`,
typical after several rapid open/close cycles or while the Vimar app is
viewing), the supervisor backs off 5 s, 10 s, 20 s, 40 s (reset after 30 s of
healthy video) instead of retrying every 3 s.

### Audio (checked 2026-11-20)

**The doorbell does not send audio on an idle LAN preview.**  A raw 60-second
QVII capture on channel 1, streams 1 and 2, contains only `E0`/`E1` video
frames: **zero `E3` audio frames**.  The same result holds during a talk
handshake (codec 4 / A-law negotiated, `ready` ACK received, zero media on
either socket).  `get.audio.*` CGI commands return `-1` on this firmware.

The APK confirms where audio *should* be: `liblive_player.so` decodes `E3`
G.711 frames from the same QVII stream, and `audioToggleEx` only calls
`QVPlayerListen` / `QVTalkListen` (local playback device), never a remote
"enable audio" command.  So there is nothing missing on the client side; the
firmware simply does not put audio in the preview while no call is active.
The likely place to see it is an **active doorbell call** (visitor pressed the
button).  A 2026-11-20 test could not produce one remotely.

The bridge now advertises the audio direction unconditionally:

* `AlwaysAudioRtspServer` adds `m=audio 0 RTP/AVP 8` (PCMA/G.711, 8 kHz mono)
  from the first DESCRIBE and answers immediately (the stock quii-helper
  client waits 5 s for video and answers `503`, which made the lazy session
  race its own cold start).
* `E3` A-law frames are forwarded as-is; mu-law (`codec 5`) and PCM16
  (`codec 9`) are transcoded to A-law so the advertised format never changes.
* Before the first `E3` frame the track is silent; when the doorbell sends
  audio (typically during a call), existing viewers hear it without a new
  DESCRIBE.
* `AUDIO=0` disables the track.  `/status` exposes `audio_enabled`, `audio`,
  `audio_format`, `audio_frames`, `audio_error`.

Talk runs in the same process: the RTSP server advertises a `sendonly` PCMA
track (`trackID=2`).  go2rtc/HA opens a second RTSP session to the bridge,
sends RTP, and the bridge forwards the PCMA payload to `vimar_talk.py` on
`127.0.0.1:8560`, which feeds `LanTalkSession`.  No doorbell talk connection is
opened until the microphone starts.  No extra go2rtc source is needed; go2rtc
is only HA's WebRTC provider.

Capture tool (stop the bridge first — one stream):

```bash
python tools/audio_probe.py --credentials work/stream_credentials.json --seconds 30
```

Exit code `2` means "connected, but no E3 audio", i.e. the current video-only
state.  Run it while someone rings the doorbell / while a call is active.  If
frames appear it writes a 8 kHz mono PCM WAV (`work/doorbell.wav`).

Two-way audio is wired through HA 2026.11's camera capability + go2rtc
backchannel.  The talk handshake works, but the real speaker path has not been
tested on the gate yet; see `homeassistant/README.md`.
