"""QUII talk channel with preview-audio routing (Vimar VIEW Door V2.2.11).

Derived from the View Door APK / the community local-preview experiment.
The important difference from a plain talk session is
``route_receive_to_video()``: after the talk channel is open, packet type 13
with ``enable=0`` tells the device to route the visitor audio to the **video
session** as E3 frames instead of the talk socket.  The bridge's RTSP audio
track already publishes those E3 frames.

The class also sends the 64 ms / 512-sample A-law talk frames used by the
Android trace.  Nothing here is printed with credentials or key material.
"""

from __future__ import annotations

import hashlib
import socket
import struct
import threading
import time
from typing import Any

from quii_helper.device.security.auth import encode_device_password
from quii_helper.media.audio.g711 import encode_g711
from quii_helper.protocols.quii.crypto import aes_cbc_crypt
from quii_helper.protocols.quii.live_envelope import (
    build_live_play_packet_from_body,
)
from quii_helper.protocols.quii.live_packets import build_live_play_packet
from quii_helper.protocols.tcp.codec.message_codec import (
    decode_tcp_header,
    decode_tcp_payload,
)
from quii_helper.protocols.tcp.transport.client import QuiiClient

TALK_SAMPLES_PER_PACKET = 512  # 64 ms at 8 kHz, as in the Android trace


