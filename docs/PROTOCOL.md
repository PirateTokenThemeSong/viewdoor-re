# Vimar VIEW Door — protocol notes

Reverse engineered from `Vimar VIEW Door V2.2.11` (`com.vimar.viewdoor`,
a.k.a. Qualvision / "QvCbb" SDK, package `com.quvii.*`).

This document describes the **local (LAN) control API** of the doorbell and
what is (and is not) available for video.

---

## 1. What the device is

* Vimar white-labels a Qualvision (Shanghai) platform. Confirmed by:
  * `Server: Qualvision -HTTPServer` on the device
  * TLS client cert `O=QUALVISION TECHNOLOGY CO.,LTD`
  * cloud host `vimar.qvcloud.net`, OEM id `G0078`, app id `2078`
  * SDK string `QvCbb_1.1.2`, `liblive_player.so`, `libqv-p2p-v2.so`
* Device categories in `assets/config/device_list.json`:
  * `VDP` (video door phone) → `IDS9457W`, `IDS9470QW`, `IDS9475W`, `IDS9483QW`
  * `BOX` → `IDS7930`, `IDS7938`
  * `NVR` / `UVR` / `VR` / `IOT` for the rest of the Vimar range.
* The Android app is a thin client: it talks to the device either
  * **LAN** → HTTP(S) CGI + proprietary media port, or
  * **cloud/P2P** → Qualvision P2P (`libqv-p2p-v2.so`) + cloud CGI relay.

### Ports actually found on a live doorbell (192.168.1.50)

| port  | state | what it is |
|-------|-------|------------|
| 80    | open  | `Qualvision -HTTPServer` (CGI `/tdkcgi`) |
| 443   | open  | same server over TLS (CN=`eziotest`) |
| 34567 | open  | `SDKConst.DEVICE_DEFAULT_STEAM_PORT` — proprietary QVII/AVAPI video |
| 6987  | open  | QV internal port (closes instantly on any RTSP/HTTP preamble) |
| 554   | **closed** | no RTSP server |
| 3702  | no reply | no ONVIF WS-Discovery |

> **There is no RTSP listener on the doorbell.** Video is only available
> through the Qualvision QVII protocol on TCP 34567 (handled by the Android
> `.so`). The app never uses RTSP.

### Audio (2026-11-20)

Audio is a native `E3` CPacket inside the same QVII media stream (G.711 A-law
or mu-law, 8 kHz mono; codec ids `4`/`5`).  The APK decodes it through
`liblive_player.so`; `AudioPlayerManager.audioToggleEx` only toggles the local
`QVPlayerListen`/`QVTalkListen` output, it does **not** send a remote command.

On this IDS9457W (`V401R001B002`) an idle LAN preview contains **no `E3`
frames at all** (0 in a 60 s capture, streams 1 and 2, channel 1; talk
handshake negotiated A-law and returned `ready` but no media).  See
`docs/STREAM.md` §8 for the bridge's auto-detect behavior and
`tools/audio_probe.py`.

---

## 2. Local CGI: `POST /tdkcgi`

* Path: `/tdkcgi` (both `http://IP/tdkcgi` and `https://IP/tdkcgi` work)
* Content type: `application/xml;charset=utf-8`
* Auth failures are returned **as HTTP 200** with `<body><error>401</error>`.

### Request envelope

```xml
<envelope>
  <header>
    <security>username</security>
    <username>adminapp2</username>
    <password>…</password>
    <passwordencode>1</passwordencode>
  </header>
  <body>
    <command>get.product.info</command>
    <content/>
  </body>
</envelope>
```

Header semantics (`DeviceRequestHelp.initHeader`, `SDKConst`):

| field            | value |
|------------------|-------|
| `security`       | `username` (default), `usernametoken`, or `httpauthen` |
| `username`       | `adminapp2` = `SDKConst.DEVICE_USER_NAME` (LAN/local sessions) |
| `password`       | `sha256(authCode)` — lowercase hex |
| `passwordencode` | `1` when the password is the sha256 digest |
| `nc`             | random nonce, only for `usernametoken` |

The password is the **cloud `out-auth-code`** of the device: a 64-hex SHA-256
value returned by the Qualvision user service in `get-device-list`, used
verbatim with `passwordencode=1` (`QvEncrypt.EncodeDevicePassword` leaves
64-char values untouched). It is **not** `sha256(sticker code)`: the sticker
code (e.g. `<LABEL_CODE>`) is the AP/label code and is only accepted by the
device's Wi-Fi AP, not by `/tdkcgi`.

> Verified on IDS9457W fw V401R001B002: `out-auth-code` → `error 0`;
> `sha256(<LABEL_CODE>)` and plain `<LABEL_CODE>` → `error 401`.

