#!/usr/bin/env python3
"""
viewdoor.py - CLI client for Vimar VIEW Door (Elvox IDSxxxx / Quvii "VDP") doorbells.

Reverse engineered from com.vimar.viewdoor 2.2.11 (APK).

The device exposes an XML-over-HTTP control API ("tdkcgi") on its LAN
interface, and (when enabled in firmware) an RTSP server.

Examples
--------
  # find the API + print model, network config (incl. RTSP port/url), doors
  python viewdoor.py probe --host 192.168.1.50 --code 123456

  # list the configured lock/relay outputs ("door 1", "door 2")
  python viewdoor.py doors --host 192.168.1.50 --code 123456

  # trigger relay 1 (channel 0) / relay 2
  python viewdoor.py open 1 --host 192.168.1.50 --code 123456
  python viewdoor.py open 2 --host 192.168.1.50 --code 123456

  # safe test of the door-open password (does not open the door)
  python viewdoor.py check-password 1234 --host 192.168.1.50 --code 123456

  # --code accepts either the label code ("<LABEL_CODE>", sha256'd) or the
  # cloud "out-auth-code" (64 hex, sent as-is)

  # try to locate the RTSP stream
  python viewdoor.py rtsp --host 192.168.1.50 --code 123456

Auth model (see docs/internal/PROTOCOL.md):
  * LAN/device auth uses  username "adminapp2", password = sha256(auth_code)
    where auth_code is the device verification code (label / set during pairing).
  * The door-open password ("unlock password") is sent sha256-hashed on devices
    that advertise the SHA256 capability (default here); use --hash off for old fw.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

DEFAULT_USER = "adminapp2"          # SDKConst.DEVICE_USER_NAME
CGI_PATH = "/tdkcgi"                # HttpDeviceConst.CGI_ADDRESS
DEFAULT_SECURITY = "username"       # SDKConst.CGI_DEVICE_SECURITY
COMMON_PORTS = [443, 80, 8080, 8443, 8000, 8899, 81, 82, 88]

# Commands (DeviceRequestHelp / DeviceJsonRequestHelp)
CMD_PRODUCT_INFO = "get.product.info"
CMD_NETWORK_CONFIG = "get.network.config"
CMD_NETWORK_BASE = "get.network.base"
CMD_SYSTEM_ABILITY = "get.system.ability"
CMD_ATTACH_INFO = "get.device.attachInfo"
CMD_OPEN_DOOR = "set.device.opendoor"
CMD_CHECK_UNLOCK_PW = "set.opendoor.checkpassword"
CMD_DEVICE_STATUS = "get.device.status"
CMD_DEVICE_SECRET = "get.device.streamkey"
CMD_ONVIF_PWD_GET = "get.onvif.pwd"
CMD_ONVIF_PWD_SET = "set.onvif.pwd"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_local(elem: ET.Element, name: str) -> Optional[ET.Element]:
    for child in elem:
        if localname(child.tag) == name:
            return child
    return None


def find_local_all(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if localname(c.tag) == name]


def deep_find(elem: ET.Element, name: str) -> Optional[ET.Element]:
    for node in elem.iter():
        if localname(node.tag) == name:
            return node
    return None


# --------------------------------------------------------------------------- #
# device client
# --------------------------------------------------------------------------- #
class DeviceError(Exception):
    pass


class DeviceClient:
    """XML /tdkcgi client."""

    def __init__(
        self,
        host: str,
        port: Optional[int] = None,
        code: str = "",
        user: str = DEFAULT_USER,
        password: Optional[str] = None,
        security: str = DEFAULT_SECURITY,
        scheme: str = "auto",
        timeout: float = 6.0,
        debug: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        # Header password (passwordencode=1):
        #   - cloud "out-auth-code" (64 hex) is already the hash -> send as-is
        #   - otherwise sha256(label code), matching QvEncrypt.EncodeDevicePassword
        if password is None:
            if len(code) == 64 and all(c in "0123456789abcdefABCDEF" for c in code):
                self.password = code.lower()
            else:
                self.password = sha256_hex(code)
            self.passwordencode = True
        else:
            self.password = password
            self.passwordencode = False
        self.security = security
        self.scheme = scheme
        self.timeout = timeout
        self.debug = debug
        self.base: Optional[str] = None
        self._ssl_no_verify = ssl._create_unverified_context()

    # -- transport ---------------------------------------------------------- #
    def _candidates(self) -> Iterable[str]:
        if self.port:
            schemes = ["https", "http"] if self.scheme == "auto" else [self.scheme]
            for s in schemes:
                yield f"{s}://{self.host}:{self.port}"
            return
        if self.scheme in ("auto", "https"):
            for p in COMMON_PORTS:
                yield f"https://{self.host}:{p}"
        if self.scheme in ("auto", "http"):
            for p in COMMON_PORTS:
                yield f"http://{self.host}:{p}"

    def _http_post(
        self, url: str, data: bytes, content_type: str
    ) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": content_type, "Accept": "*/*"},
        )
        ctx = self._ssl_no_verify if url.startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()

    def _digest_post(
        self,
        url: str,
        data: bytes,
        content_type: str,
        challenge: str,
    ) -> tuple[int, dict[str, str], bytes]:
        """HTTP Digest retry, replicating DeviceAuthHeaderInterceptor."""
        params = {}
        for item in challenge.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                params[k.strip().lower()] = v.strip().strip('"')
        realm = params.get("realm", "")
        nonce = params.get("nonce", "")
        opaque = params.get("opaque", "")
        qop = params.get("qop", "")
        if not (realm and nonce and opaque and qop):
            return 401, {}, b""
        nc, cnonce = "00001", "suiji"
        ha1 = md5_hex(f"{self.user}:{realm}:{self.password}")
        ha2 = md5_hex(f"POST:{CGI_PATH}")
        response = md5_hex(f"{ha1}:{nonce}:nc{nc}:{cnonce}:{qop}:{ha2}")
        auth = (
            f'Digest username="{self.user}",realm="{realm}",nonce="{nonce}",'
            f'uri="{CGI_PATH}",algorithm=MD5,response="{response}",'
            f'opaque="{opaque}",qop={qop},nc=nc{nc},cnonce="{cnonce}"'
        )
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Accept": "*/*",
                "Authorization": auth,
            },
        )
        ctx = self._ssl_no_verify if url.startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()

    # -- envelope ----------------------------------------------------------- #
    def envelope(self, command: str, content_xml: str = "") -> str:
        pw_el = ""
        if self.password != "":
            pw_el = f"<password>{xml_escape(self.password)}</password>"
            if self.passwordencode:
                pw_el += "<passwordencode>1</passwordencode>"
        content = f"<content>{content_xml}</content>" if content_xml is not None else ""
        return (
            "<envelope>"
            "<header>"
            f"<security>{xml_escape(self.security)}</security>"
            f"<username>{xml_escape(self.user)}</username>"
            f"{pw_el}"
            "</header>"
            "<body>"
            f"<command>{xml_escape(command)}</command>"
            f"{content}"
            "</body>"
            "</envelope>"
        )

    def call(self, command: str, content_xml: str = "") -> ET.Element:
        body = self.envelope(command, content_xml).encode("utf-8")
        bases = [self.base] if self.base else list(self._candidates())
        last_error: Optional[str] = None
        for base in bases:
            url = base.rstrip("/") + CGI_PATH
            try:
                status, headers, raw = self._http_post(
                    url, body, "application/xml;charset=utf-8"
                )
                if status == 401:
                    challenge = ""
                    for k, v in headers.items():
                        if k.lower() == "www-authenticate":
                            challenge += ("," if challenge else "") + v
                    if challenge:
                        status, headers, raw = self._digest_post(
                            url, body, "application/xml;charset=utf-8", challenge
                        )
                    if status == 401:
                        raise DeviceError(
                            "authentication failed (HTTP 401) - wrong verification code?"
                        )
                if status >= 500:
                    last_error = f"{url} -> HTTP {status}"
                    continue
                text = raw.decode("utf-8", "replace")
                if self.debug:
                    print(f"[debug] POST {url} -> HTTP {status}", file=sys.stderr)
                    print(f"[debug] {text[:2000]}", file=sys.stderr)
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError:
                    last_error = f"{url} -> HTTP {status}, non-XML answer: {text[:120]!r}"
                    continue
                if self.base is None:
                    self.base = base
                if status == 401:
                    raise DeviceError("authentication failed (HTTP 401) - wrong code?")
                err = deep_find(root, "error")
                if err is not None and err.text and err.text.strip() not in ("0",):
                    code = err.text.strip()
                    if code in ("401", "-7"):
                        raise DeviceError(
                            f"device rejected auth (error {code}) - wrong verification code?"
                        )
                    raise DeviceError(f"device error {code}")
                return root
            except DeviceError:
                raise
            except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as exc:
                last_error = f"{url} -> {exc}"
                continue
        raise DeviceError(
            "no working endpoint found"
            + (f" (last: {last_error})" if last_error else "")
        )

    # -- convenience -------------------------------------------------------- #
    def product_info(self) -> dict:
        root = self.call(CMD_PRODUCT_INFO)
        info = deep_find(root, "info")
        return _flatten(info) if info is not None else {}

    def network_config(self) -> dict:
        root = self.call(CMD_NETWORK_CONFIG)
        base = deep_find(root, "base")
        return _flatten(base) if base is not None else {}

    def system_ability(self) -> ET.Element:
        return self.call(CMD_SYSTEM_ABILITY)

    def attach_info(self) -> list[dict]:
        root = self.call(CMD_ATTACH_INFO)
        doors = []
        for chan in root.iter():
            if localname(chan.tag) != "channel":
                continue
            chan_num = _text(find_local(chan, "num"))
            chan_name = _text(find_local(chan, "name"))
            for lock in chan.iter():
                if localname(lock.tag) not in ("lock", "lockinfo"):
                    continue
                if lock is chan:
                    continue
                lock_num = _text(find_local(lock, "num"))
                if lock_num is None:
                    continue
                doors.append(
                    {
                        "channel": int(chan_num) if chan_num and chan_num.isdigit() else 0,
                        "lock": int(lock_num),
                        "name": _text(find_local(lock, "name")) or f"door {lock_num}",
                        "channel_name": chan_name,
                        "enabled": _text(find_local(lock, "enable")),
                    }
                )
        # de-duplicate
        seen, uniq = set(), []
        for d in doors:
            key = (d["channel"], d["lock"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(d)
        return uniq

    def open_door(
        self,
        lock: int,
        channel: int = 0,
        password: str = "",
        hash_password: bool = True,
    ) -> ET.Element:
        content = f"<door>{int(channel)}</door><locknumber>{int(lock)}</locknumber>"
        if password:
            value = sha256_hex(password) if hash_password else password
            content += f"<password>{xml_escape(value)}</password>"
        return self.call(CMD_OPEN_DOOR, content)

    def check_unlock_password(self, password: str) -> ET.Element:
        # The app always sha256-hashes this value (QvEncrypt.EncodeDevicePassword).
        return self.call(
            CMD_CHECK_UNLOCK_PW,
            f"<password>{xml_escape(sha256_hex(password))}</password>",
        )

    def onvif_password(self) -> Optional[str]:
        """Needs the JSON API; XML firmware may not answer."""
        try:
            root = self.call(CMD_ONVIF_PWD_GET)
        except DeviceError:
            return None
        node = deep_find(root, "pwd")
        return _text(node)


def _text(elem: Optional[ET.Element]) -> Optional[str]:
    if elem is None or elem.text is None:
        return None
    return elem.text.strip()


def _flatten(elem: Optional[ET.Element]) -> dict:
    if elem is None:
        return {}

    def conv(node: ET.Element):
        children = list(node)
        if not children:
            return _text(node)
        return {localname(c.tag): conv(c) for c in children}

    out = conv(elem)
    return out if isinstance(out, dict) else {"value": out}


# --------------------------------------------------------------------------- #
# RTSP probe
# --------------------------------------------------------------------------- #
RTSP_PATHS = [
    "/",
    "/live",
    "/live/ch0",
    "/live/ch1",
    "/live/main",
    "/live/sub",
    "/ch0",
    "/ch1",
    "/stream1",
    "/stream2",
    "/main",
    "/sub",
    "/h264",
    "/11",
    "/12",
    "/av0_0",
    "/profile1",
    "/profile2",
    "/rtsp",
    "/cam/realmonitor?channel=1&subtype=0",
    "/Streaming/Channels/101",
    "/streaming/channels/101",
    "/0",
    "/1",
    "/media/video1",
]


def _rtsp_request(
    host: str,
    port: int,
    path: str,
    method: str,
    cseq: int,
    auth: Optional[str] = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    url = f"rtsp://{host}:{port}{path}"
    lines = [f"{method} {url} RTSP/1.0", f"CSeq: {cseq}", "User-Agent: viewdoor-re"]
    if auth:
        lines.append(f"Authorization: {auth}")
    payload = ("\r\n".join(lines) + "\r\n\r\n").encode()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("utf-8", "replace")
        m = re.match(r"RTSP/\d\.\d\s+(\d+)", status_line)
        status = int(m.group(1)) if m else 0
        headers: dict[str, str] = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode().strip().lower()] = v.decode("utf-8", "replace").strip()
        if "content-length" in headers:
            need = int(headers["content-length"])
            while len(rest) < need:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                rest += chunk
        return status, headers, rest


def _rtsp_digest(
    user: str, password: str, realm: str, nonce: str, uri: str
) -> str:
    ha1 = md5_hex(f"{user}:{realm}:{password}")
    ha2 = md5_hex(f"DESCRIBE:{uri}")
    return md5_hex(f"{ha1}:{nonce}:{ha2}")


def rtsp_describe(
    host: str, port: int, path: str, user: str, password: str
) -> Optional[dict]:
    try:
        status, headers, body = _rtsp_request(host, port, path, "DESCRIBE", 1)
        if status == 401:
            challenge = headers.get("www-authenticate", "")
            realm = _pick(challenge, "realm")
            nonce = _pick(challenge, "nonce")
            if "Basic" in challenge:
                token = base64.b64encode(f"{user}:{password}".encode()).decode()
                auth = f"Basic {token}"
            elif realm and nonce:
                uri = f"rtsp://{host}:{port}{path}"
                response = _rtsp_digest(user, password, realm, nonce, uri)
                auth = (
                    f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
                    f'uri="{uri}", response="{response}"'
                )
            else:
                return None
            status, headers, body = _rtsp_request(
                host, port, path, "DESCRIBE", 2, auth=auth
            )
        if status == 200 and b"m=" in body:
            return {
                "url": f"rtsp://{host}:{port}{path}",
                "headers": headers,
                "sdp": body.decode("utf-8", "replace"),
            }
    except (OSError, socket.timeout, ValueError):
        pass
    return None


def _pick(header_value: str, key: str) -> str:
    m = re.search(rf'{key}="?([^",]+)"?', header_value, re.I)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", required=True, help="doorbell LAN IP / hostname")
    p.add_argument("--port", type=int, help="tdkcgi port (default: auto-probe)")
    p.add_argument("--code", default="", help="device verification code (auth code)")
    p.add_argument(
        "--password",
        help="raw header password (default: sha256(--code)); useful for odd firmware",
    )
    p.add_argument("--user", default=DEFAULT_USER, help="header username")
    p.add_argument("--security", default=DEFAULT_SECURITY, help="header security mode")
    p.add_argument(
        "--scheme", choices=["auto", "http", "https"], default="auto", help="API scheme"
    )
    p.add_argument("--timeout", type=float, default=6.0, help="HTTP timeout (s)")
    p.add_argument("--debug", action="store_true", help="dump raw HTTP")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Vimar VIEW Door (Elvox IDSxxxx) tdkcgi + RTSP tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="auth check + model/net/RTSP/doors dump")
    _common_args(p_probe)

    p_doors = sub.add_parser("doors", help="list lock outputs")
    _common_args(p_doors)

    p_open = sub.add_parser("open", help="open a door/relay")
    p_open.add_argument("lock", type=int, help="lock number (from 'doors')")
    p_open.add_argument("--channel", type=int, default=0, help="channel number")
    p_open.add_argument("--unlock-password", default="", help="door-open password")
    p_open.add_argument(
        "--hash",
        choices=["on", "off"],
        default="on",
        help="sha256 the unlock password (default on; off = old firmware)",
    )
    p_open.add_argument(
        "--yes",
        action="store_true",
        help="do not ask for confirmation before firing the relay",
    )
    _common_args(p_open)

    p_check = sub.add_parser("check-password", help="validate door-open password")
    p_check.add_argument("password")
    _common_args(p_check)

    p_raw = sub.add_parser("raw", help="send an arbitrary command")
    p_raw.add_argument("command")
    p_raw.add_argument("--content", default="", help="inner XML for <content>")
    _common_args(p_raw)

    p_rtsp = sub.add_parser("rtsp", help="locate the RTSP stream")
    p_rtsp.add_argument("--host", required=True)
    p_rtsp.add_argument("--port", type=int, help="tdkcgi port (for rtspport lookup)")
    p_rtsp.add_argument("--code", default="")
    p_rtsp.add_argument("--password")
    p_rtsp.add_argument("--user", default=DEFAULT_USER)
    p_rtsp.add_argument("--security", default=DEFAULT_SECURITY)
    p_rtsp.add_argument("--scheme", choices=["auto", "http", "https"], default="auto")
    p_rtsp.add_argument("--timeout", type=float, default=6.0)
    p_rtsp.add_argument("--debug", action="store_true")
    p_rtsp.add_argument("--rtsp-user", default="admin", help="RTSP username")
    p_rtsp.add_argument(
        "--rtsp-password", help="RTSP password (default: --code or onvif pwd)"
    )
    p_rtsp.add_argument(
        "--rtsp-port", type=int, action="append", help="extra/only RTSP port(s)"
    )
    p_rtsp.add_argument("--path", action="append", help="extra/only RTSP path(s)")

    args = ap.parse_args(argv)

    if args.cmd == "rtsp":
        return _cmd_rtsp(args)

    client = DeviceClient(
        host=args.host,
        port=args.port,
        code=args.code,
        user=args.user,
        password=args.password,
        security=args.security,
        scheme=args.scheme,
        timeout=args.timeout,
        debug=args.debug,
    )

    try:
        if args.cmd == "probe":
            print(f"endpoint : {client.base}{CGI_PATH}")
            try:
                info = client.product_info()
                print(f"model    : {info.get('model')}  fw={info.get('version')}")
                print(f"mac      : {info.get('mac')}")
            except DeviceError as exc:
                print(f"product  : {exc}")

            # VDP firmware: these commands may not exist (device returns -1)
            try:
                net = client.network_config()
                print(f"ip       : {net.get('ipaddress') or net.get('ip')}")
                print(f"http(s)  : {net.get('httpport')} / {net.get('httpsport')}")
                print(f"rtsp     : port={net.get('rtspport')}  url={net.get('rtspurl')}")
            except DeviceError as exc:
                print(f"network  : {exc}")

            try:
                print(f"onvif pwd: {client.onvif_password()}")
            except DeviceError as exc:
                print(f"onvif    : {exc}")

            try:
                root = client.call("get.device.qrcode")
                qr = deep_find(root, "qrcode")
                print(f"qr data  : {_text(qr)}")
            except DeviceError as exc:
                print(f"qrcode   : {exc}")

            try:
                print("doors    :")
                for d in client.attach_info():
                    print(
                        f"  - lock {d['lock']:>2}  ch {d['channel']:<2}  "
                        f"{d['name']!r}  enabled={d['enabled']}"
                    )
            except DeviceError as exc:
                print(f"doors    : {exc}")
            return 0

        if args.cmd == "doors":
            doors = client.attach_info()
            if not doors:
                print("no lock outputs reported by get.device.attachInfo")
                return 1
            _print_json(doors)
            return 0

        if args.cmd == "open":
            if not args.yes:
                answer = input(
                    f"Open door {args.lock} (channel {args.channel}) on {args.host}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("aborted - nothing sent")
                    return 1
            root = client.open_door(
                args.lock,
                args.channel,
                args.unlock_password,
                hash_password=args.hash == "on",
            )
            print("OK - door command accepted")
            if args.debug:
                print(ET.tostring(root, encoding="unicode"))
            return 0

        if args.cmd == "check-password":
            client.check_unlock_password(args.password)
            print("OK - password accepted")
            return 0

        if args.cmd == "raw":
            root = client.call(args.command, args.content)
            print(ET.tostring(root, encoding="unicode"))
            return 0
    except DeviceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 1


def _cmd_rtsp(args) -> int:
    client: Optional[DeviceClient] = None
    net: dict = {}
    if args.code or args.password:
        client = DeviceClient(
            host=args.host,
            port=args.port,
            code=args.code,
            user=args.user,
            password=args.password,
            security=args.security,
            scheme=args.scheme,
            timeout=args.timeout,
            debug=args.debug,
        )
        try:
            net = client.network_config()
            print(
                f"[device] rtsp port={net.get('rtspport')} url={net.get('rtspurl')}",
                file=sys.stderr,
            )
        except DeviceError as exc:
            print(f"[device] could not read network config: {exc}", file=sys.stderr)

    ports: list[int] = []
    for value in [net.get("rtspport")] + (args.rtsp_port or []) + [554]:
        try:
            p = int(value)
        except (TypeError, ValueError):
            continue
        if p > 0 and p not in ports:
            ports.append(p)

    paths = list(args.path or [])
    if net.get("rtspurl"):
        url = str(net["rtspurl"])
        if url.startswith("rtsp://"):
            m = re.match(r"rtsp://([^/:]+)(?::(\d+))?(/.*)?$", url)
            if m:
                if m.group(2) and int(m.group(2)) not in ports:
                    ports.insert(0, int(m.group(2)))
                if m.group(3) and m.group(3) not in paths:
                    paths.append(m.group(3))
        elif not url.startswith("/"):
            paths.append("/" + url)
        else:
            paths.append(url)
    paths += [p for p in RTSP_PATHS if p not in paths]

    candidates = []
    if args.rtsp_password is not None:
        candidates.append((args.rtsp_user, args.rtsp_password))
    else:
        if args.code:
            candidates.append((args.rtsp_user, args.code))
        if client is not None:
            pwd = client.onvif_password()
            if pwd:
                candidates.append((args.rtsp_user, pwd))
        candidates.append((args.rtsp_user, args.code or ""))
        candidates.append(("admin", "admin"))
        candidates.append((args.user, sha256_hex(args.code) if args.code else ""))
    # de-dup
    seen, uniq = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)

    found = 0
    for port in ports:
        if not _tcp_open(args.host, port, args.timeout):
            print(f"[rtsp] {args.host}:{port} closed", file=sys.stderr)
            continue
        print(f"[rtsp] {args.host}:{port} open, probing paths...", file=sys.stderr)
        for path in paths:
            for user, pwd in uniq:
                result = rtsp_describe(args.host, port, path, user, pwd)
                if result:
                    print(
                        f"FOUND  {result['url']}\n"
                        f"  user: {user}\n  pass: {pwd}\n"
                        f"  sdp : {result['sdp'].splitlines()[0] if result['sdp'] else ''}"
                    )
                    found += 1
                    break
            if found:
                break
        if found:
            break
    if not found:
        print(
            "no RTSP stream found. Device may disable RTSP, or the URL path differs.\n"
            "Try: viewdoor.py rtsp --host IP --code CODE --path /your/path --rtsp-port 554",
            file=sys.stderr,
        )
        return 1
    return 0


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main())
