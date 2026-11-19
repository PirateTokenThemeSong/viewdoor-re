#!/usr/bin/env python3
"""Vimar VIEW Door -> RTSP bridge with a single, lazy upstream session.

Architecture:

    go2rtc / any RTSP client
              |  (many TCP connections, each a normal RTSP session)
              v
        [front :8556]  proxy, counts viewers
              |  (1 TCP connection per viewer, but...)
              v
        [inner :8557]  quii-helper RTSP server
              |  (exactly ONE QVII device session)
              v
        192.168.1.50:34567

The inner server is started only when the first viewer arrives and is closed
a few seconds after the last viewer leaves.  A hard lock guarantees that at
most one QVII session ever exists, even across restarts and device errors.

Environment:
  CREDENTIALS        stream_credentials.json
  RTSP_BIND/RTSP_PORT  front listener (0.0.0.0:8556)
  INNER_PORT         loopback RTSP server (8557)
  STATUS_PORT        loopback JSON status (8558)
  LINGER_SECONDS     keep the device session after the last viewer (12)
  COOLDOWN_SECONDS   pause between a close and the next open (3)
  START_TIMEOUT      wait for the first keyframe/SPS before letting viewers in
  STALL_SECONDS      restart if no video for this long while viewers are on
  AUDIO              1 to publish native G.711 audio when the doorbell sends it
"""

from __future__ import annotations

import json
import os
import select
import signal
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import quii_helper.camera.streaming.rtsp as camera_rtsp
from quii_helper import AutonomousConfig, Camera, CameraStreamCredentials
from quii_helper.streaming.rtp.g711 import G711Format
from quii_helper.streaming.rtsp import RtspH264Server

CREDENTIALS = os.environ.get("CREDENTIALS", "/config/vimar/stream_credentials.json")
BIND = os.environ.get("RTSP_BIND", "0.0.0.0")
PORT = int(os.environ.get("RTSP_PORT", "8556"))
INNER_HOST = "127.0.0.1"
INNER_PORT = int(os.environ.get("INNER_PORT", "8557"))
STATUS_PORT = int(os.environ.get("STATUS_PORT", "8558"))
LINGER = float(os.environ.get("LINGER_SECONDS", "12"))
COOLDOWN = float(os.environ.get("COOLDOWN_SECONDS", "3"))
START_TIMEOUT = float(os.environ.get("START_TIMEOUT", "20"))
STOP_TIMEOUT = float(os.environ.get("STOP_TIMEOUT", "12"))
STALL = float(os.environ.get("STALL_SECONDS", "12"))
BLACK_CHECK = float(os.environ.get("BLACK_CHECK_SECONDS", "10"))
BLACK_BYTES = int(os.environ.get("BLACK_BYTES", "2000"))
GO2RTC_API = os.environ.get("GO2RTC_API", "http://127.0.0.1:1984").rstrip("/")
GO2RTC_SRC = os.environ.get("GO2RTC_SRC", "cancelletto")
MAX_SESSION = float(os.environ.get("MAX_SESSION_SECONDS", "60"))
BLOCK_HOLD = float(os.environ.get("BLOCK_HOLD_SECONDS", "20"))
UNBLOCK_IDLE = float(os.environ.get("UNBLOCK_IDLE_SECONDS", "15"))
DISABLED_H264 = os.environ.get("DISABLED_H264", "/config/vimar/disabled.h264")
MAX_CLIENTS = int(os.environ.get("MAX_CLIENTS", "64"))
DEVICE_HOST = os.environ.get("CAMERA_DEVICE_HOST", "192.168.1.50")
DEVICE_PORT = int(os.environ.get("CAMERA_STREAM_PORT", "34567"))
CHANNEL = int(os.environ.get("CAMERA_CHANNEL", "1"))
STREAM_ID = int(os.environ.get("CAMERA_STREAM_ID", "2"))
AUDIO = os.environ.get("AUDIO", "1").strip().lower() not in {
    "0", "false", "no", "off", ""
}

_log_lock = threading.Lock()


