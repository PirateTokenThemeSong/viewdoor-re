"""Offline tests for the always-on PCMA track and RTSP backchannel."""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime

from homeassistant import vimar_bridge
from homeassistant.vimar_bridge import AlwaysAudioRtspServer
from quii_helper.media.audio import CameraAudioFrame
from quii_helper.media.audio.g711 import decode_g711, encode_g711

SPS = b"\x67\x42\x00\x1e\x95\xa8\x28\x0f\x00\x44\xfc\xb8\x08\x80"
PPS = b"\x68\xce\x06\xe2"
IDR = b"\x65\x88\x84\x00\x0a\xff\xff"
AU = (
    b"\x00\x00\x00\x01" + SPS
    + b"\x00\x00\x00\x01" + PPS
    + b"\x00\x00\x00\x01" + IDR
)


def _request(sock: socket.socket, text: str) -> bytes:
    sock.sendall(text.encode())
    time.sleep(0.15)
    sock.settimeout(3)
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        if b"Content-Length:" in data:
            head, body = data.split(b"\r\n\r\n", 1)
            length = int(
                [
                    line
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length")
                ][0].split(b":")[1]
            )
            while len(body) < length:
                body += sock.recv(4096)
            data = head + b"\r\n\r\n" + body
    except socket.timeout:
        pass
    return data


def _read_rtp(sock: socket.socket) -> bytes:
    sock.settimeout(3)
    header = b""
    while len(header) < 4:
        header += sock.recv(4 - len(header))
    assert header[0] == 0x24
    length = int.from_bytes(header[2:4], "big")
    data = b""
    while len(data) < length:
        data += sock.recv(length - len(data))
    return data


def _frame(data: bytes, codec: int, stamp: int) -> CameraAudioFrame:
    return CameraAudioFrame(
        data=data,
        codec=codec,
        sample_rate=8000,
        channels=1,
        frame_stamp=stamp,
        milliseconds=0,
    )


def _stamp() -> int:
    now = datetime.now()
    return (
        ((now.year - 2000) << 26)
        | (now.month << 22)
        | (now.day << 17)
        | (now.hour << 12)
        | (now.minute << 6)
        | now.second
    )


def test_always_audio_track() -> None:
    """SDP/audio track are available before any E3 frame."""
    stamp = _stamp()
    server = AlwaysAudioRtspServer(
        host="127.0.0.1", port=0, path="/live", audio=True
    ).start()
    server.publish(AU)
    assert server.audio_format is not None
    assert server.audio_format.encoding == "PCMA"

    sock = socket.create_connection(("127.0.0.1", server.port))
    describe = _request(
        sock,
        f"DESCRIBE rtsp://127.0.0.1:{server.port}/live RTSP/1.0\r\n"
        "CSeq: 1\r\n\r\n",
    )
    assert describe.startswith(b"RTSP/1.0 200")
    assert b"m=audio" in describe and b"PCMA/8000/1" in describe

    setup = _request(
        sock,
        f"SETUP rtsp://127.0.0.1:{server.port}/live/trackID=1 RTSP/1.0\r\n"
        "CSeq: 2\r\nTransport: RTP/AVP/TCP;unicast;interleaved=2-3\r\n\r\n",
    )
    assert setup.startswith(b"RTSP/1.0 200")
    play = _request(
        sock,
        f"PLAY rtsp://127.0.0.1:{server.port}/live RTSP/1.0\r\n"
        f"CSeq: 3\r\nSession: {server.session_id}\r\n\r\n",
    )
    assert play.startswith(b"RTSP/1.0 200")

    alaw = b"\xd5" * 320
    server.publish_audio(_frame(alaw, 4, stamp))
    rtp = _read_rtp(sock)
    assert rtp[1] & 0x7F == 8
    assert rtp[12:] == alaw

    pcm = b"\x00\x00" * 160
    mulaw = encode_g711(pcm, 5)
    server.publish_audio(_frame(mulaw, 5, stamp))
    rtp = _read_rtp(sock)
    assert rtp[1] & 0x7F == 8
    assert rtp[12:] == encode_g711(decode_g711(mulaw, 5), 4)

    server.publish_audio(_frame(pcm, 9, stamp))
    rtp = _read_rtp(sock)
    assert rtp[1] & 0x7F == 8
    assert rtp[12:] == encode_g711(pcm, 4)

    assert server.audio_frames == 3
    sock.close()
    server.close()


def test_rtsp_backchannel_forwards_pcma() -> None:
    """A go2rtc-style sendonly track forwards RTP payload to the talk server."""
    stamp = _stamp()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    talk_port = listener.getsockname()[1]
    received: list[bytes] = []

    def fake_talk() -> None:
        conn, _ = listener.accept()
        conn.settimeout(3)
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                received.append(data)
        except socket.timeout:
            pass
        finally:
            conn.close()

    threading.Thread(target=fake_talk, daemon=True).start()
    vimar_bridge.TALK = True
    vimar_bridge.TALK_PORT = talk_port

    server = AlwaysAudioRtspServer(
        host="127.0.0.1", port=0, path="/live", audio=True
    ).start()
    server.publish(AU)

    sock = socket.create_connection(("127.0.0.1", server.port))
    describe = _request(
        sock,
        f"DESCRIBE rtsp://127.0.0.1:{server.port}/live RTSP/1.0\r\n"
        "CSeq: 1\r\n\r\n",
    )
    assert b"a=sendonly" in describe
    assert b"a=control:trackID=2" in describe

    setup = _request(
        sock,
        f"SETUP rtsp://127.0.0.1:{server.port}/live/trackID=2 RTSP/1.0\r\n"
        "CSeq: 2\r\nTransport: RTP/AVP/TCP;unicast;interleaved=4-5\r\n\r\n",
    )
    assert setup.startswith(b"RTSP/1.0 200")
    play = _request(
        sock,
        f"PLAY rtsp://127.0.0.1:{server.port}/live RTSP/1.0\r\n"
        f"CSeq: 3\r\nSession: {server.session_id}\r\n\r\n",
    )
    assert play.startswith(b"RTSP/1.0 200")

    payload = b"\xd5" * 320
    rtp = (
        bytes([0x80, 8])
        + b"\x00\x01"
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x01"
        + payload
    )
    sock.sendall(b"$\x04" + len(rtp).to_bytes(2, "big") + rtp)
    deadline = time.monotonic() + 3
    while not received and time.monotonic() < deadline:
        time.sleep(0.05)
    assert received
    assert b"".join(received) == payload

    sock.close()
    server.close()
    listener.close()
    _ = stamp


if __name__ == "__main__":
    test_always_audio_track()
    test_rtsp_backchannel_forwards_pcma()
