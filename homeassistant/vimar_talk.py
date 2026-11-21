#!/usr/bin/env python3
"""Two-way QVII LAN talk bridge for the Vimar VIEW Door.

The bridge's RTSP backchannel writes raw G.711 A-law (8000 Hz mono) bytes to
this process over a loopback TCP socket.  We buffer them into 40 ms frames
(320 bytes), open one ``LanTalkSession`` to the doorbell and send each frame
on the native talk channel (selector 65535).

While the talk session is active the doorbell sends the visitor's voice on
that same talk channel (the app's ``QVTalkListen``).  We parse those media
payloads and hand them to ``incoming_sink`` so the bridge can publish them on
its RTSP audio track; that is what makes the visitor audible while talking.

The server binds 127.0.0.1 only and does **not** open a doorbell connection
until a client connects, i.e. only when someone presses "Start talking" in
Home Assistant.

Environment:
  TALK_PORT            loopback TCP port (8560)
  CREDENTIALS          stream_credentials.json
  CAMERA_DEVICE_HOST   doorbell IP
  CAMERA_STREAM_PORT   QVII TCP port (34567)
  CAMERA_CHANNEL       preview channel (only used to build the config)
  CAMERA_STREAM_ID     stream id (only used to build the config)
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime
from typing import Any

from quii_helper import AutonomousConfig, CameraStreamCredentials
from quii_helper.media.audio import CameraAudioFrame
from quii_helper.media.audio.g711 import decode_g711, encode_g711
from quii_helper.media.cpacket import starts_cpacket
from quii_helper.media.frames.cframe_pack import QuiiCFramePack
from quii_helper.protocols.tcp.talk.session import LanTalkSession

TALK_PORT = int(os.environ.get("TALK_PORT", "8560"))
CREDENTIALS = os.environ.get(
    "CREDENTIALS", "/config/vimar/stream_credentials.json"
)
DEVICE_HOST = os.environ.get("CAMERA_DEVICE_HOST", "192.168.1.50")
DEVICE_PORT = int(os.environ.get("CAMERA_STREAM_PORT", "34567"))
CHANNEL = int(os.environ.get("CAMERA_CHANNEL", "1"))
STREAM_ID = int(os.environ.get("CAMERA_STREAM_ID", "2"))

FRAME_BYTES = 320  # 320 G.711 samples = 40 ms at 8 kHz
FRAME_SECONDS = FRAME_BYTES / 8000.0
MAX_BACKLOG_FRAMES = 5
ACCEPT_TIMEOUT = 0.5
CLIENT_RECV_BYTES = 1024

_log_lock = threading.Lock()


def log(msg: str) -> None:
    """Print a timestamped line without leaking audio content."""
    with _log_lock:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _config() -> AutonomousConfig:
    saved = CameraStreamCredentials.load(CREDENTIALS)
    return AutonomousConfig(
        connection_mode="lan",
        device_host=DEVICE_HOST,
        stream_port=DEVICE_PORT,
        channel=CHANNEL,
        stream=STREAM_ID,
        stream_key=saved.stream_key,
        auth_code=saved.auth_code,
    )


def _to_codec(pcma: bytes, codec: int | None) -> bytes | None:
    """Convert one 320-byte PCMA frame to the device's negotiated codec."""
    if codec == 4:  # A-law, the common Vimar case
        return pcma
    if codec == 5:  # mu-law
        return encode_g711(decode_g711(pcma, 4), 5)
    return None


