# Doorbell ring

A doorbell press can start a Home Assistant automation. The camera stream does
not contain the outdoor button event, so the integration reads the same Vimar
cloud call journal as the Vimar app.

This feature is optional. Watching video and using the gate relays do not
require it.

## How it works

- The managed bridge polls the Vimar cloud call journal every few seconds.
- It reads only **incoming call** records for the configured doorbell.
- On startup it records the existing calls as a baseline, so old calls do not
  trigger an automation.
- When a new incoming call appears, the bridge sends it to the integration.
- The integration fires the Home Assistant event **`vimar_door_ring`**.
- Your automation uses an **event** trigger for `vimar_door_ring`.

The bridge does not answer calls, mark records as read, download media, open
the gate or register a phone's push token.

## Requirements

- A valid **Vimar app account** for the doorbell. The one-step setup already
  creates and stores the cloud credentials.
- The managed bridge running; this is automatic after adding the integration.
- The cloud account must be able to see the doorbell.

Ring events need the cloud; they are not available from the local QVII video
stream.

## Create the automation

Create a normal automation with an **Event** trigger:

```yaml
- id: vimar_door_ring
  alias: Vimar - qualcuno ha suonato
  trigger:
    - platform: event
      event_type: vimar_door_ring
  action:
    - service: notify.mobile_app_<phone>
      data:
        title: <name>
        message: Qualcuno ha suonato.
```

The event data contains:

```json
{
  "event": "ring",
  "source": "vimar_cloud_call",
  "device_id": "<doorbell UID>",
  "channel": 1,
  "record_id": "<cloud record id>",
  "alarm_id": "<alarm id>",
  "time": "2026-11-22 09:00:00",
  "ring_count": 1,
  "timestamp": 1795338000.0
}
```

Choose any action: notification, lights, TTS, snapshot, dashboard, script.

## Testing

1. Check the bridge status in the integration diagnostics or in the bridge
   `/status` page.
2. Ring the doorbell once.
3. Wait up to the poll interval plus a few seconds.
4. Check the automation's **Last triggered** value and trace.
5. The event is also visible in **Developer Tools → Events** if you listen for
   `vimar_door_ring`.

The first successful read after a restart is a baseline. A call that was
already in the cloud before that baseline will not trigger an automation. This
is intentional.

## If it does not trigger

| Symptom | Check |
|---------|-------|
| The automation never runs | The event type must be exactly `vimar_door_ring`; check the automation trace. |
| The bridge reports a cloud error | The Vimar account credentials or session are invalid; reconfigure the integration or re-authenticate the account. |
| No new records | Ring the doorbell after the bridge has completed its first cloud read. Existing calls are baseline. |
| The camera still works but rings do not | The local video session is independent; check the cloud bridge, not the camera. |

The integration diagnostics/logs contain the bridge status. Do not paste
account tokens, passwords or device keys into a public issue.

## Advanced/legacy external bridge

An older installation may run the bridge as a separate service. In that case
the bridge can deliver rings through the webhook described in
[RTSP bridge](bridge.md#ring-events), and the automation uses a webhook
trigger. New installations use `vimar_door_ring` and do not need this setup.

## Related

- [RTSP bridge](bridge.md)
- [Troubleshooting](troubleshooting.md)
- [Internal ring implementation](internal/INTEGRATION.md#doorbell-ring)
