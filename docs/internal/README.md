# Internal documentation

This directory is the technical reference for the integration and the RTSP
bridge. It is aimed at developers, maintainers and people debugging the
protocol. User-facing installation and usage instructions live in the
[user documentation](../README.md).

## Technical documents

| Document | Contents |
|----------|----------|
| [Integration notes](INTEGRATION.md) | Entity implementation, camera capabilities, two-way-audio code paths, ring watcher behaviour and live verification notes |
| [Bridge reference](bridge.md) | Bridge environment, ports, signals, key refresh and deployment internals |
| [QVII protocol](PROTOCOL.md) | Local `/tdkcgi` control API, cloud services, authentication and relay commands |
| [Streaming internals](STREAM.md) | QVII handshake, stream key, RTSP bridge, session lifecycle and audio routing |
| [Test doors](TEST-DOORS.md) | Historical hardware notes and test targets |

## Repository layout

```text
custom_components/vimar_door/   Home Assistant integration
homeassistant/                  QVII -> RTSP bridge and helpers
bridge_tests/                   Offline bridge/audio/talk tests
tests/                          HA integration tests
tools/                          Discovery, probes and RE helpers
docs/                           User documentation
docs/internal/                  This technical reference
```

## Local checks

Run the bridge tests from the repository root:

```bash
cd bridge_tests
pytest
```

Compile the bridge modules while editing:

```bash
python -m py_compile \
  homeassistant/vimar_bridge.py \
  homeassistant/vimar_ring.py \
  homeassistant/vimar_cloud.py \
  homeassistant/vimar_talk.py \
  homeassistant/vimar_route.py
```

The integration itself is exercised by Home Assistant; import it only in an
environment that has the Home Assistant package installed.

## Build and runtime assumptions

- The bridge runs on Python 3.12 with `quii-helper`.
- The integration runs inside Home Assistant and does not run `quii-helper`.
- The bridge is the only component with a QVII session to the doorbell.
- The HA camera entity uses the bridge's RTSP URL; go2rtc is only HA's WebRTC
  provider.
- Cloud ring detection is separate from the local video session and needs its
  own valid cloud account file.

## Secrets

Keep these out of the repository and out of public logs:

- the doorbell out-auth-code;
- `stream_credentials.json` and its 32-byte stream key;
- `cloud-account.json`, including its client ID and password/token;
- webhook IDs and URLs;
- Home Assistant long-lived access tokens.

The public repository contains only synthetic test data and placeholders.

## User documentation

Do not duplicate user-facing instructions here. Link to:

- [Installation](../installation.md)
- [RTSP bridge](../bridge.md)
- [Usage](../usage.md)
- [Doorbell ring](../ring.md)
- [Troubleshooting](../troubleshooting.md)