class TalkConnection:
    """One QVII talk channel with preview-audio routing."""

    def __init__(
        self,
        host: str,
        password: str,
        key: str,
        channel: int = 65535,
    ) -> None:
        if not isinstance(channel, int) or not 0 <= channel <= 65535:
            raise ValueError("Invalid talk channel")
        self.channel = channel
        self.key = key.encode()
        self.client = QuiiClient(
            host,
            34567,
            "adminapp2",
            encode_device_password(password),
            f"/talk/idc={channel}&ap=2",
            key,
        )
        self.lock = threading.RLock()
        self.pending = bytearray()
        self.pending_alaw = bytearray()
        self.opened = False
        self.frames_sent = 0

    def packet(self, kind: int, enable: int = 0, codec: int = 0) -> bytes:
        """Build a native talk control request."""
        header = bytearray(32)
        header[0] = kind
        struct.pack_into("<Q", header, 1, int(time.time()))
        struct.pack_into("<H", header, 9, 32)
        struct.pack_into("<H", header, 11, self.channel)
        header[13] = enable
        if kind == 12:
            header[14] = codec
            struct.pack_into("<H", header, 15, 8000)
        return aes_cbc_crypt(
            bytes(header), self.key, crypto_mode=2
        ) + aes_cbc_crypt(
            hashlib.sha256(header).digest(), self.key, crypto_mode=2
        )

    def receive(self) -> tuple[Any, bytes]:
        """Read one talk command/media message and validate commands."""
        raw = self.client.recv_exact(32)
        header = decode_tcp_header(raw, crypto_mode=2, key=self.key)
        if not 0 <= header.read_size <= 1024 * 1024:
            raise ValueError("Unexpected audio packet size")
        payload = (
            decode_tcp_payload(
                header,
                self.client.recv_exact(header.read_size),
                crypto_mode=2,
                key=self.key,
            )
            if header.read_size
            else b""
        )
        if not header.is_media:
            size = header.header.raw_size if header.header.packet_type == 254 else 0
            digest = hashlib.sha256(header.header.raw + payload[:size]).digest()
            if payload[size : size + 32] != digest:
                raise ValueError("Audio response integrity check failed")
        return header, payload

    def open(self) -> None:
        """Open setup + talk session and enable both directions."""
        try:
            self.client.connect(timeout=4)
            self.client.transport.set_timeout(3)
            setup = bytearray(32)
            setup[0] = 169
            setup[9] = 2
            self.client.transport.send_all(setup)
            response = self.client.recv_setup()
            if (
                response["status"] != 0
                or response["crypto_mode"] != 2
                or response["raw"][11] != 1
            ):
                raise ValueError("Unsupported audio setup")
            packet = build_live_play_packet(
                self.client.username,
                self.client.password,
                self.client.path,
                packet_type=11,
                ext_len_low=self.channel & 255,
                ext_len_high=self.channel >> 8,
                play_param=0,
                crypto_mode=2,
                sha_mode=1,
                key=self.key,
                encrypt=True,
            )[2]
            self.client.transport.send_all(packet)
            header, _ = self.receive()
            if header.header.packet_type != 11 or header.header.raw[11] != 0:
                raise ValueError("Monitor rejected audio session")
            mask = int.from_bytes(header.header.raw[12:14], "little")
            if not mask & 1:
                raise ValueError("Monitor does not support the A-law profile")
            self.client.transport.send_all(self.packet(12, 1))
            header, _ = self.receive()
            if header.header.packet_type != 12 or header.header.raw[11] != 0:
                raise ValueError("Monitor rejected talk direction")
            self.client.transport.send_all(self.packet(13, 1))
            header, _ = self.receive()
            if header.header.packet_type != 13 or header.header.raw[11] != 0:
                raise ValueError("Monitor rejected listening direction")
            self.opened = True
        except BaseException:
            self.client.close()
            raise

    def route_receive_to_video(self) -> None:
        """Route incoming audio to the video E3 stream (the key command)."""
        with self.lock:
            if not self.opened:
                raise RuntimeError("Audio session is not open")
            self.client.transport.send_all(self.packet(13, 0))
            header, _ = self.receive()
            if header.header.packet_type != 13 or header.header.raw[11] != 0:
                raise ValueError("Monitor rejected preview audio routing")

    def _send_media(self, encoded: bytes) -> None:
        body = struct.pack(
            "<IBBH", 0xF0010000, 14, 2, len(encoded)
        ) + encoded
        header = bytearray(32)
        header[0] = 162
        struct.pack_into("<Q", header, 1, int(time.time()))
        struct.pack_into("<H", header, 9, 32)
        struct.pack_into("<I", header, 11, len(body))
        packet = (
            aes_cbc_crypt(bytes(header), self.key, crypto_mode=2)
            + aes_cbc_crypt(body[:32], self.key, crypto_mode=2)
            + body[32:]
        )
        self.client.transport.send_all(packet)
        self.frames_sent += 1

    def send_pcm(self, pcm: bytes) -> None:
        """Encode PCM16 LE and send 64 ms A-law blocks."""
        with self.lock:
            if not self.opened:
                return
            if len(pcm) % 2 or len(pcm) > 32000:
                raise ValueError("Invalid microphone block")
            self.pending.extend(encode_g711(pcm, 4))
            while len(self.pending) >= TALK_SAMPLES_PER_PACKET:
                audio = bytes(self.pending[:TALK_SAMPLES_PER_PACKET])
                del self.pending[:TALK_SAMPLES_PER_PACKET]
                self._send_media(audio)

    def send_alaw(self, alaw: bytes) -> None:
        """Send already encoded A-law blocks (RTSP backchannel)."""
        with self.lock:
            if not self.opened:
                return
            self.pending_alaw.extend(alaw)
            while len(self.pending_alaw) >= TALK_SAMPLES_PER_PACKET:
                audio = bytes(
                    self.pending_alaw[:TALK_SAMPLES_PER_PACKET]
                )
                del self.pending_alaw[:TALK_SAMPLES_PER_PACKET]
                self._send_media(audio)

    def keepalive(self) -> None:
        """Native stream-control heartbeat while talk media is active."""
        with self.lock:
            if self.opened:
                self.client.transport.send_all(
                    build_live_play_packet_from_body(
                        b"",
                        packet_type=0,
                        play_param=0,
                        crypto_mode=2,
                        sha_mode=1,
                        key=self.key,
                        encrypt=True,
                    )[2]
                )

    def close(self) -> None:
        """Stop both directions and release the socket."""
        with self.lock:
            if self.opened:
                for kind in (12, 13):
                    try:
                        self.client.transport.send_all(self.packet(kind, 0))
                    except OSError:
                        pass
            self.opened = False
            self.pending.clear()
            self.pending_alaw.clear()
            try:
                self.client.close()
            except OSError:
                pass