class TalkServer:
    """One-client loopback talk bridge."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active = False
        self._listener: socket.socket | None = None
        self._talk: LanTalkSession | None = None
        self.port = TALK_PORT
        self.incoming_sink: Any = None
        self.incoming_frames = 0
        self.incoming_last = 0.0

    def start(self) -> None:
        """Bind the loopback listener and start accepting clients."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", TALK_PORT))
            sock.listen(4)
        except OSError as exc:
            log(f"talk listener cannot bind 127.0.0.1:{TALK_PORT}: {exc}")
            sock.close()
            return
        self.port = sock.getsockname()[1]
        sock.settimeout(ACCEPT_TIMEOUT)
        self._listener = sock
        log(f"talk listener on 127.0.0.1:{self.port} (no device traffic yet)")
        while not self._stop.is_set():
            try:
                client, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                if self._active:
                    log("talk client rejected: another talk session is active")
                    client.close()
                    continue
                self._active = True
            threading.Thread(
                target=self._handle_client,
                args=(client,),
                name="vimar-talk-client",
                daemon=True,
            ).start()

    def status(self) -> dict:
        """Return the loopback listener/talk session state."""
        return {
            "port": self.port,
            "listening": self._listener is not None,
            "active": self._active,
            "incoming_frames": self.incoming_frames,
            "incoming_last": self.incoming_last,
            "incoming_sink": self.incoming_sink is not None,
        }

    def stop(self) -> None:
        """Stop accepting and close the device talk session."""
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        talk = self._talk
        if talk is not None:
            try:
                talk.close()
            except Exception:
                pass

    def _handle_client(self, client: socket.socket) -> None:
        talk: LanTalkSession | None = None
        reader_stop = threading.Event()
        reader: threading.Thread | None = None
        try:
            client.settimeout(0.5)
            log("talk client connected, opening QVII talk session")
            talk = LanTalkSession(_config())
            talk.open()
            with self._lock:
                self._talk = talk
            log(f"talk session ready (codec {talk.codec})")

            reader = threading.Thread(
                target=self._read_talk,
                args=(talk, reader_stop, QuiiCFramePack(), talk.codec),
                name="vimar-talk-reader",
                daemon=True,
            )
            reader.start()

            buffer = bytearray()
            next_send = time.monotonic()
            while not self._stop.is_set():
                try:
                    data = client.recv(CLIENT_RECV_BYTES)
                except socket.timeout:
                    continue
                if not data:
                    break
                buffer.extend(data)
                if len(buffer) > FRAME_BYTES * MAX_BACKLOG_FRAMES:
                    drop = len(buffer) - FRAME_BYTES * MAX_BACKLOG_FRAMES
                    del buffer[:drop]
                    log(f"talk backlog: dropped {drop} bytes")

                while len(buffer) >= FRAME_BYTES:
                    chunk = bytes(buffer[:FRAME_BYTES])
                    del buffer[:FRAME_BYTES]
                    encoded = _to_codec(chunk, talk.codec)
                    if encoded is None:
                        log(f"unsupported talk codec {talk.codec}, closing")
                        return
                    now = time.monotonic()
                    if now < next_send:
                        time.sleep(min(next_send - now, 0.1))
                    talk.send_audio(encoded)
                    next_send = max(
                        next_send + FRAME_SECONDS, time.monotonic()
                    )
        except Exception as exc:  # device busy, network, protocol
            log(f"talk failed: {type(exc).__name__}: {exc}")
        finally:
            reader_stop.set()
            if reader is not None:
                reader.join(timeout=1.0)
            if talk is not None:
                try:
                    talk.close()
                except Exception:
                    pass
            with self._lock:
                self._talk = None
                self._active = False
            try:
                client.close()
            except OSError:
                pass

    def _read_talk(
        self,
        talk: LanTalkSession,
        stop: threading.Event,
        packer: QuiiCFramePack,
        codec: int | None,
    ) -> None:
        """Drain ACKs/keepalives and forward the visitor's talk audio."""
        while not stop.is_set():
            try:
                message = talk.recv_message(timeout=0.5)
            except TimeoutError:
                continue
            except Exception as exc:
                log(f"talk read ended: {type(exc).__name__}: {exc}")
                return
            if message.get("is_media"):
                payload = message.get("payload", b"")
                if isinstance(payload, bytes) and payload:
                    self._forward_talk_audio(payload, packer, codec)

    @staticmethod
    def _timestamp() -> tuple[int, int]:
        """Packed device-local calendar value + milliseconds (current time)."""
        now = datetime.now()
        stamp = (
            ((now.year - 2000) << 26)
            | (now.month << 22)
            | (now.day << 17)
            | (now.hour << 12)
            | (now.minute << 6)
            | now.second
        )
        return stamp, now.microsecond // 1000

    def _emit(self, sink: Any, audio: CameraAudioFrame) -> None:
        try:
            sink(audio)
        except Exception as exc:  # a dead RTSP session must not kill talk
            log(f"talk audio forward failed: {type(exc).__name__}: {exc}")
            return
        self.incoming_frames += 1
        self.incoming_last = time.time()

    def _forward_talk_audio(
        self,
        payload: bytes,
        packer: QuiiCFramePack,
        codec: int | None,
    ) -> None:
        """Forward talk-channel media to the bridge RTSP audio track."""
        sink = self.incoming_sink
        if sink is None:
            return

        frames = packer.feed(payload)
        for frame in frames:
            if frame.get("frame_tag") != 0xE3:
                continue
            try:
                audio = CameraAudioFrame.from_cpacket(frame)
            except ValueError:
                continue
            self._emit(sink, audio)
        if frames or starts_cpacket(payload, 0):
            return

        # Native compact talk frame: 00 00 01 F0 | tag | .. | len16 | payload
        if len(payload) >= 8 and payload[:4] == b"\x00\x00\x01\xf0":
            tag = payload[4]
            length = int.from_bytes(payload[6:8], "little")
            data = payload[8 : 8 + length]
            if not data or len(data) != length:
                return
            frame_codec = 4 if tag == 0x0E else 5 if tag == 0x0A else codec
            if frame_codec not in (4, 5):
                return
            stamp, ms = self._timestamp()
            self._emit(
                sink,
                CameraAudioFrame(
                    data=data,
                    codec=frame_codec,
                    sample_rate=8000,
                    channels=1,
                    frame_stamp=stamp,
                    milliseconds=ms,
                ),
            )
            return

        # Some firmware sends the raw G.711 payload without a CPacket header.
        if len(payload) in (160, 320, 640) and (codec in (4, 5)):
            stamp, ms = self._timestamp()
            self._emit(
                sink,
                CameraAudioFrame(
                    data=payload,
                    codec=codec,
                    sample_rate=8000,
                    channels=1,
                    frame_stamp=stamp,
                    milliseconds=ms,
                ),
            )


def main() -> int:
    """Run the loopback talk bridge until SIGTERM/SIGINT."""
    server = TalkServer()

    def _term(*_args) -> None:
        log("SIGTERM: closing talk bridge")
        server.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log(
        f"vimar talk bridge: {DEVICE_HOST}:{DEVICE_PORT} -> "
        f"127.0.0.1:{TALK_PORT}"
    )
    server.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
