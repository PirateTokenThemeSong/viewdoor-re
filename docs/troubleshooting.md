# Troubleshooting

Start with the bridge status and the Home Assistant logs. Most problems are
visible in one of those places.

## Bridge status

```bash
curl -s http://127.0.0.1:8558/status
```

Useful fields:

| Field | Meaning |
|-------|---------|
| `session` | A doorbell QVII session is open |
| `ready` | Video frames have arrived |
| `clients` | Number of RTSP viewers |
| `audio_frames` | Visitor audio frames received |
| `talk.active` | A microphone client has opened the talk channel |
| `talk.mic_active` | The microphone backchannel is connected |
| `talk.route_ready` | Visitor audio is routed to the video stream |
| `key_refresh` | Last automatic stream-key refresh and any error |
| `ring.ok` | Cloud call journal is readable |
| `ring.last_error` | Last ring watcher error, if any |
| `ring.ring_count` | New calls delivered to Home Assistant |
| `ring.last_webhook_ok` | Whether the last webhook delivery succeeded |

For a managed bridge, the bridge output is written to the Home Assistant log
with the `Vimar bridge:` prefix. For a legacy external bridge, read its
container/service logs.

## Camera does not appear

- Confirm the bridge is running and `session`/`ready` become true.
- Confirm Home Assistant can reach the RTSP URL configured in the integration.
  `127.0.0.1` only works when the bridge runs on the same host as Home
  Assistant.
- Check the auth code in the integration. A wrong out-auth-code prevents the
  doorbell from opening a session or firing the relays.
- The doorbell allows one video stream. Close the Vimar app, another dashboard
  or a second integration using the same doorbell.
- If the camera worked and then stopped after a long-running session, wait for
  the bridge to cycle the session, or reload the integration.

## Video stops working

The doorbell's 32-byte stream key can rotate after re-pairing, cloud changes or
a firmware event. If the bridge reports a session that opens but never
produces video:

1. Check `key_refresh` in `/status`.
2. Make sure `cloud-account.json` exists and is valid.
3. If automatic refresh is disabled, set `KEY_REFRESH=1` and restart the
   bridge.
4. A manual refresh can be performed with the tools in the repository; see
   the [internal bridge reference](internal/bridge.md#if-video-stops-but-the-app-works).

The stream key is sensitive. Do not paste it into an issue.

## Gate buttons do nothing

- Confirm the relay output number. Relay 1 and relay 2 may control different
  outputs depending on the installation.
- Check that the bridge is not holding a video session at the same time; the
  doorbell can reject some commands while busy.
- Check the integration log in **Settings → System → Logs**.
- A wrong or expired auth code is the most common cause. Reconfigure the
  integration with the current out-auth-code.

The relay opens a physical gate. Test only when it is safe.

## Two-way audio

### Start talking is missing

All four conditions are required:

- the integration option **Two-way audio** is enabled;
- the camera stream is provided over WebRTC/go2rtc;
- Home Assistant is open over **HTTPS**;
- **Settings → System → Labs → Two-way audio** is enabled.

The control is in the camera's **more-info dialog**, not in a plain
`custom:webrtc-camera` card.

### No microphone audio at the doorbell

- Allow microphone permission for the Home Assistant origin in the browser or
  app.
- Check `talk.active` and `talk.mic_active` in `/status` while speaking.
- If `talk.active` is false, the browser did not open the backchannel; check
  WebRTC and the bridge's `TALK` setting.
- If `talk.active` is true but `mic_active` is false, the RTSP backchannel did
  not reach the bridge; check the bridge logs and network.
- If both are true, the transport is working; the remaining check is the
  physical speaker at the gate.

Speaker audio is a real public output. Keep the volume conservative.

### Visitor audio is silent

- The doorbell sends visitor audio only during an active call/ring on this
  model. An idle preview can be silent; this is normal.
- Check `audio_frames` while someone is at the door.
- Check `audio`/`audio_enabled` in `/status`.
- Start talking can interrupt video briefly; that is expected.

## Ring events do not arrive

1. Check the bridge status in the integration diagnostics or `/status`:
   - `ring.enabled` should be true.
   - `ring.ok` should be true.
   - `ring.baseline_ready` should be true after the first successful read.
2. If `ring.ok` is false, the cloud account is missing or expired. The
   `ring.last_error` value identifies the stage. A `cloud-login-...` error
   means the Vimar cloud login must be renewed.
3. If `ring.ring_count` increases but the automation does not run, check the
   automation trigger is exactly `vimar_door_ring` and is enabled.
4. Existing calls are baselined when the bridge starts. Wait for a **new**
   call after the bridge has completed its first successful read.

Ring detection is cloud-based on this model; there is no local button event in
the QVII video stream. The Vimar app account must be able to see the doorbell.

## Cloud account and key refresh

The cloud account file is separate from the doorbell auth code. It is used for:

- automatic stream-key refresh;
- doorbell ring events.

If the cloud login expires, both features stop while the camera and relays
continue to work. Remove and re-add the integration with the Vimar account to
regenerate the private files under `/config/vimar_door/`; do not edit them by
hand. If you use a legacy external bridge, replace its `cloud-account.json`
and restart it.

Do not put account files, passwords, device keys or webhook IDs in a public
issue.

## Integration will not load

- Confirm the `custom_components/vimar_door` folder is directly under the
  Home Assistant `config/custom_components/` directory.
- Check the Home Assistant log for a Python error.
- Restart Home Assistant after copying the integration.
- If you used the old YAML package, remove it to avoid duplicate
  duplicate relay button entities.
- Confirm Home Assistant is version 2026.11 or newer.

## Credentials and privacy

Never post these in a public issue or commit:

- the doorbell out-auth-code;
- the 32-byte stream key;
- `cloud-account.json`;
- the webhook ID or URL;
- full diagnostics that contain account or device identifiers.

When reporting a problem, describe the symptom, the bridge `/status` fields
that are relevant, and the Home Assistant error message with secrets removed.

## Related

- [Installation](installation.md)
- [Using the integration](usage.md)
- [Doorbell ring](ring.md)
- [Internal technical reference](internal/README.md)
