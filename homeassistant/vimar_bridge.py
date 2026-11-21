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
  AUDIO              1 to advertise a PCMA 8 kHz track and forward E3 audio
  TALK               1 to start the loopback PCMA -> QVII talk bridge
  TALK_PORT          loopback talk TCP port (8560, read by vimar_talk.py)
  CLOUD_ACCOUNT      account JSON used to refresh a rotated stream key
  KEY_REFRESH        1 to auto-refresh the stream key after silent failures
  KEY_REFRESH_AFTER  consecutive failures before a refresh (1)
  KEY_REFRESH_COOLDOWN  seconds between cloud refreshes (600)
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
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from urllib.parse import urlsplit

import quii_helper.camera.streaming.rtsp as camera_rtsp
import quii_helper.streaming.rtsp.server as rtsp_server
from quii_helper import AutonomousConfig, Camera, CameraStreamCredentials
from quii_helper.media.audio.g711 import decode_g711, encode_g711
from quii_helper.streaming.rtp.g711 import G711Format
from quii_helper.streaming.rtsp import RtspH264Server
from quii_helper.streaming.rtsp.client import RtspClient
from quii_helper.streaming.rtsp.request import parse_request
from quii_helper.streaming.rtsp.sdp import build_h264_sdp
from quii_helper.streaming.rtsp.transport import RtpTransport

try:  # package import (tests / integration)
    from . import vimar_talk as talk_module
except ImportError:  # script import (/config/vimar/serve_rtsp.py)
    try:
        import vimar_talk as talk_module
    except ImportError:  # pragma: no cover - optional backend
        talk_module = None  # type: ignore[assignment]

TalkServer = talk_module.TalkServer if talk_module is not None else None

try:
    from .vimar_cloud import refresh_stream_key
except ImportError:
    try:
        from vimar_cloud import refresh_stream_key
    except ImportError:  # pragma: no cover
        refresh_stream_key = None  # type: ignore[assignment]

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
TALK = os.environ.get("TALK", "1").strip().lower() not in {
    "0", "false", "no", "off", ""
}
TALK_PORT = int(os.environ.get("TALK_PORT", "8560"))
CLOUD_ACCOUNT = os.environ.get(
    "CLOUD_ACCOUNT", "/config/vimar/cloud-account.json"
)
KEY_REFRESH = os.environ.get("KEY_REFRESH", "1").strip().lower() not in {
    "0", "false", "no", "off", ""
}
KEY_REFRESH_AFTER = int(os.environ.get("KEY_REFRESH_AFTER", "1"))
KEY_REFRESH_COOLDOWN = float(os.environ.get("KEY_REFRESH_COOLDOWN", "600"))

_log_lock = threading.Lock()


def log(msg: str) -> None:
    with _log_lock:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class AlwaysAudioRtspServer(RtspH264Server):
    """RTSP server with an always-present PCMA 8 kHz mono audio track.

    quii-helper's stock server advertises G.711 only after an E3 frame and
    answers 503 to DESCRIBE until then.  This doorbell sends no E3 audio in
    idle preview (and may only send it during a call), so hiding the track
    would either break the stream or prevent HA/go2rtc from negotiating audio.

    Advertise PCMA immediately and send silence until an E3 frame arrives.
    Mu-law and PCM16 frames are transcoded to A-law so the advertised format
    never changes.  Video-only clients simply ignore the silent track.
    """

    def __init__(self, *args, **kwargs) -> None:
        audio = bool(kwargs.get("audio", False))
        kwargs["audio"] = audio
        super().__init__(*args, **kwargs)
        self.audio_frames = 0
        self.audio_error = None
        self._audio_requested = audio
        self.talk_backchannel = TALK
        if audio:
            # Advertise the track before the first E3 frame.  A-law (PCMA)
            # is what the doorbell negotiates for talk and what go2rtc /
            # WebRTC support natively.
            self.audio_format = G711Format(4)
            self.audio_ready.set()

    def start(self):
        # RtspH264Server.start() calls _reset_media(), which clears the
        # format/event we set in __init__.
        super().start()
        if self._audio_requested:
            self.audio_format = G711Format(4)
            self.audio_ready.set()
        return self

    def _as_alaw(self, frame):
        """Return an A-law frame, transcoding mu-law/PCM16 if needed."""
        if frame.sample_rate != 8000 or frame.channels != 1:
            self.audio_error = (
                f"unsupported audio format {frame.sample_rate} Hz / "
                f"{frame.channels} ch"
            )
            return None
        if frame.codec == 4:
            return frame
        if frame.codec == 5:
            pcm = decode_g711(frame.data, 5)
            return replace(frame, data=encode_g711(pcm, 4), codec=4)
        if frame.codec == 9:
            if len(frame.data) % 2:
                self.audio_error = "odd PCM16 frame length"
                return None
            return replace(frame, data=encode_g711(frame.data, 4), codec=4)
        self.audio_error = f"unsupported audio codec {frame.codec}"
        return None

    def publish_audio(self, frame) -> None:
        if not self._running:
            return
        frame = self._as_alaw(frame)
        if frame is None:
            return
        try:
            super().publish_audio(frame)
        except Exception as exc:  # keep video alive on a bad audio frame
            self.audio_error = f"{type(exc).__name__}: {exc}"
            if self.audio_frames == 0:
                log(f"audio frame dropped: {self.audio_error}")
            return
        if self.audio_frames == 0:
            log("audio: first E3 frame received (PCMA/8000 mono)")
        self.audio_frames += 1


