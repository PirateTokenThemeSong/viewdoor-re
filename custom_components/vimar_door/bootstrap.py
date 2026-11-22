"""One-step Vimar cloud bootstrap for the Home Assistant integration.

The wizard gives us the Vimar app email/password and the doorbell LAN IP.
This module logs into Vimar SSO, lists the cloud devices, fetches the local
device token and writes the two private files consumed by the embedded bridge.

It never prints or returns the Vimar password, the cloud token or the local
device key. Only the selected device name/model and public identity are
returned to the UI.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

AUTH_URL = "https://r1-2-sec.qvcloud.net/auth/user"
SSO_URL = "https://sso.qvcloud.net/vimar/usrpwd/authorize"
OEM = "G0078"
APP_ID = 2078
CLIENT_TYPE = 3
LOGIN_CLASS = "com.quvii.qvweb.userauth.bean.request.LoginReqContent"
DEVICE_LIST_CLASS = "com.quvii.qvweb.userauth.bean.request.DevListReqContent"
TOKEN_CLASS = "com.quvii.qvweb.userauth.bean.request.DevDynamicPwdGetReqContent"
XML_HEADERS = {
    "Content-Type": "application/xml",
    "Charset": "utf-8",
    "Accept-Language": "en-US",
    "User-Agent": "okhttp/3.12.13",
}
BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 11; sdk_gphone_x86) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36"
)


class BootstrapError(Exception):
    """A fixed, non-secret error shown by the config flow."""

    def __init__(self, code: str, *, devices: list[dict[str, str]] | None = None):
        self.code = code
        self.devices = devices or []
        super().__init__(code)


@dataclass(frozen=True)
class BootstrapResult:
    """Safe result for the config flow."""

    host: str
    device_id: str
    device_name: str
    model: str
    auth_code: str
    stream_key: str
    client_id: str


def new_client_id() -> str:
    """Generate the stable QV client identity used by SSO and the bridge."""
    return f"003-{APP_ID}-{uuid.uuid4()}"


def bootstrap(
    email: str,
    password: str,
    host: str,
    client_id: str,
    *,
    cloud_path: str,
    credentials_path: str,
    device_id: str | None = None,
    timeout: float = 30.0,
) -> BootstrapResult:
    """Run the complete one-step setup in a worker thread."""
    if not email or not password or not host:
        raise BootstrapError("missing_input")
    if not client_id.startswith(f"003-{APP_ID}-"):
        raise BootstrapError("invalid_client")

    # The SSO uses an isolated cookie jar; its password is a one-time token.
    sso = requests.Session()
    sso.headers.update({"User-Agent": BROWSER_UA})
    try:
        account, cloud_password, region = _sso_login(
            sso, email, password, client_id, timeout
        )
    except BootstrapError:
        raise
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise BootstrapError("sso_failed") from exc

    cloud = requests.Session()
    try:
        session_id, account_id = _qv_login(
            cloud, account, cloud_password, region, client_id, timeout
        )
        devices = _device_list(cloud, session_id, timeout)
        if not devices:
            raise BootstrapError("no_devices")
        if device_id:
            selected = next((d for d in devices if d["id"] == device_id), None)
            if selected is None:
                raise BootstrapError(
                    "device_not_found", devices=[_public_device(d) for d in devices]
                )
        elif len(devices) == 1:
            selected = devices[0]
        else:
            raise BootstrapError(
                "multiple_devices", devices=[_public_device(d) for d in devices]
            )

        token = _device_token(cloud, session_id, selected["id"], timeout)
        stream_key = token.get("data-encode-key", "")
        auth_code = token.get("out-auth-code", "")
        if len(stream_key) != 32 or not auth_code:
            raise BootstrapError("invalid_device_token")
    except BootstrapError:
        raise
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise BootstrapError("cloud_failed") from exc

    cloud_data = {
        "result": 0,
        "account": account,
        "password": cloud_password,
        "rgn": region,
        "client_id": client_id,
    }
    credentials_data = {
        "version": 1,
        "device_id": selected["id"],
        "stream_key": stream_key,
        "auth_code": auth_code,
    }
    _write_private(Path(cloud_path), cloud_data)
    _write_private(Path(credentials_path), credentials_data)
    return BootstrapResult(
        host=host,
        device_id=selected["id"],
        device_name=selected.get("name", "") or selected["id"],
        model=selected.get("model", ""),
        auth_code=auth_code,
        stream_key=stream_key,
        client_id=client_id,
    )


def _sso_login(
    session: requests.Session,
    email: str,
    password: str,
    client_id: str,
    timeout: float,
) -> tuple[str, str, int]:
    start = (
        f"{SSO_URL}?oem={OEM}&client_id={client_id}&rgn=1"
    )
    page = session.get(start, timeout=timeout)
    if page.status_code != 200:
        raise BootstrapError("sso_login_page")
    match = re.search(r'name="hash"[^>]*value="([^"]+)"', page.text)
    if match is None:
        match = re.search(r'value="([^"]+)"[^>]*name="hash"', page.text)
    if match is None:
        raise BootstrapError("sso_form_changed")
    response = session.post(
        page.url,
        data={
            "hash": match.group(1),
            "email": email,
            "passw": password,
            "go": "",
        },
        timeout=timeout,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise BootstrapError("sso_auth_failed") from exc
    if payload.get("result") != 0:
        raise BootstrapError("sso_auth_failed")
    account = payload.get("account")
    cloud_password = payload.get("password")
    region = payload.get("rgn", 1)
    if not isinstance(account, str) or not account or not isinstance(
        cloud_password, str
    ) or not cloud_password:
        raise BootstrapError("sso_empty_credentials")
    try:
        region = int(region)
    except (TypeError, ValueError):
        region = 1
    return account, cloud_password, region


def _qv_login(
    session: requests.Session,
    account: str,
    password: str,
    region: int,
    client_id: str,
    timeout: float,
) -> tuple[str, str]:
    content = _element("content")
    _text(content, "account", account)
    _text(content, "auth-code", "")
    _text(content, "ip-region-id", str(region))
    _text(content, "password", password)
    _text(content, "auth-type", "0")
    root = _post(
        session,
        AUTH_URL,
        _envelope("login", content, "", 1, client_id, LOGIN_CLASS),
        timeout,
    )
    result = _findtext(root, "./header/result")
    if result not in ("", "0"):
        raise BootstrapError("qv_login_failed")
    session_id = _findtext(root, "./header/session/id")
    account_id = _findtext(root, "./content/account-id")
    if not session_id or not account_id:
        raise BootstrapError("qv_login_no_session")
    return session_id, account_id


def _device_list(
    session: requests.Session, session_id: str, timeout: float
) -> list[dict[str, str]]:
    content = _element("content")
    _element("filter", content)
    _text(content, "order", "0")
    _text(content, "count", "0")
    _text(content, "page", "0")
    _element("owner", content)
    root = _post(
        session,
        AUTH_URL,
        _envelope(
            "get-device-list",
            content,
            session_id,
            0,
            None,
            DEVICE_LIST_CLASS,
        ),
        timeout,
    )
    result = _findtext(root, "./header/result")
    if result not in ("", "0"):
        raise BootstrapError("device_list_failed")
    content_node = root.find("./content")
    if content_node is None:
        return []
    devices = content_node.findall("device")
    parsed = []
    for device in devices:
        device_id = _findtext(device, "id") or _findtext(device, "device-id")
        if not device_id:
            continue
        parsed.append(
            {
                "id": device_id,
                "name": _findtext(device, "name"),
                "model": _findtext(device, "model"),
            }
        )
    return parsed


def _device_token(
    session: requests.Session,
    session_id: str,
    device_id: str,
    timeout: float,
) -> dict[str, str]:
    content = _element("content")
    _text(content, "device-id", device_id)
    root = _post(
        session,
        AUTH_URL,
        _envelope(
            "get-device-token",
            content,
            session_id,
            0,
            None,
            TOKEN_CLASS,
        ),
        timeout,
    )
    result = _findtext(root, "./header/result")
    if result not in ("", "0"):
        raise BootstrapError("device_token_failed")
    return {
        "data-encode-key": _findtext(root, "./content/data-encode-key"),
        "out-auth-code": _findtext(root, "./content/out-auth-code"),
        "password-expired": _findtext(root, "./content/password-expired"),
    }


def _public_device(device: dict[str, str]) -> dict[str, str]:
    return {
        "id": device.get("id", ""),
        "name": device.get("name", "") or device.get("model", ""),
        "model": device.get("model", ""),
    }


def _element(name: str, parent: Any | None = None) -> Any:
    from xml.etree import ElementTree as ET

    if parent is None:
        return ET.Element(name)
    return ET.SubElement(parent, name)


def _text(parent: Any, name: str, value: str) -> None:
    from xml.etree import ElementTree as ET

    node = ET.SubElement(parent, name)
    node.text = str(value)


def _envelope(
    command: str,
    content: Any,
    session_id: str,
    seq: int,
    client_id: str | None,
    content_class: str,
) -> bytes:
    from xml.etree import ElementTree as ET

    root = ET.Element("envelope")
    ET.SubElement(root, "content", {"class": content_class}).extend([content])
    header = ET.SubElement(root, "header")
    if client_id:
        client = ET.SubElement(header, "client")
        ET.SubElement(client, "app").text = str(APP_ID)
        ET.SubElement(client, "id").text = client_id
        ET.SubElement(client, "oem").text = OEM
        ET.SubElement(client, "type").text = str(CLIENT_TYPE)
    ET.SubElement(header, "command").text = command
    ET.SubElement(header, "flag").text = "tdkcloud"
    ET.SubElement(header, "seq").text = str(seq)
    if session_id:
        ET.SubElement(header, "session").text = session_id
    ET.SubElement(header, "user-data")
    ET.SubElement(header, "version").text = ""
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + body


def _post(
    session: requests.Session, url: str, body: bytes, timeout: float
) -> Any:
    from xml.etree import ElementTree as ET

    response = session.post(url, data=body, headers=XML_HEADERS, timeout=timeout)
    try:
        return ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise BootstrapError("invalid_cloud_response") from exc


def _findtext(element: Any, path: str) -> str:
    value = element.findtext(path)
    return value.strip() if value else ""


def _write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    os.replace(tmp, path)