For devices that advertise the HTTP-Digest capability, the app first gets a
`401` + `WWW-Authenticate: Digest …` and then sends
(`DeviceAuthHeaderInterceptor`):

```
HA1 = md5(username + ":" + realm + ":" + dataEncodeKey)
HA2 = md5("POST:/tdkcgi")
response = md5(HA1 + ":" + nonce + ":nc00001:suiji:" + qop + ":" + HA2)
Authorization: Digest username="…",realm="…",nonce="…",uri="/tdkcgi",
  algorithm=MD5,response="…",opaque="…",qop=auth,nc=nc00001,cnonce="suiji"
```

### Commands used by this project

| command | purpose |
|---------|---------|
| `get.product.info` | model, firmware, MAC |
| `get.network.config` | network + **`<rtspport>` / `<rtspurl>`** |
| `get.network.base` | alternate network dump |
| `get.system.ability` | capability bitmask incl. `<rtsp><preview>` |
| `get.device.attachInfo` | channel/lock tree (the door buttons) |
| `set.device.opendoor` | **open door / relay** |
| `set.opendoor.checkpassword` | validate the door-open password (does not open) |
| `get.device.streamkey` | stream key (`get.device.streamkey`) |
| `get.device.status` | status |
| `set.seurity.verifycode` | change the verification code |
| `get.onvif.pwd` / `set.onvif.pwd` | ONVIF password (JSON API) |

Responses are XML:

```xml
<envelope>
  <body>
    <error>0</error>
    <content>…</content>
  </body>
</envelope>
```

`error == 0` is success. Other values are Qualvision error codes
(`-10028` = wrong door password, `-10029` = device busy, `401` = auth).

---

## 3. The two door-open buttons

`get.device.attachInfo` returns one `<channel>` per camera channel, each with a
list of `<lockinfo>` relays:

```xml
<content>
  <channelinfo>
    <channellist>
      <channel>
        <num>0</num>
        <name>Entrance</name>
        <lockinfo><num>1</num><name>Door 1</name><enable>1</enable></lockinfo>
        <lockinfo><num>2</num><name>Door 2</name><enable>1</enable></lockinfo>
      </channel>
    </channellist>
  </channelinfo>
</content>
```

Opening a relay (`DeviceRequestHelp.deviceUnlock` → `DeviceUnlockContent`):

```xml
<envelope>
  <header>…</header>
  <body>
    <command>set.device.opendoor</command>
    <content>
      <door>0</door>          <!-- channel number -->
      <locknumber>1</locknumber> <!-- lock number from attachInfo -->
      <password>…</password>  <!-- optional, see below -->
    </content>
  </body>
</envelope>
```

* `door` is the **channel** number, `locknumber` is the **relay** number
  (confusing, but that is how the SDK serialises it).
* `<password>` is the *door-open password* configured in the app. On devices
  with the SHA-256 capability (ability bit 24) the app sends
  `sha256(password)`; on older firmware it sends it in clear. If no password is
  configured the element can be omitted (both are `required=false`).
* `set.opendoor.checkpassword` always sends `sha256(password)` and lets you
  test the code without opening the door.

Equivalent `curl` (replace `CODE` / `PWD`):

```bash
CODE_HASH=$(printf %s "$CODE" | sha256sum | cut -d' ' -f1)
PW_HASH=$(printf %s "$PWD"  | sha256sum | cut -d' ' -f1)

curl -sS http://192.168.1.50/tdkcgi \
  -H 'Content-Type: application/xml;charset=utf-8' \
  --data-binary "<envelope>
    <header><security>username</security><username>adminapp2</username>
      <password>${CODE_HASH}</password><passwordencode>1</passwordencode></header>
    <body><command>set.device.opendoor</command>
      <content><door>0</door><locknumber>1</locknumber>
        <password>${PW_HASH}</password></content></body>
  </envelope>"
```

---

## 4. Video / RTSP

The firmware *knows about* RTSP: `get.network.config` returns

```xml
<network><base>
  <httpport>80</httpport><httpsport>443</httpsport>
  <mediaport>34567</mediaport>
  <rtspport>…</rtspport>
  <rtspurl>…</rtspurl>
</base></network>
```

and `get.system.ability` returns `<rtsp><preview>0|1</preview></rtsp>`.

However:

* No port is listening for RTSP (554 and all other ports checked).
* The app never reads `rtspport`/`rtspurl`; the video player uses the
  Qualvision QVII protocol on 34567 (`QvPlayerCore` → `liblive_player.so`).
* ONVIF (`get.onvif.pwd`/`set.onvif.pwd`) is implemented in the SDK but the
  device does not run an ONVIF service by default (no WS-Discovery reply, no
  `/onvif/device_service`).

So a native `rtsp://` URL can only appear if the firmware's RTSP server is
enabled. The pieces to check with the verification code are:

