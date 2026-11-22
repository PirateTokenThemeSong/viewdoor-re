# Installation

The integration is designed for a one-step setup: install it, add the
doorbell with your Vimar account and IP, and let it create the local
credentials and bridge.

## Before you start

You need:

- Home Assistant **2026.11** or newer.
- A Vimar VIEW Door / QVII doorbell on the LAN.
- The **Vimar app account** used for this doorbell (email and password).
- The doorbell's LAN **IP address**.
- No separate bridge container: the integration runs the bridge as a managed
  process inside the Home Assistant container.

The doorbell accepts only one QVII video stream at a time.

## Install the integration

### HACS

Add this repository as a custom **Integration** repository in HACS, install
the latest release and restart Home Assistant.

### Manual installation

1. Copy the `custom_components/vimar_door` folder from this repository into
   your Home Assistant `config/custom_components/` directory.
2. Remove any old `vimar_door` folder or legacy YAML package first.
3. Restart Home Assistant. Home Assistant installs the integration
   requirements (`quii-helper`) automatically.

## Add the doorbell

1. Open **Settings → Devices & services**.
2. Choose **Add integration** and search for **Vimar VIEW Door**.
3. Enter:

   | Field | Notes |
   |-------|-------|
   | Vimar email | The account used in the Vimar app |
   | Vimar password | The same account password |
   | Doorbell IP | Its LAN address, for example `192.168.1.5` |
   | Name | Optional; blank uses the name reported by the Vimar cloud |
   | Two-way audio | Optional; see the requirements below |

The integration then:

- signs in to the Vimar SSO;
- lists the cloud device and selects it;
- fetches the out-auth-code and stream key;
- writes private credentials under `/config/vimar_door/`;
- starts the QVII → RTSP bridge inside Home Assistant;
- creates the camera and relay entities.

No auth code, stream key, RTSP URL or separate bridge installation is needed.

If the account contains more than one doorbell, automatic selection is not
supported yet; configure the doorbell manually or use an account with one
doorbell.

## Two-way audio

Two-way audio is optional and needs all of:

- the integration option **Two-way audio** enabled;
- Home Assistant opened over **HTTPS**;
- **Settings → System → Labs → Two-way audio** enabled;
- a compatible browser and the bridge's audio backchannel.

The control appears in the camera's **more-info** dialog as **Start talking**.
See [Using the integration](usage.md#two-way-audio).

## Existing external bridge

Previous versions used a separately deployed bridge. Those entries continue
to work. New installations use the managed bridge by default. The advanced
external deployment is described in [RTSP bridge](bridge.md).

## Next steps

- [Using the integration](usage.md)
- [Doorbell ring](ring.md)
- [Troubleshooting](troubleshooting.md)
