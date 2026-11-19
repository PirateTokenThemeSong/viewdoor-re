# Home Assistant — Vimar cancelletto (porta 2)

A real `button` entity that fires door relay **2** (pedestrian gate) over the LAN CGI.

| entity | relay | status |
|--------|-------|--------|
| `button.cancelletto` | `<locknumber>2</locknumber>` | ✅ installed, opens |

Door relay **1** (`locknumber 1`) is not exposed yet — the `rest_command` block for it
is commented in `vimar_doors.yaml`.

## Where it lives (live instance)

Installed on `https://ha.example.com`:

* `/config/packages/vimar_doors.yaml` — the package (source: `vimar_doors.yaml`)
* `/config/configuration.yaml` — now has

  ```yaml
  homeassistant:
    packages: !include_dir_named packages
    allowlist_external_dirs:
      - /testmedia
  ```

* A backup of the old config is at `/config/configuration.yaml.bak-pre-vimar`.

The package is loaded at startup, so **any edit needs a restart**
(Settings → System → Restart).

## Dashboard

Added to **Casa → Prese e luci**, section *Cancelli e garage*, right under the two
garage tiles (`button.cancelletto`, name “Cancelletto (porta 2)”, with a confirm
dialog before firing).

## Use it

* Tap the card → confirm → gate opens.
* Automation / script / voice:

  ```yaml
  action:
    - action: button.press
      target:
        entity_id: button.cancelletto
  ```

* Direct `rest_command` (same thing the button does):

  ```yaml
  action:
    - action: rest_command.vimar_open_door_2
  ```

## How it works

```
button.cancelletto (template button)
   └─ press → rest_command.vimar_open_door_2 → HTTP POST
```

```
POST http://192.168.1.50/tdkcgi
Content-Type: application/xml;charset=utf-8

<envelope>
  <header>
    <security>username</security>
    <username>adminapp2</username>
    <password><OUT_AUTH_CODE></password>
    <passwordencode>1</passwordencode>
  </header>
  <body>
    <command>set.device.opendoor</command>
    <content>
      <door>0</door>
      <locknumber>2</locknumber>
      <password><OUT_AUTH_CODE></password>
    </content>
  </body>
</envelope>
```

Replace `<OUT_AUTH_CODE>` with the device's `out-auth-code` — the same value goes
in the header and in the door `<password>` element (`error -1` without it,
`error -3` with the sticker code).

## Notes

* **Token**: the repo deliberately ships a placeholder. If the doorbell is
  re-paired or the token rotates, calls return `401` and a fresh value must be
  fetched from the cloud.
* **HA reachability** is confirmed: the HA container (host network) posts to
  `192.168.1.50` from `<HA_HOST_IP>` and gets `error 0`.
* The doorbell allows **one concurrent video stream**; the relay command is
  independent of streaming.
* Keep the token out of public backups if you share configs.