1. `get.network.config` → non-zero `<rtspport>` + `<rtspurl>`.
2. If it is `0`, try `set.network.config` with `rtspport`/`rtspurl` set.
3. `set.onvif.pwd` (JSON) and then ONVIF `GetProfiles` / `GetStreamUri` at
   `http://IP/onvif/device_service`.

If neither works, the only remaining option is a **QVII→RTSP bridge** (the
Android SDK has `SDK_StartRtspserver`/`QVRTSPGetAPI` in `liblive_player.so`,
but it is ARM-only and not exposed by the app UI).

### RTSP probe helper

`tools/viewdoor.py rtsp --host 192.168.1.50 --code CODE` reads the device's
`rtspport`/`rtspurl`, then sends *one* DESCRIBE at a time (important: the
doorbell allows only one concurrent stream) and tries the usual paths
(`/live/ch0`, `/Streaming/Channels/101`, `/cam/realmonitor…`, …) with the
supplied credentials.

---

## 5. Cloud / account flow (for reference)

The app can log in with a MyVimar account (third-party login tag `vimar`):

1. `GET https://sso.qvcloud.net/vimar/usrpwd/authorize?oem=G0078&client_id=…&rgn=…`
   → `302` to the Vimar OAuth2 server
   (`https://oauth.vimar.com/it/it/oauth/authorization-code/authorize?client_id=qualvision…`).
2. Log in with the MyVimar e-mail/password (form fields `hash`, `email`, `passw`).
3. Redirect to `https://sso.qvcloud.net/vimar/callback?code=…` returns JSON:

   ```json
   {"result":0,"account":"[vimar]<account-id>","password":"<64 hex>","rgn":1}
   ```

   These are the **Qualvision** account + hashed password (not the Vimar one).
4. OAuth2 password grant for the Qualvision API:

   ```
   GET https://vimar.qvcloud.net/qvoauthv2/token
       ?grant_type=password&client_id=003-2078-<android_id>&client_type=3
       &oemid=G0078&appid=2078&usr=<account>&pwd=<token>&region_id=1&client_flag=1
   ```

   returns `access_token` + `refresh_token` (JWT, `qv-uid`, `qv-rgn`, …).
5. Qualvision API calls use the header `token: <access_token>`
   (see `OkHttpUtil`), e.g. `GET /openapi-tdk/client/user/findUserServerInfo`.

The device list (with each device's `out-auth-code`) lives in the Qualvision
*user* service (`POST /auth/user`, XML envelope, `command=login`).

Region‑1 endpoints (from the app's `save.xml` after login):

| service | URL |
|---------|-----|
| OAuth2 | `https://oauth2r1.qvcloud.net/` (path `/qvoauthv2/token`) |
| user / device list | `https://r1-2-sec.qvcloud.net/` (path `/auth/user`) |
| openapi | `https://tdkopenapir1.qvcloud.net/` |
| SSO | `https://sso.qvcloud.net/` |
| push/MQTT | `mqtts://mqttsr1.qvcloud.net:1886/` |

Login (verified):

1. `GET https://oauth2r1.qvcloud.net/qvoauthv2/token` with
   `grant_type=password&client_id=003-2078-<android_id>&client_type=3&oemid=G0078&appid=2078&usr=<acct>&pwd=<sso-token>&region_id=1&client_flag=1`
2. `POST https://r1-2-sec.qvcloud.net/auth/user` with `command=login`
   → `<session><id>…</id></session>`
3. `POST` same URL with `command=get-device-list` and the session id
   → `<device>` blocks with `<out-auth-code>` (the local CGI password),
   `<dynamic-password>`, `<data-encode-key>`, `<default-out-auth-code>`.

The `client_id` must be one the app registered (its Android ID based id,
`003-2078-…`); otherwise the login returns `100101141`.

`tools/viewdoor.py` does not need any of this: the LAN CGI only needs the
device verification code.

---

## 6. Home Assistant

Once the CGI works, the two relays can be exposed as REST commands:

```yaml
rest_command:
  vimar_door_1:
    url: "http://192.168.1.50/tdkcgi"
    method: POST
    content_type: "application/xml;charset=utf-8"
    payload: >-
      <envelope><header><security>username</security><username>adminapp2</username>
      <password>{{ states('input_text.vimar_code_hash') }}</password><passwordencode>1</passwordencode></header>
      <body><command>set.device.opendoor</command>
      <content><door>0</door><locknumber>1</locknumber>
      <password>{{ states('input_text.vimar_door_pw_hash') }}</password></content></body></envelope>
```

If the HA host is not on the doorbell's LAN (Tailscale/WireGuard), route
`192.168.1.50` through a tunnel — it is a plain HTTP call to the device.

For the video, see section 4: as long as the firmware does not expose RTSP,
Home Assistant cannot consume the stream directly.