class NoWaitRtspClient(RtspClient):
    """Answer DESCRIBE immediately, with audio if the bridge advertises it.

    The stock client waits up to 5 s for video/SPS/PPS and answers 503 when
    the doorbell is still opening its stream.  The bridge's front proxy then
    drops the viewer and the lazy session closes, so every retry races the
    cold start.  This client returns the SDP immediately; the H.264 decoder
    parameters arrive in-band with the first IDR, exactly like the previous
    video-only bridge.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._backchannel = False

    def _handle_request(self, request_text: str) -> None:
        request = parse_request(request_text)
        if request is None:
            super()._handle_request(request_text)
            return
        method = request.method.upper()

        if method == "DESCRIBE":
            body = build_h264_sdp(
                connect_host=self.server.connect_host,
                payload_type=self.server.payload_type,
                audio_format=self.server.audio_format,
                parameter_sets=self.server.parameter_sets,
            )
            if getattr(self.server, "talk_backchannel", False):
                # go2rtc discovers this send-only track on the main session
                # and opens a second RTSP session for the microphone.
                body = body.rstrip("\r\n") + "\r\n"
                body += (
                    "m=audio 0 RTP/AVP 8\r\n"
                    "a=rtpmap:8 PCMA/8000/1\r\n"
                    "a=control:trackID=2\r\n"
                    "a=sendonly\r\n"
                )
            self._send_response(
                200,
                "OK",
                cseq=request.cseq,
                headers={
                    "Content-Type": "application/sdp",
                    "Content-Base": urlsplit(request.uri)
                    ._replace(
                        path=self.server.path.rstrip("/") + "/",
                        query="",
                        fragment="",
                    )
                    .geturl(),
                },
                body=body,
            )
            return

        if method == "SETUP":
            path = urlsplit(request.uri).path.rstrip("/")
            if path.endswith("trackID=2"):
                self._backchannel = True
                self.tracks.setdefault(2, RtpTransport(channel=4))
                transport = request.headers.get("transport", "")
                if "RTP/AVP/TCP" in transport.upper():
                    self._setup_tcp_transport(
                        request, transport, track_id=2
                    )
                else:
                    self._setup_udp_transport(
                        request, transport, track_id=2
                    )
                return

        if method == "PLAY" and self._backchannel:
            with self._send_lock:
                self.playing = True
                self._send_response(
                    200,
                    "OK",
                    cseq=request.cseq,
                    headers={"Session": self.server.session_id},
                )
            self._read_backchannel()
            return

        super()._handle_request(request_text)

    @staticmethod
    def _rtp_payload(packet: bytes) -> bytes:
        """Strip the RTP header (and CSRC/extension) from one packet."""
        if len(packet) < 12:
            return b""
        offset = 12 + 4 * (packet[0] & 0x0F)
        if packet[0] & 0x10:
            if len(packet) < offset + 4:
                return b""
            offset += (
                4
                + int.from_bytes(packet[offset + 2 : offset + 4], "big")
                * 4
            )
        return packet[offset:]

    def _read_backchannel(self) -> None:
        """Read interleaved RTP and forward PCMA to the talk server."""
        try:
            talk = socket.create_connection(
                ("127.0.0.1", TALK_PORT), timeout=2
            )
        except OSError as exc:
            log(f"rtsp backchannel: talk server unavailable: {exc}")
            return
        log("rtsp backchannel: connected to talk server")

        buffer = bytearray()
        try:
            self.sock.settimeout(1.0)
            while not self._closed and not self.server.stop_event.is_set():
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                buffer.extend(chunk)
                while buffer:
                    if buffer[0] == 0x24:  # interleaved RTP/RTCP '$'
                        if len(buffer) < 4:
                            break
                        channel = buffer[1]
                        length = int.from_bytes(buffer[2:4], "big")
                        if len(buffer) < 4 + length:
                            break
                        packet = bytes(buffer[4 : 4 + length])
                        del buffer[: 4 + length]
                        track = self.tracks.get(2)
                        if track is None or channel != track.channel:
                            # RTCP (channel + 1) or another track: never feed
                            # it to the talk socket as PCMA.
                            continue
                        payload = self._rtp_payload(packet)
                        if payload:
                            try:
                                talk.sendall(payload)
                            except OSError:
                                return
                        continue

                    end = buffer.find(b"\r\n\r\n")
                    if end < 0:
                        break
                    request = bytes(buffer[: end + 4])
                    del buffer[: end + 4]
                    if b"TEARDOWN" in request:
                        return
                    if request.startswith(b"OPTIONS"):
                        cseq = b""
                        for line in request.split(b"\r\n"):
                            if line.lower().startswith(b"cseq:"):
                                cseq = line.split(b":", 1)[1].strip()
                                break
                        try:
                            self.sock.sendall(
                                b"RTSP/1.0 200 OK\r\nCSeq: "
                                + cseq
                                + b"\r\n\r\n"
                            )
                        except OSError:
                            return
        finally:
            try:
                talk.close()
            except OSError:
                pass


# CameraRtspStream instantiates the class from its own module namespace.
camera_rtsp.RtspH264Server = AlwaysAudioRtspServer
# RtspH264Server._accept_loop looks up RtspClient in its own module.
rtsp_server.RtspClient = NoWaitRtspClient


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
        self.talk_server = None
        self.credentials_path = CREDENTIALS
        self.last_key_refresh = 0.0
        self.key_refresh_error = None
        self.key_lock = threading.Lock()

    # -- session management ------------------------------------------------
    def _spawn(self):
        saved = CameraStreamCredentials.load(self.credentials_path)
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

    def _maybe_refresh_key(self) -> None:
        """Refresh a rotated QVII key after repeated silent play replies."""
        if not KEY_REFRESH or refresh_stream_key is None:
            return
        if not os.path.exists(CLOUD_ACCOUNT):
            self.key_refresh_error = f"missing {CLOUD_ACCOUNT}"
            return
        now = time.time()
        if now - self.last_key_refresh < KEY_REFRESH_COOLDOWN:
            return
        if not self.key_lock.acquire(False):
            return
        try:
            self.last_key_refresh = now
            result = refresh_stream_key(
                CLOUD_ACCOUNT, self.credentials_path
            )
            if not result.get("ok"):
                self.key_refresh_error = result.get("error", "failed")
                log(f"stream key refresh failed: {self.key_refresh_error}")
                return
            self.key_refresh_error = None
            self.credentials_path = str(result["path"])
            if talk_module is not None:
                talk_module.CREDENTIALS = self.credentials_path
            log(
                "stream key refresh: changed=%s key_len=%s path=%s"
                % (
                    result.get("changed"),
                    result.get("key_len"),
                    self.credentials_path,
                )
            )
            if result.get("changed"):
                # The next attempt should use the new key immediately.
                self.error_streak = 0
                self.next_attempt_at = 0.0
        finally:
            self.key_lock.release()

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
        if reason in ("error", "stall", "startup_stall"):
            self.error_streak += 1
            delay = min(60.0, 5.0 * (2 ** min(self.error_streak - 1, 3)))
            log(
                f"device backoff {delay:.0f}s "
                f"(consecutive failures: {self.error_streak})"
            )
            if self.error_streak >= KEY_REFRESH_AFTER:
                self._maybe_refresh_key()
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
            # A rotated stream key makes the doorbell accept TCP/setup/play
            # but never send a frame.  Do not wait forever for the first
            # keyframe; close and let the key refresher run.
            if (
                clients > 0
                and not stream.server.video_ready.is_set()
                and time.time() - self.started_at > START_TIMEOUT
            ):
                log(
                    f"no video after {START_TIMEOUT:.0f}s startup timeout, "
                    "restarting"
                )
                self._close_session("startup_stall", drop_clients=True)
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
                "talk": self.talk_server.status()
                if self.talk_server is not None
                else None,
                "credentials_path": self.credentials_path,
                "key_refresh": {
                    "last": self.last_key_refresh,
                    "error": self.key_refresh_error,
                    "after": KEY_REFRESH_AFTER,
                    "cooldown": KEY_REFRESH_COOLDOWN,
                },
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
        if bridge.talk_server is not None:
            bridge.talk_server.stop()
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

    if TALK:
        if TalkServer is None:
            log("talk bridge unavailable (vimar_talk.py not importable)")
        else:
            bridge.talk_server = TalkServer()
            threading.Thread(
                target=bridge.talk_server.start,
                name="vimar-talk",
                daemon=True,
            ).start()

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
