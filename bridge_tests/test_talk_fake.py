"""Offline test for the loopback PCMA -> talk bridge."""

from __future__ import annotations

import socket
import threading
import time

import homeassistant.vimar_talk as talk_module
from quii_helper.media.frames.cframe_pack import QuiiCFramePack


class FakeTalk:
    """Capture frames instead of opening a doorbell connection."""

    codec = 4

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    def open(self) -> "FakeTalk":
        return self

    def send_audio(self, data: bytes) -> None:
        self.frames.append(data)

    def recv_message(self, timeout: float = 1.0) -> None:
        time.sleep(min(timeout, 0.05))
        raise TimeoutError

    def close(self) -> None:
        self.closed = True


def test_incoming_talk_audio_is_forwarded() -> None:
    """Visitor audio on the talk socket reaches the RTSP audio sink."""

    class IncomingTalk:
        codec = 4

        def __init__(self) -> None:
            self.sent = False

        def recv_message(self, timeout: float = 1.0) -> dict:
            if not self.sent:
                self.sent = True
                return {"is_media": True, "payload": bytes([0xD5]) * 320}
            time.sleep(min(timeout, 0.05))
            raise TimeoutError

    server = talk_module.TalkServer()
    stop = threading.Event()
    frames: list = []

    def sink(frame) -> None:
        frames.append(frame)
        stop.set()

    server.incoming_sink = sink
    server._read_talk(IncomingTalk(), stop, QuiiCFramePack(), 4)
    assert len(frames) == 1
    assert frames[0].codec == 4
    assert frames[0].data == bytes([0xD5]) * 320
    assert server.incoming_frames == 1


def main() -> None:
    fake = FakeTalk()
    talk_module.LanTalkSession = lambda _cfg: fake
    talk_module._config = lambda: None
    talk_module.TALK_PORT = 0

    server = talk_module.TalkServer()
    threading.Thread(target=server.start, daemon=True).start()
    deadline = time.monotonic() + 3
    while not server.port and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.port

    client = socket.create_connection(("127.0.0.1", server.port))
    client.sendall(b"\xd5" * 640)
    time.sleep(1.0)
    client.close()
    time.sleep(0.3)
    server.stop()

    assert len(fake.frames) == 2
    assert all(len(frame) == 320 for frame in fake.frames)
    assert all(frame == b"\xd5" * 320 for frame in fake.frames)
    assert fake.closed


def test_talk_loopback() -> None:
    main()


if __name__ == "__main__":
    test_talk_loopback()
    test_incoming_talk_audio_is_forwarded()