def log(msg: str) -> None:
    with _log_lock:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class OptionalAudioRtspServer(RtspH264Server):
    """RTSP server that adds the audio track only if the camera sends audio.

    quii-helper's stock server advertises G.711 only after an E3 frame and
    answers 503 to DESCRIBE until then.  The doorbell is video-only in normal
    preview, so that would make the stream unplayable.  Here the audio track
    stays hidden (video-only SDP) until the first valid E3 frame; from then
    on new viewers get A/V and existing viewers stay video-only.
    """

    def __init__(self, *args, **kwargs) -> None:
        # CameraRtspStream passes audio=True; keep the track hidden until the
        # doorbell actually sends an E3 frame.  The parent dataclass has no
        # __post_init__, so forcing the field here is what takes effect.
        kwargs["audio"] = False
        super().__init__(*args, **kwargs)
        self.audio_frames = 0
        self.audio_error = None

    def publish_audio(self, frame) -> None:
        if not self._running:
            return
        try:
            profile = G711Format.from_frame(frame)
        except ValueError as exc:
            self.audio_error = str(exc)
            return
        if self.audio_format is not None and self.audio_format != profile:
            self.audio_error = "audio format changed"
            return
        if not self.audio:
            self.audio = True
            log(f"audio track enabled ({profile.encoding}/8000 mono)")
        try:
            super().publish_audio(frame)
        except Exception as exc:  # keep video alive on a bad audio frame
            self.audio_error = f"{type(exc).__name__}: {exc}"
            if self.audio_frames == 0:
                log(f"audio frame dropped: {self.audio_error}")
            return
        self.audio_frames += 1


# CameraRtspStream instantiates the class from its own module namespace.
camera_rtsp.RtspH264Server = OptionalAudioRtspServer


