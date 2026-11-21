"""Offline tests for the talk route command and microphone forwarding."""

from __future__ import annotations

import socket
import threading
import time

from homeassistant import vimar_talk
from homeassistant.vimar_route import TalkConnection
from quii_helper.protocols.quii.crypto import aes_cbc_crypt


def test_route_packet_type13_disable() -> None:
    """The audio route is type 13 with enable=0 on channel 65535."""
    key = "K" * 32
    connection = TalkConnection("127.0.0.1", "A" * 64, key)
    packet = connection.packet(13, 0)
    header = aes_cbc_crypt(packet[:32], key.encode(), crypto_mode=2, decrypt=True)
    assert header[0] == 13
    assert header[11] | (header[12] << 8) == 65535
    assert header[13] == 0


def test_talk_server_forwards_microphone_pcma() -> None:
    """The RTSP backchannel PCMA reaches the shared talk connection."""

    class FakeRoute:
        def __init__(self) -> None:
            self.alaw = bytearray()

        def send_alaw(self, data: bytes) -> None:
            self.alaw.extend(data)

    route = FakeRoute()
    server = vimar_talk.TalkServer()
    server._route = route  # type: ignore[assignment]
    server._route_ready.set()

    left, right = socket.socketpair()
    worker = threading.Thread(target=server._handle_client, args=(left,))
    worker.start()
    right.sendall(b"\xd5" * 320)
    time.sleep(0.2)
    right.close()
    worker.join(timeout=2)
    assert bytes(route.alaw) == b"\xd5" * 320
    assert server._mic_active is False


if __name__ == "__main__":
    test_route_packet_type13_disable()
    test_talk_server_forwards_microphone_pcma()
