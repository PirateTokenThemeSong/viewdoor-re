#!/usr/bin/env python3
"""Capture the doorbell's native QVII audio (E3 G.711) to a WAV file.

The Vimar IDS9457W sends no audio on an idle LAN preview (verified 2026-11-20:
zero E3 frames in 60 s, streams 1 and 2, channel 1).  If the doorbell is in an
active call it may start sending audio; this probe is the quickest way to
check, without the RTSP bridge and without enabling any outgoing audio.

It opens ONE normal LAN preview session (video is discarded), so stop the
`vimar-stream` bridge / Vimar app first: the doorbell allows one stream.

Examples:

    python tools/audio_probe.py --code <OUT_AUTH_CODE> --seconds 30
    python tools/audio_probe.py --credentials work/stream_credentials.json \
        --seconds 60 --out work/doorbell.wav

Exit codes: 0 = audio saved, 2 = connected but no E3 audio, 1 = error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

try:
    from quii_helper import AutonomousConfig, Camera, CameraStreamCredentials
    from quii_helper.media.audio.g711 import decode_g711
except ImportError:  # pragma: no cover - dependency hint
    print("quii-helper is required: python -m pip install quii-helper")
    raise SystemExit(1)


def load_credentials(path: str | None) -> tuple[str, str, str]:
    if not path:
        return "", "", ""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        str(data.get("device_id") or ""),
        str(data.get("stream_key") or ""),
        str(data.get("auth_code") or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.1.50", help="doorbell LAN IP")
    parser.add_argument("--port", type=int, default=34567, help="QVII TCP port")
    parser.add_argument("--channel", type=int, default=1, help="video channel")
    parser.add_argument("--stream", type=int, default=1, help="stream id (1=main)")
    parser.add_argument(
        "--credentials",
        default="work/stream_credentials.json",
        help="JSON with device_id + stream_key + auth_code",
    )
    parser.add_argument("--code", help="device out-auth-code (overrides JSON)")
    parser.add_argument("--key", help="32-byte stream key (overrides JSON)")
    parser.add_argument("--device-id", help="device id (overrides JSON)")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=2.0, help="per-read timeout")
    parser.add_argument("--out", default="work/doorbell.wav")
    args = parser.parse_args()

    device_id, key, code = load_credentials(args.credentials)
    device_id = args.device_id or device_id
    key = args.key or key
    code = args.code or code
    if not key or not code:
        parser.error("missing key/auth code: pass --credentials or --key/--code")

    cfg = AutonomousConfig(
        connection_mode="lan",
        device_host=args.host,
        stream_port=args.port,
        channel=args.channel,
        stream=args.stream,
        stream_key=key,
        auth_code=code,
    )
    camera = Camera(
        config=cfg,
        connection_mode="lan",
        device_id=device_id,
        stream_key=key,
        auth_code=code,
        status=lambda msg: print(f"[quii] {msg}", flush=True),
    )

    frames = []
    started = time.monotonic()
    try:
        with camera.listen_audio() as audio:
            print(
                f"listening for E3 audio on {args.host}:{args.port} "
                f"(channel {args.channel}, stream {args.stream}) "
                f"for {args.seconds:.0f}s",
                flush=True,
            )
            while time.monotonic() - started < args.seconds:
                try:
                    frame = audio.recv_frame(timeout=args.timeout)
                except TimeoutError:
                    continue
                frames.append(frame)
                if len(frames) == 1 or len(frames) % 25 == 0:
                    print(
                        f"  {len(frames)} frames, codec={frame.codec} "
                        f"({frame.codec_name}), {frame.sample_rate} Hz, "
                        f"{frame.channels} ch, {len(frame.data)} B",
                        flush=True,
                    )
    except Exception as exc:  # device busy, auth, network, ...
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "hint: the doorbell allows one stream; stop the HA bridge / Vimar app "
            "and wait ~15 s before retrying.",
            file=sys.stderr,
        )
        return 1

    if not frames:
        print(
            "no E3 audio received.  The LAN preview is video-only in this state; "
            "try again while a doorbell call is active (someone pressed the button)."
        )
        return 2

    codec = frames[0].codec
    matching = [f for f in frames if f.codec == codec]
    print(
        f"received {len(frames)} frames, {len(matching)} with codec {codec} "
        f"({frames[0].codec_name})"
    )
    if codec not in (4, 5):
        raw = Path(args.out).with_suffix(".g711")
        raw.write_bytes(b"".join(f.data for f in matching))
        print(f"unsupported codec, raw payload saved to {raw}")
        return 0

    pcm = b"".join(decode_g711(f.data, codec) for f in matching)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(pcm)
    duration = len(pcm) / 16000.0
    print(f"saved {out} ({duration:.2f}s, {frames[0].codec_name} -> PCM16)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