class Bridge:
    def __init__(self) -> None:
        self.cond = threading.Condition(threading.RLock())
        self.clients = 0
        self.session = None
        self.camera = None
        self.stopping = False
        self.timer = None
        self.failures = 0
        self.error_streak = 0
        self.starts = 0
        self.stops = 0
        self.next_attempt_at = 0.0
        self.last_error = None
        self.started_at = 0.0
        self.last_reason = ""
        self.black_hits = 0
        self.last_frame_bytes = 0
        self.last_black_check = 0.0
        self.blocked = False
        self.last_client_at = time.time()
        self.dummy = None
        self.dummy_au = None
        self.dummy_stop = None
        self.dummy_lock = threading.RLock()
        self.block_consumers = None
        self.last_consumers = None
        self.consumer_error = None
        self.client_socks: set[socket.socket] = set()
        self.client_lock = threading.Lock()
        self.shutdown = threading.Event()

    # -- session management ------------------------------------------------
    def _spawn(self):
        saved = CameraStreamCredentials.load(CREDENTIALS)
        cfg = AutonomousConfig(
            connection_mode="lan",
            device_host=DEVICE_HOST,
            stream_port=DEVICE_PORT,
            channel=CHANNEL,
            stream=STREAM_ID,
        )
        camera = Camera(
            config=cfg,
            connection_mode="lan",
            device_id=saved.device_id,
            stream_key=saved.stream_key,
            auth_code=saved.auth_code,
            status=lambda m: log(f"[quii] {m}"),
        )
        stream = camera.serve_rtsp(
            host=INNER_HOST,
            port=INNER_PORT,
            path="/live",
            audio=AUDIO,
            reconnect=False,
        )
        log(
            f"doorbell session opened on {DEVICE_HOST}:{DEVICE_PORT} "
            f"(audio {'on' if AUDIO else 'off'})"
        )
        return camera, stream

    def ensure_started(self):
        with self.cond:
            while self.stopping and not self.shutdown.is_set():
                self.cond.wait(0.5)
            if self.shutdown.is_set():
                return None
            stream = self.session
            if stream is None:
                if self.next_attempt_at - time.monotonic() > 0:
                    # In backoff: close the client at once so go2rtc retries
                    # instead of waiting on a silent socket (i/o timeout).
                    return None
                try:
                    camera, stream = self._spawn()
                except Exception as exc:  # device busy / network / credentials
                    self.failures += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    backoff = min(30.0, COOLDOWN * (2 ** min(self.failures, 4)))
                    self.next_attempt_at = time.monotonic() + backoff
                    log(
                        f"doorbell session failed ({self.failures}): "
                        f"{self.last_error} (retry in {backoff:.0f}s)"
                    )
                    return None
                self.camera, self.session = camera, stream
                self.starts += 1
                self.started_at = time.time()
                self.failures = 0
                self.last_error = None

        # Return immediately: the inner RTSP server is already listening and
        # answers DESCRIBE with 503 + Retry-After until SPS/PPS are available.
        # Holding the client here made go2rtc's offer read time out.
        return stream

    def _wait_thread_dead(self, stream) -> None:
        thread = getattr(stream, "_thread", None)
        deadline = time.monotonic() + STOP_TIMEOUT
        while (
            thread is not None
            and thread.is_alive()
            and time.monotonic() < deadline
        ):
            time.sleep(0.2)

    def _close_session(self, reason: str, drop_clients: bool) -> None:
        with self.cond:
            if self.stopping or self.session is None:
                return
            self.stopping = True
            self.last_reason = reason
            stream, self.session = self.session, None
        log(f"closing doorbell session ({reason})")
        try:
            stream.close()
        except Exception as exc:
            log(f"close error: {exc}")
        self._wait_thread_dead(stream)
        if drop_clients:
            self._drop_clients()
        # Stay in the stopping state during the cooldown so no new session can
        # be opened while the doorbell is still releasing its single stream.
        delay = COOLDOWN
        if reason in ("error", "stall"):
            self.error_streak += 1
            delay = min(60.0, 5.0 * (2 ** min(self.error_streak - 1, 3)))
            log(
                f"device backoff {delay:.0f}s "
                f"(consecutive failures: {self.error_streak})"
            )
        time.sleep(delay)
        with self.cond:
            self.stopping = False
            self.stops += 1
            self.cond.notify_all()
        log("doorbell session closed")

    def _drop_clients(self) -> None:
        with self.client_lock:
            socks = list(self.client_socks)
        for sock in socks:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # -- viewer accounting -------------------------------------------------
    def viewer_joined(self) -> bool:
        with self.cond:
            if self.clients >= MAX_CLIENTS:
                return False
            self.clients += 1
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            log(f"viewer joined (clients={self.clients})")
            return True

    def viewer_left(self) -> None:
        self.last_client_at = time.time()
        with self.cond:
            self.clients = max(0, self.clients - 1)
            log(f"viewer left (clients={self.clients})")
            if self.clients == 0 and self.session is not None and not self.stopping:
                self.timer = threading.Timer(LINGER, self._idle_timeout)
                self.timer.daemon = True
                self.timer.start()
                log(f"idle timer armed ({LINGER:.0f}s)")

    def _idle_timeout(self) -> None:
        with self.cond:
            self.timer = None
            if self.clients > 0:
                return
        self._close_session("idle", drop_clients=False)

    def _probe_frame_bytes(self) -> int | None:
        """Ask go2rtc for a JPEG frame and return its size (None on error)."""
        import urllib.request

        url = f"{GO2RTC_API}/api/frame.jpeg?src={GO2RTC_SRC}"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                return len(resp.read())
        except Exception:
            return None

    # -- monitor -----------------------------------------------------------
    def _probe_consumers(self) -> int | None:
        if not GO2RTC_API or not GO2RTC_SRC:
            return None
        try:
            with urllib.request.urlopen(
                f"{GO2RTC_API}/api/streams", timeout=3
            ) as resp:
                data = json.load(resp)
            stream = data.get(GO2RTC_SRC) or {}
            self.consumer_error = None
            return len(stream.get("consumers") or [])
        except Exception as exc:
            self.consumer_error = f"{type(exc).__name__}: {exc}"
            return None

    def _unblock(self, reason: str) -> None:
        with self.dummy_lock:
            self.blocked = False
            self.block_consumers = None
            self.last_consumers = None
            srv, self.dummy = self.dummy, None
            stop, self.dummy_stop = self.dummy_stop, None
        if stop is not None:
            stop.set()
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass
        log(f"unblocked ({reason})")

    def _check_unblock(self) -> None:
        consumers = self._probe_consumers()
        if consumers is not None:
            if consumers != self.last_consumers:
                log(
                    f"blocked: consumers {self.last_consumers} -> {consumers} "
                    f"(baseline {self.block_consumers})"
                )
            self.last_consumers = consumers
            if self.block_consumers is None or consumers < self.block_consumers:
                self.block_consumers = consumers
            elif consumers > self.block_consumers:
                # A new WebRTC viewer (phone, new tab) asked for the stream:
                # treat it as a deliberate watch request.
                self._unblock(f"new viewer {consumers}>{self.block_consumers}")
                return
        if self.clients == 0 and time.time() - self.last_client_at >= UNBLOCK_IDLE:
            self._unblock(f"no viewers for {UNBLOCK_IDLE:.0f}s")

    def _load_disabled(self) -> bytes | None:
        try:
            with open(DISABLED_H264, "rb") as fh:
                data = fh.read()
            if data:
                log(f"disabled screen loaded ({len(data)} B)")
                return data
        except OSError as exc:
            log(f"disabled screen not available: {exc}")
        return None

    def ensure_dummy(self):
        """RTSP server showing the 'STREAM DISATTIVATO' frame."""
        with self.dummy_lock:
            if not self.blocked:
                return None
            if self.dummy is not None:
                return self.dummy
            srv = None
            for _ in range(25):
                try:
                    srv = RtspH264Server(
                        host=INNER_HOST, port=INNER_PORT, path="/live"
                    ).start()
                    break
                except OSError:
                    time.sleep(0.2)
            if srv is None:
                log("cannot start disabled-screen RTSP server")
                return None
            self.dummy = srv
            if self.dummy_au is None:
                self.dummy_au = self._load_disabled()
            if self.dummy_au:
                try:
                    srv.publish(self.dummy_au)
                except Exception as exc:
                    log(f"disabled screen publish failed: {exc}")
            self.dummy_stop = threading.Event()
            thread = threading.Thread(
                target=self._publish_disabled,
                args=(srv, self.dummy_stop),
                daemon=True,
            )
            thread.start()
            log("disabled screen serving on the inner RTSP")
            return srv

    def _publish_disabled(self, srv, stop_event: threading.Event) -> None:
        while not stop_event.is_set() and not self.shutdown.is_set():
            if self.dummy_au:
                try:
                    srv.publish(self.dummy_au)
                except Exception:
                    return
            stop_event.wait(1.0)

    def monitor(self) -> None:
        while not self.shutdown.is_set():
            time.sleep(2)
            if self.blocked:
                self._check_unblock()
                continue
            with self.cond:
                stream = self.session
                clients = self.clients
            if stream is None:
                continue
            error = getattr(stream, "_error", None)
            thread = getattr(stream, "_thread", None)
            dead = thread is not None and not thread.is_alive()
            if error is not None or dead:
                log(f"session ended unexpectedly: {error!r}")
                self._close_session("error", drop_clients=True)
                continue
            # Hard cap: never let a viewer hold the doorbell for more than
            # MAX_SESSION seconds (it blocks the call button / other features).
            # After the cap the session is closed and no new one is allowed
            # until the viewers actually leave (idle for UNBLOCK_IDLE seconds).
            if (
                MAX_SESSION > 0
                and clients > 0
                and time.time() - self.started_at >= MAX_SESSION
            ):
                log(
                    f"max session duration {MAX_SESSION:.0f}s reached, "
                    "closing and blocking new sessions"
                )
                self.blocked = True
                self.last_consumers = None
                self.block_consumers = self._probe_consumers()
                self._close_session("max_duration", drop_clients=True)
                self.ensure_dummy()
                continue

            if (
                stream.server.video_ready.is_set()
                and stream.server.last_video_at
                and time.time() - self.started_at > 30
            ):
                # healthy for a while: forgive previous failures
                self.error_streak = 0
            if (
                clients > 0
                and stream.server.video_ready.is_set()
                and stream.server.last_video_at
                and time.monotonic() - stream.server.last_video_at > STALL
            ):
                log(f"no video for >{STALL:.0f}s, restarting")
                self._close_session("stall", drop_clients=True)
                continue

            # The doorbell can go black on a long-lived session (device-side
            # blanking or the Vimar app taking the single stream). A fresh
            # session fixes it, so detect black frames and cycle the session.
            if (
                clients > 0
                and stream.server.video_ready.is_set()
                and time.time() - self.started_at > 20
                and time.monotonic() - self.last_black_check >= BLACK_CHECK
            ):
                self.last_black_check = time.monotonic()
                size = self._probe_frame_bytes()
                if size is not None:
                    self.last_frame_bytes = size
                    if size < BLACK_BYTES:
                        self.black_hits += 1
                        log(
                            f"black frame detected ({size} B, "
                            f"{self.black_hits}/2)"
                        )
                        if self.black_hits >= 2:
                            self.black_hits = 0
                            log("session is black, cycling the doorbell session")
                            self._close_session("black", drop_clients=True)
                            continue
                    else:
                        self.black_hits = 0

    # -- proxy -------------------------------------------------------------
    def handle_client(self, client: socket.socket) -> None:
        if not self.viewer_joined():
            client.close()
            return
        upstream = None
        try:
            if self.blocked:
                if self.ensure_dummy() is None:
                    return
            elif self.ensure_started() is None:
                return
            try:
                upstream = socket.create_connection(
                    (INNER_HOST, INNER_PORT), timeout=10
                )
            except OSError as exc:
                log(f"inner connect failed: {exc}")
                return
            with self.client_lock:
                self.client_socks.add(client)
                self.client_socks.add(upstream)
            self._relay(client, upstream)
        finally:
            with self.client_lock:
                self.client_socks.discard(client)
                if upstream is not None:
                    self.client_socks.discard(upstream)
            for sock in (client, upstream):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            self.viewer_left()

    def _relay(self, a: socket.socket, b: socket.socket) -> None:
        a.setblocking(False)
        b.setblocking(False)
        peers = {a: b, b: a}
        while not self.shutdown.is_set():
            try:
                readable, _, _ = select.select([a, b], [], [], 30)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            for sock in readable:
                try:
                    data = sock.recv(65536)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                try:
                    peers[sock].sendall(data)
                except OSError:
                    return

    # -- status ------------------------------------------------------------
    def status(self) -> dict:
        with self.cond:
            stream = self.session
            clients = self.clients
            return {
                "clients": clients,
                "inner_clients": stream.server.client_count if stream else 0,
                "session": stream is not None,
                "stopping": self.stopping,
                "ready": bool(stream and stream.server.video_ready.is_set()),
                "sps_pps": bool(stream and stream.server.parameter_sets),
                "audio_enabled": AUDIO,
                "audio": bool(stream and stream.server.audio_format is not None),
                "audio_format": getattr(
                    getattr(stream, "server", None), "audio_format", None
                ).encoding
                if stream is not None
                and stream.server.audio_format is not None
                else None,
                "audio_frames": int(
                    getattr(getattr(stream, "server", None), "audio_frames", 0)
                ),
                "audio_error": getattr(
                    getattr(stream, "server", None), "audio_error", None
                ),
                "uptime_s": round(time.time() - self.started_at, 1) if stream else 0,
                "starts": self.starts,
                "stops": self.stops,
                "failures": self.failures,
                "last_error": self.last_error,
                "last_reason": self.last_reason,
                "last_frame_bytes": self.last_frame_bytes,
                "black_hits": self.black_hits,
                "blocked": self.blocked,
                "dummy": self.dummy is not None,
                "consumers": self.last_consumers,
                "block_consumers": self.block_consumers,
                "consumer_error": self.consumer_error,
                "max_session_s": MAX_SESSION,
                "rtsp": f"rtsp://{BIND}:{PORT}/live",
            }


