# Doorbell ring

A doorbell press can start a Home Assistant automation. The camera stream
does **not** contain the outdoor button event, so the integration uses the
same cloud call journal as the Vimar app.

This feature is optional. Watching video and using the gate relays do not
require it.

## How it works

- The bridge polls the Vimar cloud call journal every few seconds.
- It reads only **incoming call** records for the configured doorbell.
- On startup it records the existing calls as a baseline, so old calls do not
  trigger an automation.
- When a new incoming call appears, the bridge sends a small JSON payload to
  a Home Assistant webhook.
- The webhook trigger runs your automation.

The bridge does not answer calls, mark records as read, download media, open
the gate or register a phone's push token.

## Requirements

- A `cloud-account.json` file for the bridge, using the same Vimar app account
  that can see the doorbell.
- The bridge's `RING_WATCH` setting enabled.
- A Home Assistant webhook URL in the bridge's `RING_WEBHOOK_URL`.
- A webhook-triggered automation in Home Assistant.

The cloud account is separate from the doorbell's out-auth-code. The
out-auth-code authenticates to the device; the cloud account authenticates to
Vimar's service.

## Configure the bridge

In the bridge environment:

```yaml
RING_WATCH: "1"
RING_WEBHOOK_URL: "http://127.0.0.1:8123/api/webhook/<webhook-id>"
RING_POLL_SECONDS: "5"
```

Use `http://<home-assistant-host>:8123/...` if the bridge is not on the same
host as Home Assistant. The webhook URL contains a secret; do not commit it or
paste it into a public issue.

Restart the bridge after changing the environment. Then check:

```bash
curl -s http://127.0.0.1:8558/status
```

The `ring` section should show:

- `enabled: true`
- `ok: true`
- `baseline_ready: true`
- `webhook_configured: true`

`ring_count` is the number of new calls delivered since the bridge started.

## Create the Home Assistant automation

The easiest way is through the automation editor:

1. Create a new automation.
2. Add a **Webhook** trigger.
3. Copy the generated webhook ID.
4. Put the full URL in the bridge's `RING_WEBHOOK_URL`.
5. Add the actions you want.

The YAML equivalent is:

```yaml
- id: vimar_door_ring
  alias: Vimar - qualcuno ha suonato
  trigger:
    - platform: webhook
      webhook_id: <webhook-id>
      allowed_methods: [POST]
      local_only: true
  action:
    - service: notify.mobile_app_<phone>
      data:
        title: Cancelletto
        message: Qualcuno ha suonato.
```

`local_only: true` is correct when the bridge and Home Assistant are on the
same host. If the bridge is on another machine, set it to `false` and make
sure the webhook is reachable only from your trusted network.

## Payload

The bridge sends a JSON body like:

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

The trigger itself does not need to parse the payload unless you want to use
the channel or time in your actions.

## Actions you can choose

Any Home Assistant action works. Common choices:

- Send a notification to the companion app.
- Turn on an entrance light.
- Play a TTS announcement.
- Start a script that opens a dashboard view.
- Save a snapshot from another camera.
- Add a logbook entry.

Keep the automation fast and avoid opening a second live stream at the same
time as the camera dashboard; the doorbell allows only one video stream.

## Testing

1. Check the bridge status as described above.
2. Ring the doorbell once.
3. Wait up to the poll interval plus a few seconds.
4. Check `ring_count` in the bridge status.
5. Check the automation's **Last triggered** value and its trace.

The first successful poll after a bridge restart is a baseline. A call that
was already in the cloud before that baseline will not trigger an automation.
This is intentional.

## If it does not trigger

| Symptom | Check |
|---------|-------|
| `ring.ok` is `false` | The cloud account is missing, expired or invalid. See [Troubleshooting](troubleshooting.md#ring-events-do-not-arrive). |
| `ring.baseline_ready` is `false` | The bridge has not completed a successful cloud read yet. |
| `ring.webhook_configured` is `false` | `RING_WEBHOOK_URL` is empty or the bridge was not restarted. |
| `last_webhook_ok` is `false` | The bridge cannot reach Home Assistant, or the webhook is remote and rejected by `local_only`. |
| `ring_count` increases but the automation does not run | The automation is disabled, the webhook ID does not match, or its conditions/trace show a failure. |
| An old call is delivered after a restart | The bridge normally baselines on startup; make sure the current bridge version is running. |

Webhook IDs are secrets. If one is exposed, create a new webhook trigger and
update the bridge.

## Related

- [RTSP bridge](bridge.md#ring-events)
- [Troubleshooting](troubleshooting.md#ring-events-do-not-arrive)
- [Internal ring implementation](internal/INTEGRATION.md#doorbell-ring)
