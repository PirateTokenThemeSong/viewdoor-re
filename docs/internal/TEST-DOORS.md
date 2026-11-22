> Internal historical test procedure. It can fire physical relays; do not
> use it on an installation you do not control. User documentation is in
> [docs/README.md](../README.md).

# Test the two door relays (tomorrow)

Everything below is ready; **nothing has been fired yet**.

* doorbell: `192.168.1.50` (IDS9457W, fw V401R001B002)
* token: `<OUT_AUTH_CODE>` — replace it with the device's `out-auth-code`
  (the sticker code `<LABEL_CODE>` is not accepted by `/tdkcgi`)
* the cloud record gives the token an expiry; if you get `401`, fetch a fresh
  one with `get-device-token`.

## 1. Sanity check (no door)

```bash
cd <repo>

python tools/viewdoor.py raw get.product.info \
  --host 192.168.1.50 --scheme http \
  --code <OUT_AUTH_CODE>
```

Expect `<error>0</error>` and `<version>V401R001B002</version>`.
If you get `401`, the device was re-bound/re-paired and the token rotated —
we can pull a fresh one from the cloud.

## 2. Door 1

```bash
python tools/viewdoor.py open 1 \
  --host 192.168.1.50 --scheme http \
  --code <OUT_AUTH_CODE>
```

It asks `Open door 1 (channel 0) on 192.168.1.50? [y/N]` — type `y`.

## 3. Door 2

```bash
python tools/viewdoor.py open 2 \
  --host 192.168.1.50 --scheme http \
  --code <OUT_AUTH_CODE>
```

## If something is off

| symptom | meaning / next step |
|---------|---------------------|
| `device error -10029` | device busy (someone is viewing the camera) — retry |
| `device error -10028` | a door-open password is required: add `--unlock-password '<the code set in the app>'` (the tool sha256s it) |
| `device rejected auth (error 401)` | token rotated / wrong — re-fetch via cloud |
| nothing happens (no error) | relays may be numbered differently — try lock 2 for the other button |

If the two buttons are swapped, just swap `1` and `2` in the commands (that is
the only difference: `<locknumber>1</locknumber>` vs `<locknumber>2</locknumber>`).

Raw equivalent if you want to paste it in `curl` / HA:

```bash
TOKEN=<OUT_AUTH_CODE>
curl -sS http://192.168.1.50/tdkcgi \
  -H 'Content-Type: application/xml;charset=utf-8' \
  --data-binary "<envelope><header><security>username</security><username>adminapp2</username><password>${TOKEN}</password><passwordencode>1</passwordencode></header><body><command>set.device.opendoor</command><content><door>0</door><locknumber>1</locknumber></content></body></envelope>"
```