def status_server(bridge: Bridge) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps(bridge.status(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # silence
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", STATUS_PORT), Handler)
    httpd.daemon_threads = True
    log(f"status on http://127.0.0.1:{STATUS_PORT}/status")
    httpd.serve_forever()


def main() -> int:
    log(
        f"bridge: front {BIND}:{PORT} -> inner {INNER_HOST}:{INNER_PORT} "
        f"-> {DEVICE_HOST}:{DEVICE_PORT} (linger {LINGER:.0f}s)"
    )
    bridge = Bridge()

    def _term(*_args) -> None:
        log("SIGTERM: closing")
        bridge.shutdown.set()
        with bridge.cond:
            stream = bridge.session
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if bridge.dummy is not None:
            try:
                bridge.dummy.close()
            except Exception:
                pass
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    threading.Thread(target=status_server, args=(bridge,), daemon=True).start()
    threading.Thread(target=bridge.monitor, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((BIND, PORT))
    sock.listen(64)
    sock.settimeout(0.5)
    log(f"RTSP listening on rtsp://{BIND}:{PORT}/live")

    while not bridge.shutdown.is_set():
        try:
            client, addr = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(
            target=bridge.handle_client, args=(client,), daemon=True
        ).start()

    return 0


if __name__ == "__main__":
    sys.exit(main())
