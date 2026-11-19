<img src="assets/icon.svg" width="64" height="64" alt="">

# viewdoor-re

Reverse engineering of **Vimar VIEW Door V2.2.11** (`com.vimar.viewdoor`), a
white-label of the **Qualvision** ("QvCbb" / TDK-cloud) doorbell platform
(Elvox `IDS9457W`, `IDS9470QW`, `IDS9475W`, `IDS9483QW`).

Goal: get a **video stream** and a **local API for the two door-open relays**.

* Protocol write-up: [docs/PROTOCOL.md](docs/PROTOCOL.md)
* Video stream, without the emulator: [docs/STREAM.md](docs/STREAM.md)
* Tooling: `tools/`

## TL;DR of the findings

| item | result |
|------|--------|
| Local control API | `POST http(s)://<doorbell>/tdkcgi`, XML envelope |
| Auth | `username=adminapp2`, `password=sha256(device verification code)`, `passwordencode=1` (+ optional HTTP Digest) |
| Door relays | `set.device.opendoor` → `<door>channel</door><locknumber>1|2</locknumber>` |
| Relay list | `get.device.attachInfo` (the two buttons) |
| RTSP | **not listening** (554 closed, all 65k ports checked). Video is the proprietary Qualvision QVII stream on **TCP 34567** |
| Audio | ⚠️ **not sent by this firmware in idle LAN preview** (0 E3 frames in 60 s, streams 1/2). The bridge now auto-enables G.711 if the doorbell ever sends it — probe with `tools/audio_probe.py` |
| LAN media handshake | ✅ accepted (`0xA9`, status 0), device forces **AES-256 / SHA256** (`crypto_mode=2`) |
| Stream key | ⛔ the 32-byte AES key lives in the cloud; `get.device.streamkey` returns `-1` on this firmware — [how to get it](docs/STREAM.md) |
| RTSP config | firmware exposes `<rtspport>` / `<rtspurl>` in `get.network.config` (needs the verification code to read/toggle) |
| ONVIF | SDK supports it (`get.onvif.pwd` / `set.onvif.pwd`), but no ONVIF service runs by default |
| Cloud | MyVimar SSO → Qualvision OAuth2 (`vimar.qvcloud.net`) works; LAN CGI does not need it |

## Tooling

```bash
# locate the doorbell on the LAN (HTTP "Server: Qualvision -HTTPServer" + port 34567)
python tools/find_doorbell.py
python tools/find_doorbell.py --net 192.168.1.0/24

# authenticate and dump model / network / RTSP fields / relays
python tools/viewdoor.py probe  --host 192.168.1.50 --code <VERIFICATION_CODE>

# list the lock outputs ("Door 1", "Door 2")
python tools/viewdoor.py doors  --host 192.168.1.50 --code <VERIFICATION_CODE>

# open relay 1 (channel 0) / relay 2
python tools/viewdoor.py open 1 --host 192.168.1.50 --code <VERIFICATION_CODE>
python tools/viewdoor.py open 2 --host 192.168.1.50 --code <VERIFICATION_CODE>

# test the door-open password without opening anything
python tools/viewdoor.py check-password 1234 --host 192.168.1.50 --code <CODE>

# try to locate an RTSP stream (one DESCRIBE at a time, single-stream device)
python tools/viewdoor.py rtsp --host 192.168.1.50 --code <VERIFICATION_CODE>

# arbitrary command
python tools/viewdoor.py raw get.system.ability --host 192.168.1.50 --code <CODE>

# if you photograph the QR sticker on the doorbell, decode UID + code from it
python tools/decode_qr.py label.jpg
```

Useful flags: `--scheme http|https|auto`, `--port`, `--user`, `--security`,
`--debug`, `--hash on|off` (door password hashing, default `on`).

`<VERIFICATION_CODE>` is the password printed **below the QR code on the
device label** (8 uppercase letters, or 4–16 digits). The app's own hint says:
*"input UID (12 letters and numbers) and password: 4–16 numbers (if defined)
or 8 uppercase letters, usually you can find it below QR code"*. It is also
what the LAN "add device" dialog asks for. The QR itself encodes
`<ap> <uid> <code> <model>` (V1) or `{"v":2,"u":…,"c":…}` (V2).

## How the token was obtained

The `/tdkcgi` password is the device's **`out-auth-code`** (64 hex), not
`sha256(sticker code)`.

Reproduced end-to-end:

1. Installed the APK in an **x86_64 API 30 Google APIs** emulator (it has ARM
   translation, `libndk_translation.so`; API 33 x86 images do not).
2. Logged in with the MyVimar account in the app (WebView SSO).
3. The app's `shared_prefs/save.xml` then contained the region‑1 endpoints and
   the app's registered client id (`calc`).
4. Used that client id to log in to `https://r1-2-sec.qvcloud.net/auth/user`
   (`command=login` → session) and called `command=get-device-list`, which
   returns `<out-auth-code>` for the doorbell.
5. Verified against the device: `get.product.info` → `error 0`.

Bonus: `get.device.qrcode` returns the label payload
`<ap-name> <uid> <sticker-code> <model> <mac>`.

## Status

* ✅ Local CGI auth + relay command (`set.device.opendoor`) implemented.
* ✅ Token recovered and verified; no door command has been fired yet.
* ✅ `get.product.info`, `get.device.qrcode`, `get.device.status`,
  `get.product.time`, `get.tfcard.info`, upgrade* work on this firmware.
* ⛔ `get.network.config`, `get.system.ability`, `get.device.attachInfo`,
  `get.onvif.pwd` return `-1` (not implemented on the VDP firmware).
* ⛔ The doorbell has **no RTSP server** (554 closed, also while streaming) and
  no ONVIF. Video is the proprietary QVII stream on TCP 34567.

## Notes / safety

* The doorbell allows **one concurrent video stream**; the RTSP probe is
  serial on purpose.
* Door relays are physical outputs — do not fire them at random.
* `work/` (extracted APK, jadx output, scratch scripts) is git-ignored.
