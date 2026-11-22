# Vimar VIEW Door documentation

This is the user documentation for the Home Assistant integration and its
RTSP bridge. It covers what most people need to install the doorbell, use it
day to day, and fix common problems.

## Start here

| I want to… | Read |
|------------|------|
| Install everything for the first time | [Installation](installation.md) |
| Run the RTSP bridge | [RTSP bridge](bridge.md) |
| See the camera, use the gate buttons or talk | [Using the integration](usage.md) |
| Run automations when someone rings | [Doorbell ring](ring.md) |
| Fix a problem | [Troubleshooting](troubleshooting.md) |

## What the system is made of

The integration and its bridge are managed together:

1. **The Home Assistant integration** (`vimar_door`) creates the camera,
   buttons and optional audio capability.
2. **The managed bridge** runs inside the Home Assistant container and talks
   to the doorbell. The one-step setup starts and supervises it.

An advanced/legacy deployment can still use a separate RTSP bridge; see
[RTSP bridge](bridge.md).

## Supported hardware

The integration was developed and tested on a **Vimar VIEW Door** doorbell
based on the Qualvision QVII platform, model **Elvox IDS9457W**. Other QVII
doorbells may work, but they have not been verified.

## Getting help

- Read [Troubleshooting](troubleshooting.md) first.
- For a reproducible bug, open an issue on
  [GitHub](https://github.com/PirateTokenThemeSong/viewdoor-re/issues) and
  include the relevant Home Assistant and bridge logs.
- Never paste authentication codes, cloud account files, passwords or
  webhook IDs into a public issue.

## For developers

Implementation notes, the QVII protocol, bridge internals and test procedures
are kept separately in [internal documentation](internal/README.md).
