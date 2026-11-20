"""Minimal async client for the Vimar / Qualvision local `/tdkcgi` endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Final
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import aiohttp

from .const import DEFAULT_PORT, DOOR_CHANNEL

DEFAULT_USERNAME: Final = "adminapp2"
REQUEST_TIMEOUT: Final = 10
_HEX_64 = re.compile(r"[0-9a-fA-F]{64}")


class VimarError(Exception):
    """The device answered with an error (or an unparseable response)."""

    def __init__(self, code: str) -> None:
        """Initialize the error."""
        self.code = code
        super().__init__(f"device error {code}")


def encode_device_password(code: str) -> str:
    """Encode a label code or an already-hashed out-auth-code.

    The app sends the 64-hex cloud `out-auth-code` verbatim with
    `passwordencode=1`; shorter sticker codes are SHA-256 hashed first.
    """
    code = code.strip()
    if _HEX_64.fullmatch(code):
        return code.lower()
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def build_envelope(password: str, command: str, content: str = "") -> bytes:
    """Build the XML envelope used by `/tdkcgi`."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<envelope><header><security>username</security>"
        f"<username>{DEFAULT_USERNAME}</username>"
        f"<password>{escape(password)}</password>"
        "<passwordencode>1</passwordencode></header>"
        f"<body><command>{escape(command)}</command>"
        f"<content>{content}</content></body></envelope>"
    ).encode("utf-8")


def parse_error(root: ET.Element) -> str:
    """Return the `<error>` code, or `""` for success/no error element."""
    element = root.find(".//error")
    if element is None:
        return ""
    return (element.text or "").strip()


class VimarClient:
    """Talk to one doorbell over the LAN CGI API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        auth_code: str,
        port: int = DEFAULT_PORT,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._url = f"http://{host}:{port}/tdkcgi"
        self._password = encode_device_password(auth_code)

    async def _post(self, command: str, content: str = "") -> ET.Element:
        """POST one command and return the parsed XML envelope."""
        data = build_envelope(self._password, command, content)
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with asyncio.timeout(REQUEST_TIMEOUT + 2):
            async with self._session.post(
                self._url,
                data=data,
                timeout=timeout,
                headers={"Content-Type": "application/xml;charset=utf-8"},
            ) as response:
                if response.status == 401:
                    raise VimarError("401")
                response.raise_for_status()
                text = await response.text()

        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise VimarError("invalid-xml") from err

        code = parse_error(root)
        if code not in ("", "0"):
            raise VimarError(code)
        return root

    async def async_validate(self) -> str:
        """Validate host and auth code, returning the firmware version."""
        root = await self._post("get.product.info")
        version = root.findtext(".//version")
        return (version or "").strip()

    async def async_open_door(self, locknumber: int) -> None:
        """Fire one physical relay (1 or 2)."""
        content = (
            f"<door>{DOOR_CHANNEL}</door>"
            f"<locknumber>{int(locknumber)}</locknumber>"
            f"<password>{escape(self._password)}</password>"
        )
        await self._post("set.device.opendoor", content)
