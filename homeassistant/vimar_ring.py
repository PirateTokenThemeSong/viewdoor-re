"""Poll the QV cloud call journal and notify Home Assistant on a doorbell ring.

The Vimar VIEW Door app does **not** raise the outdoor button on the local
QVII video session.  The shop app receives a cloud push whose data branch is
the regular QV alarm with ``AlarmEvent=19`` (``QvPushInfo.ALARM_TYPE_CALL``).
The same event is written to the QV ``UserAlarm`` journal as
``client-query-recordlist`` row ``event=19``.

This module deliberately reads only that read-only journal.  It never answers
a call, never changes read state, never opens the door and never registers a
push token.  A tokenless alarm reader identity is used, as in the vendor SDK.

No credential, session id, password or cloud URL is logged.  The watcher emits
only a small JSON ring record to an optional Home Assistant webhook.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
)
from xml.etree import ElementTree as ET

CALL_EVENT = 19
PAGE_SIZE = 15
MAX_SEEN = 256
DEFAULT_AUTH_URL = "https://r1-2-sec.qvcloud.net/auth/user"
# Vimar profile (app 2078 / OEM G0078) service discovery, region 1.
DEFAULT_ALARM_URL = "https://shi-20-sec.qvcloud.net:4443/UserAlarm"
OEM = "G0078"
APP_ID = 2078
CLIENT_TYPE = 3
LOGIN_CLASS = "com.quvii.qvweb.userauth.bean.request.LoginReqContent"
ALARM_LOGIN_CLASS = "com.quvii.qvweb.Alarm.bean.request.AlarmLoginReqContent"
XML_HEADERS = {
    "Content-Type": "application/xml",
    "Charset": "utf-8",
    "Accept-Language": "en-US",
    "User-Agent": "okhttp/3.12.13",
}


class RingWatcher:
    """Background poller for incoming CALL records in the QV cloud journal."""

    def __init__(
        self,
        account_path: str,
        device_id: str,
        *,
        auth_url: str = DEFAULT_AUTH_URL,
        alarm_url: str = DEFAULT_ALARM_URL,
        webhook_url: str = "",
        poll_seconds: float = 5.0,
        error_seconds: float = 30.0,
        log=None,
    ) -> None:
        self.account_path = account_path
        self.device_id = (device_id or "").strip()
        self.auth_url = auth_url.rstrip("/")
        self.alarm_url = alarm_url.rstrip("/")
        self.webhook_url = webhook_url.strip()
        self.poll_seconds = max(2.0, float(poll_seconds))
        self.error_seconds = max(5.0, float(error_seconds))
        self._log = log or (lambda _msg: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._running = False
        self._ok = False
        self._last_error: str | None = None
        self._last_success = 0.0
        self._last_ring = 0.0
        self._ring_count = 0
        self._last_record: dict[str, Any] | None = None
        self._last_webhook_ok: bool | None = None
        self._seen: list[str] = []
        self._seen_set: set[str] = set()
        self._baseline_ready = False

    # -- public API -------------------------------------------------------
    def start(self) -> None:
        """Start the daemon thread once."""
        with self._lock:
            if self._running or not self.device_id:
                return
            self._running = True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="vimar-ring", daemon=True
            )
            self._thread.start()
            self._log("ring watcher started (cloud CALL journal)")

    def stop(self) -> None:
        """Stop the watcher and join briefly."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=8)
        with self._lock:
            self._running = False

    def status(self) -> dict[str, Any]:
        """Return only bounded diagnostic fields (no credentials/URLs)."""
        with self._lock:
            return {
                "enabled": True,
                "running": self._running,
                "ok": self._ok,
                "baseline_ready": self._baseline_ready,
                "last_ring": self._last_ring,
                "ring_count": self._ring_count,
                "last_record": self._last_record,
                "last_success": self._last_success,
                "last_error": self._last_error,
                "webhook_configured": bool(self.webhook_url),
                "last_webhook_ok": self._last_webhook_ok,
                "poll_seconds": self.poll_seconds,
            }

    # -- polling ----------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._poll_once()
                delay = self.poll_seconds
            except Exception as exc:  # noqa: BLE001 - status carries the reason
                with self._lock:
                    self._ok = False
                    self._last_error = self._safe_error(exc)
                self._log(f"ring watcher: {self._last_error}")
                delay = self.error_seconds
            delay = max(0.5, delay - (time.monotonic() - started))
            self._stop.wait(delay)
        with self._lock:
            self._running = False

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        if isinstance(exc, RingError):
            return exc.code
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(exc, HTTPError):
            return f"http-{exc.code}"
        if isinstance(exc, URLError):
            return "network"
        return type(exc).__name__.lower()[:64]

    def _poll_once(self) -> None:
        account = self._load_account()
        auth = _CloudSession()
        session, account_id = self._login(auth, account)
        reader_id = _reader_uuid(account["account"], account_id)
        alarm = _CloudSession()
        self._alarm_login(alarm, session, account_id, reader_id)
        rows = self._history(alarm, session)
        records = self._call_records(rows)
        with self._lock:
            self._ok = True
            self._last_error = None
            self._last_success = time.time()
            if not self._baseline_ready:
                for record in records:
                    self._remember(record["record_id"])
                self._baseline_ready = True
                self._log(
                    f"ring watcher baseline: {len(records)} existing CALL record(s)"
                )
                return
            fresh = []
            for record in records:
                if record["record_id"] not in self._seen_set:
                    self._remember(record["record_id"])
                    fresh.append(record)
            for record in reversed(fresh):
                self._emit_ring(record)

    def _load_account(self) -> dict[str, Any]:
        try:
            with open(self.account_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RingError("cloud-account-unavailable") from exc
        if not isinstance(data, dict) or not data.get("account") or not data.get(
            "password"
        ):
            raise RingError("cloud-account-incomplete")
        return data

    def _login(
        self, cloud: "_CloudSession", account: dict[str, Any]
    ) -> tuple[str, str]:
        client_id = f"00{CLIENT_TYPE}-{APP_ID}-" + "0" * 32
        content = ET.Element("content")
        ET.SubElement(content, "account").text = str(account["account"])
        ET.SubElement(content, "auth-code").text = ""
        ET.SubElement(content, "ip-region-id").text = str(
            account.get("rgn", 1)
        )
        ET.SubElement(content, "password").text = str(account["password"])
        ET.SubElement(content, "auth-type").text = "0"
        root = cloud.post(
            self.auth_url,
            _envelope(
                "login",
                content,
                client_id=client_id,
                session="",
                seq=1,
                content_class=LOGIN_CLASS,
            ),
        )
        result = _text(root, "./header/result")
        if result not in ("", "0"):
            raise RingError(f"cloud-login-{result}")
        session = _text(root, "./header/session/id")
        account_id = _text(root, "./content/account-id")
        if not session or not account_id:
            raise RingError("cloud-login-no-session")
        return session, account_id

    def _alarm_login(
        self,
        cloud: "_CloudSession",
        session: str,
        account_id: str,
        reader_id: str,
    ) -> None:
        host = self.auth_url.split("/", 3)[2].split(":", 1)[0]
        content = ET.Element("content")
        client = ET.SubElement(content, "client")
        for name, value in (
            ("id", f"00{CLIENT_TYPE}-{APP_ID}-{reader_id}"),
            ("tokentype", "0"),
            ("type", str(CLIENT_TYPE)),
            ("oemid", OEM),
            ("appid", str(APP_ID)),
            ("notifylang", "en"),
            ("timezone", "GMT+00:00"),
        ):
            ET.SubElement(client, name).text = value
        owner = ET.SubElement(content, "account")
        ET.SubElement(owner, "id").text = account_id
        ET.SubElement(owner, "authserver").text = host
        root = cloud.post(
            self.alarm_url,
            _envelope(
                "client-login",
                content,
                client_id=None,
                session=session,
                seq=2,
                content_class=ALARM_LOGIN_CLASS,
            ),
        )
        result = _text(root, "./header/result")
        if result not in ("", "0"):
            raise RingError(f"alarm-login-{result}")

    def _history(
        self, cloud: "_CloudSession", session: str
    ) -> list[ET.Element]:
        content = ET.Element("content")
        for name, value in (
            ("maxid", "0"),
            ("pageno", "0"),
            ("pagelinenum", str(PAGE_SIZE)),
        ):
            ET.SubElement(content, name).text = value
        filters = ET.SubElement(content, "filter")
        ET.SubElement(ET.SubElement(filters, "devid"), "id").text = self.device_id
        ET.SubElement(ET.SubElement(filters, "event"), "type").text = str(
            CALL_EVENT
        )
        ET.SubElement(filters, "peroid")
        root = cloud.post(
            self.alarm_url,
            _envelope(
                "client-query-recordlist",
                content,
                client_id=None,
                session=session,
                seq=0,
                content_class=None,
            ),
        )
        result = _text(root, "./header/result")
        if result not in ("", "0"):
            raise RingError(f"history-{result}")
        record = root.find("./content/record")
        return list(record.findall("data")) if record is not None else []

    def _call_records(self, rows: list[ET.Element]) -> list[dict[str, Any]]:
        records = []
        for row in rows:
            if _opt_text(row, "event") != str(CALL_EVENT):
                continue
            if _opt_text(row, "devid") != self.device_id:
                continue
            record_id = _opt_text(row, "id")
            if not record_id:
                continue
            records.append(
                {
                    "record_id": record_id,
                    "alarm_id": _opt_text(row, "alarmid"),
                    "channel": _int_text(row, "chno") + 1,
                    "time": _opt_text(row, "time"),
                    "record_url_present": bool(_opt_text(row, "recordsubresurl")),
                }
            )
        return records

    def _remember(self, record_id: str) -> None:
        if record_id in self._seen_set:
            return
        self._seen.append(record_id)
        self._seen_set.add(record_id)
        while len(self._seen) > MAX_SEEN:
            old = self._seen.pop(0)
            self._seen_set.discard(old)

    def _emit_ring(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._ring_count += 1
            self._last_ring = time.time()
            self._last_record = dict(record)
            payload = {
                "event": "ring",
                "source": "vimar_cloud_call",
                "device_id": self.device_id,
                "channel": record["channel"],
                "record_id": record["record_id"],
                "alarm_id": record["alarm_id"],
                "time": record["time"],
                "ring_count": self._ring_count,
                "timestamp": self._last_ring,
            }
        self._log(
            f"ring watcher: CALL record {record['record_id']} "
            f"(channel {record['channel']})"
        )
        if self.webhook_url:
            ok = self._post_webhook(payload)
            with self._lock:
                self._last_webhook_ok = ok

    def _post_webhook(self, payload: dict[str, Any]) -> bool:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener().open(request, timeout=8) as response:
                return 200 <= response.status < 300
        except (HTTPError, URLError, OSError) as exc:
            self._log(f"ring webhook failed: {self._safe_error(exc)}")
            return False


class RingError(Exception):
    """A fixed, non-secret ring watcher error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _CloudSession:
    """Cookie-consistent HTTP session for one QV cloud service."""

    def __init__(self) -> None:
        self._cookies = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookies))

    def post(self, url: str, xml: bytes) -> ET.Element:
        request = Request(url, data=xml, headers=XML_HEADERS, method="POST")
        with self._opener.open(request, timeout=25) as response:
            body = response.read(262144)
        try:
            return ET.fromstring(body)
        except ET.ParseError as exc:
            raise RingError("invalid-xml") from exc


def _envelope(
    command: str,
    content: ET.Element,
    *,
    client_id: str | None,
    session: str,
    seq: int,
    content_class: str | None,
) -> bytes:
    root = ET.Element("envelope")
    attributes = {"class": content_class} if content_class else {}
    ET.SubElement(root, "content", attributes).extend([content])
    header = ET.SubElement(root, "header")
    if client_id is not None:
        client = ET.SubElement(header, "client")
        ET.SubElement(client, "app").text = str(APP_ID)
        ET.SubElement(client, "id").text = client_id
        ET.SubElement(client, "oem").text = OEM
        ET.SubElement(client, "type").text = str(CLIENT_TYPE)
    ET.SubElement(header, "command").text = command
    ET.SubElement(header, "flag").text = "tdkcloud"
    ET.SubElement(header, "seq").text = str(seq)
    if session:
        ET.SubElement(header, "session").text = session
    ET.SubElement(header, "user-data")
    ET.SubElement(header, "version").text = ""
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + body


def _reader_uuid(account: str, account_id: str) -> str:
    # Stable per account/device, distinct from the phone's alarm client.
    digest = __import__("hashlib").sha256(
        f"vimar-door:{account}:{account_id}".encode("utf-8")
    ).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def _opt_text(element: ET.Element, path: str) -> str:
    return (element.findtext(path) or "").strip()


def _text(root: ET.Element, path: str) -> str:
    return _opt_text(root, path)


def _int_text(element: ET.Element, path: str) -> int:
    value = _opt_text(element, path)
    try:
        return int(value)
    except ValueError:
        return -1
