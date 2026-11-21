#!/usr/bin/env python3
"""QVII talk + preview-audio routing for the Vimar VIEW Door.

The doorbell sends the visitor's audio on the video session only after the talk
channel has been opened and packet type 13 has been sent with ``enable=0``
(``route_receive_to_video``).  This is the behaviour of the Vimar app's
``QVTalkListen`` and of the community local-preview experiment.

The server binds 127.0.0.1 only.  The bridge calls ``start_route`` when a video
session becomes ready; that opens the talk channel, routes the audio to the
video E3 stream and keeps the talk session alive with 64 ms silence blocks.
The RTSP backchannel client sends the microphone as PCMA and is forwarded on
the same talk channel.  Incoming visitor audio is published by the bridge's
normal E3 audio track, not by this socket.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time

from quii_helper import CameraStreamCredentials

try:  # package import (tests / integration)
    from .vimar_route import TalkConnection
except ImportError:  # script import (/config/vimar/serve_rtsp.py)
    try:
        from vimar_route import TalkConnection
    except ImportError:  # pragma: no cover - optional backend
        TalkConnection = None  # type: ignore[assignment]

TALK_PORT = int(os.environ.get("TALK_PORT", "8560"))
CREDENTIALS = os.environ.get(
    "CREDENTIALS", "/config/vimar/stream_credentials.json"
)
DEVICE_HOST = os.environ.get("CAMERA_DEVICE_HOST", "192.168.1.50")
DEVICE_PORT = int(os.environ.get("CAMERA_STREAM_PORT", "34567"))
CHANNEL = int(os.environ.get("CAMERA_CHANNEL", "1"))
STREAM_ID = int(os.environ.get("CAMERA_STREAM_ID", "2"))

ACCEPT_TIMEOUT = 0.5
CLIENT_RECV_BYTES = 1024
SILENCE_PCM = bytes(1024)  # 512 samples / 64 ms
SILENCE_SECONDS = 0.064

_log_lock = threading.Lock()


def log(msg: str) -> None:
    """Print a timestamped line without leaking audio content."""
    with _log_lock:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class TalkServer:
    """Loopback microphone listener + one routed talk session."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self.port = TALK_PORT
        self._route: TalkConnection | None = None
        self._route_lock = threading.RLock()
        self._route_ready = threading.Event()
        self._silence_stop = threading.Event()
        self._mic_active = False
        self.route_error: str | None = None
        self.preview_ready: threading.Event | None = None

    def start(self) -> None:
        """Bind the loopback listener and accept microphone clients."""
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
            threading.Thread(
                target=self._handle_client,
                args=(client,),
                name="vimar-talk-client",
                daemon=True,
            ).start()

    def start_route(self, preview_ready: threading.Event) -> None:
        """Wait for video, then open the talk channel and route audio to E3."""
        self.preview_ready = preview_ready
        threading.Thread(
            target=self._ensure_route,
            name="vimar-talk-route",
            daemon=True,
        ).start()

    def _ensure_route(self) -> None:
        if TalkConnection is None:
            self.route_error = "vimar_route.py not importable"
            return
        ready = self.preview_ready
        if ready is not None and not ready.wait(30):
            self.route_error = "video session not ready"
            log("talk route: video session not ready")
            return
        with self._route_lock:
            if self._stop.is_set() or self._route is not None:
                return
            try:
                saved = CameraStreamCredentials.load(CREDENTIALS)
                route = TalkConnection(
                    DEVICE_HOST, saved.auth_code, saved.stream_key
                )
                route.open()
                route.route_receive_to_video()
                self._route = route
                self.route_error = None
                self._route_ready.set()
                self._silence_stop.clear()
                threading.Thread(
                    target=self._silence_loop,
                    args=(route,),
                    name="vimar-talk-silence",
                    daemon=True,
                ).start()
                log("talk route active: preview E3 audio enabled")
            except Exception as exc:
                self.route_error = f"{type(exc).__name__}: {exc}"
                log(f"talk route failed: {self.route_error}")
                try:
                    route.close()
                except Exception:
                    pass

    def _silence_loop(self, route: TalkConnection) -> None:
        """Keep the talk session alive with 64 ms silence when silent."""
        while not self._stop.is_set() and not self._silence_stop.is_set():
            if not self._mic_active:
                try:
                    route.send_pcm(SILENCE_PCM)
                except Exception as exc:
                    log(f"talk silence failed: {type(exc).__name__}: {exc}")
                    return
            time.sleep(SILENCE_SECONDS)

    def stop_route(self) -> None:
        """Close the routed talk session (video session can stay open)."""
        self._silence_stop.set()
        self._route_ready.clear()
        with self._route_lock:
            route, self._route = self._route, None
        if route is not None:
            try:
                route.close()
            except Exception:
                pass
            log("talk route closed")

    def status(self) -> dict:
        """Return listener, route and microphone state."""
        return {
            "port": self.port,
            "listening": self._listener is not None,
            "active": self._route is not None,
            "mic_active": self._mic_active,
            "route_error": self.route_error,
            "route_ready": self._route_ready.is_set(),
        }

    def stop(self) -> None:
        """Stop accepting and close the routed talk session."""
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self.stop_route()

    def _handle_client(self, client: socket.socket) -> None:
        """Forward RTSP backchannel PCMA to the shared talk channel."""
        route: TalkConnection | None = None
        try:
            client.settimeout(0.5)
            if not self._route_ready.wait(10):
                log("talk client connected before the route is ready")
                return
            with self._route_lock:
                route = self._route
            if route is None:
                log("talk client: no routed session")
                return
            self._mic_active = True
            log("talk microphone client connected")
            while not self._stop.is_set():
                try:
                    data = client.recv(CLIENT_RECV_BYTES)
                except socket.timeout:
                    continue
                if not data:
                    break
                route.send_alaw(data)
        except Exception as exc:
            log(f"talk microphone failed: {type(exc).__name__}: {exc}")
        finally:
            self._mic_active = False
            try:
                client.close()
            except OSError:
                pass


def main() -> int:
    """Run the loopback talk server until SIGTERM/SIGINT."""
    server = TalkServer()

    def _term(*_args) -> None:
        log("SIGTERM: closing talk server")
        server.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log(
        f"vimar talk server: {DEVICE_HOST}:{DEVICE_PORT} -> "
        f"127.0.0.1:{TALK_PORT}"
    )
    server.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
