"""Qualvision cloud helper: refresh the 32-byte QVII stream key.

Used by the bridge when a session opens but the doorbell silently ignores the
play (rotated `data-encode-key`) and by the `tools/refresh_credentials.py`
command.  Standard library only, so it also runs in the slim bridge image.

Nothing here prints a key or an account password.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, build_opener
from xml.etree import ElementTree as ET

AUTH_URL = "https://r1-2-sec.qvcloud.net/auth/user"
OEM = "G0078"
APP_ID = 2078
CLIENT_TYPE = 3
CLIENT_ID = f"00{CLIENT_TYPE}-{APP_ID}-0000000000000000000000000000"
LOGIN_CLASS = "com.quvii.qvweb.userauth.bean.request.LoginReqContent"
TOKEN_CLASS = "com.quvii.qvweb.userauth.bean.request.DevDynamicPwdGetReqContent"


def _envelope(
    command: str,
    content: ET.Element,
    session: str,
    seq: int,
    client_id: str = CLIENT_ID,
) -> bytes:
    env = ET.Element("envelope")
    cls = LOGIN_CLASS if command == "login" else TOKEN_CLASS
    ET.SubElement(env, "content", {"class": cls}).extend([content])
    header = ET.SubElement(env, "header")
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
    body = ET.tostring(env, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + body


class _CloudSession:
    """Cookie-consistent Qualvision auth session.

    The user service rejects `get-device-token` with 100100001 (session id not
    consistent) if the login and token requests do not share cookies.
    """

    def __init__(self) -> None:
        self._cookies = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookies))

    def post(self, xml: bytes, timeout: float = 30.0) -> ET.Element:
        request = urllib.request.Request(
            AUTH_URL,
            data=xml,
            headers={
                "Content-Type": "application/xml",
                "Charset": "utf-8",
                "Accept-Language": "en-US",
                "User-Agent": "okhttp/3.12.13",
            },
        )
        with self._opener.open(request, timeout=timeout) as response:
            return ET.fromstring(response.read())


def _write_json(path: Path, data: dict) -> None:
    """Atomically replace the credentials file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def refresh_stream_key(
    account_path: str | Path,
    credentials_path: str | Path,
    device_id: str | None = None,
    timeout: float = 30.0,
) -> dict:
    """Fetch a fresh `data-encode-key` and update the credentials JSON.

    Returns a dict with `ok`, `changed`, `path`, lengths and expiry, or
    `{"ok": False, "error": "..."}`.  If the original credentials file is
    read-only (the bridge container mounts it ro), a writable copy is written
    to `/tmp/vimar_credentials.json` and its path is returned.
    """
    try:
        account = json.loads(Path(account_path).read_text(encoding="utf-8"))
        out_path = Path(credentials_path)
        current = (
            json.loads(out_path.read_text(encoding="utf-8"))
            if out_path.exists()
            else {}
        )
        device_id = device_id or current.get("device_id")
        if not device_id:
            return {"ok": False, "error": "missing device-id"}
        if not account.get("account") or not account.get("password"):
            return {"ok": False, "error": "missing cloud account/password"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"local credentials: {exc}"}

    cloud = _CloudSession()
    client_id = str(account.get("client_id") or "").strip() or CLIENT_ID
    try:
        login = ET.Element("content")
        ET.SubElement(login, "account").text = account["account"]
        ET.SubElement(login, "auth-code").text = ""
        ET.SubElement(login, "ip-region-id").text = str(account.get("rgn", 1))
        ET.SubElement(login, "password").text = account["password"]
        ET.SubElement(login, "auth-type").text = "0"
        root = cloud.post(
            _envelope("login", login, "", 1, client_id=client_id), timeout
        )
        result = (root.findtext("./header/result") or "").strip()
        if result and result != "0":
            return {"ok": False, "error": f"login result {result}"}
        session = (root.findtext("./header/session/id") or "").strip()
        if not session:
            return {"ok": False, "error": "login session missing"}

        token_content = ET.Element("content")
        ET.SubElement(token_content, "device-id").text = device_id
        root = cloud.post(
            _envelope(
                "get-device-token", token_content, session, 0,
                client_id=client_id,
            ),
            timeout,
        )
        result = (root.findtext("./header/result") or "").strip()
        if result and result != "0":
            return {"ok": False, "error": f"token result {result}"}
        content = root.find("./content")
        if content is None:
            return {"ok": False, "error": "token content empty"}
    except (urllib.error.URLError, OSError, ET.ParseError, ValueError) as exc:
        return {"ok": False, "error": f"cloud: {exc}"}

    stream_key = (content.findtext("data-encode-key") or "").strip()
    auth_code = (content.findtext("out-auth-code") or "").strip()
    if len(stream_key) != 32:
        return {"ok": False, "error": f"key length {len(stream_key)}"}

    old_key = current.get("stream_key")
    old_code = current.get("auth_code")
    data = {
        "version": 1,
        "device_id": device_id,
        "stream_key": stream_key,
        "auth_code": auth_code or old_code or "",
    }

    target = out_path
    try:
        _write_json(target, data)
    except OSError:
        target = Path("/tmp/vimar_credentials.json")
        try:
            _write_json(target, data)
        except OSError as exc:
            return {"ok": False, "error": f"write credentials: {exc}"}

    return {
        "ok": True,
        "changed": old_key != stream_key,
        "path": str(target),
        "key_len": len(stream_key),
        "auth_changed": old_code is not None and old_code != data["auth_code"],
        "auth_len": len(data["auth_code"]),
        "expires": content.findtext("password-expired") or "unknown",
    }
