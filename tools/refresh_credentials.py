#!/usr/bin/env python3
"""Refresh the 32-byte QVII stream key from the Qualvision cloud.

The doorbell's stream key can rotate (cloud token renewal, re-pairing, app
revalidation).  The old bridge then opens TCP 34567, the setup succeeds, but
the play is silently ignored while the Vimar app still works with the new key.

This tool logs in with the Qualvision account, calls `get-device-token` and
updates a `stream_credentials.json`.  It never prints the key or the account
password.

Example:

    python tools/refresh_credentials.py \
        --account-json work/cloud-account.json \
        --out work/stream_credentials.json

The device id is read from the existing credentials file unless `--device-id`
is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:  # pragma: no cover
    print("requests is required: python -m pip install requests")
    raise SystemExit(1)

AUTH_URL = "https://r1-2-sec.qvcloud.net/auth/user"
OEM = "G0078"
APP_ID = 2078
CLIENT_TYPE = 3
CLIENT_ID = f"00{CLIENT_TYPE}-{APP_ID}-0000000000000000000000000000"
LOGIN_CLASS = "com.quvii.qvweb.userauth.bean.request.LoginReqContent"
TOKEN_CLASS = "com.quvii.qvweb.userauth.bean.request.DevDynamicPwdGetReqContent"


def envelope(command: str, content: ET.Element, session: str, seq: int) -> bytes:
    """Build the Qualvision auth XML envelope."""
    env = ET.Element("envelope")
    cls = LOGIN_CLASS if command == "login" else TOKEN_CLASS
    ET.SubElement(env, "content", {"class": cls}).extend([content])
    header = ET.SubElement(env, "header")
    client = ET.SubElement(header, "client")
    ET.SubElement(client, "app").text = str(APP_ID)
    ET.SubElement(client, "id").text = CLIENT_ID
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


def post(session: requests.Session, xml: bytes) -> ET.Element:
    """POST one XML request to the Qualvision user service."""
    response = session.post(
        AUTH_URL,
        data=xml,
        headers={
            "Content-Type": "application/xml",
            "Charset": "utf-8",
            "Accept-Language": "en-US",
            "User-Agent": "okhttp/3.12.13",
        },
        timeout=40,
    )
    response.raise_for_status()
    return ET.fromstring(response.content)


def main() -> int:
    """Log in, fetch a fresh device token and update the credentials file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account-json",
        default="work/cloud-account.json",
        help="JSON with account, password and rgn",
    )
    parser.add_argument(
        "--out",
        default="work/stream_credentials.json",
        help="credentials JSON to update",
    )
    parser.add_argument("--device-id", help="override device id")
    args = parser.parse_args()

    account = json.loads(Path(args.account_json).read_text(encoding="utf-8"))
    out_path = Path(args.out)
    current = (
        json.loads(out_path.read_text(encoding="utf-8"))
        if out_path.exists()
        else {}
    )
    device_id = args.device_id or current.get("device_id")
    if not device_id:
        parser.error("missing --device-id (no device_id in the output file)")

    session = requests.Session()
    login = ET.Element("content")
    ET.SubElement(login, "account").text = account["account"]
    ET.SubElement(login, "auth-code").text = ""
    ET.SubElement(login, "ip-region-id").text = str(account.get("rgn", 1))
    ET.SubElement(login, "password").text = account["password"]
    ET.SubElement(login, "auth-type").text = "0"

    root = post(session, envelope("login", login, "", 1))
    result = (root.findtext("./header/result") or "").strip()
    if result and result != "0":
        print(f"login failed: result={result}", file=sys.stderr)
        return 2
    auth_session = (root.findtext("./header/session/id") or "").strip()
    if not auth_session:
        print("login failed: no session id", file=sys.stderr)
        return 2

    token_content = ET.Element("content")
    ET.SubElement(token_content, "device-id").text = device_id
    root = post(
        session,
        envelope("get-device-token", token_content, auth_session, 0),
    )
    result = (root.findtext("./header/result") or "").strip()
    if result and result != "0":
        print(f"get-device-token failed: result={result}", file=sys.stderr)
        return 3
    content = root.find("./content")
    if content is None:
        print("get-device-token failed: empty content", file=sys.stderr)
        return 3

    stream_key = (content.findtext("data-encode-key") or "").strip()
    auth_code = (content.findtext("out-auth-code") or "").strip()
    if len(stream_key) != 32:
        print(
            f"invalid stream key length {len(stream_key)} (expected 32)",
            file=sys.stderr,
        )
        return 4

    old_key = current.get("stream_key")
    old_code = current.get("auth_code")
    data = {
        "version": 1,
        "device_id": device_id,
        "stream_key": stream_key,
        "auth_code": auth_code or old_code or "",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    print(f"device       {device_id}")
    print(f"key changed  {old_key != stream_key}")
    print(f"auth changed {old_code is not None and old_code != data['auth_code']}")
    print(f"key length   {len(stream_key)}")
    print(f"auth length  {len(data['auth_code'])}")
    print(f"expires      {content.findtext('password-expired') or 'unknown'}")
    print(f"updated      {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
