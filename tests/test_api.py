"""Offline tests for the Vimar CGI client helpers."""

from __future__ import annotations

import hashlib
from xml.etree import ElementTree as ET

from custom_components.vimar_door.api import (
    build_envelope,
    encode_device_password,
    parse_error,
)


def test_password_64_hex_is_kept_verbatim() -> None:
    """The cloud out-auth-code is already the password hash."""
    code = "A" * 64
    assert encode_device_password(code) == "a" * 64


def test_sticker_code_is_sha256_hashed() -> None:
    """Short label codes are hashed like the app does."""
    code = "ABCD1234"
    assert encode_device_password(code) == hashlib.sha256(
        code.encode()
    ).hexdigest()


def test_envelope_contains_command_and_escaped_password() -> None:
    """The XML envelope is well formed and escapes special characters."""
    raw = build_envelope("a&b", "get.product.info").decode()
    root = ET.fromstring(raw)
    assert root.findtext(".//command") == "get.product.info"
    assert root.findtext(".//password") == "a&b"
    assert root.findtext(".//username") == "adminapp2"


def test_parse_error_success_and_failure() -> None:
    """The `<error>` element is optional on success."""
    assert parse_error(ET.fromstring("<envelope><body/></envelope>")) == ""
    assert (
        parse_error(
            ET.fromstring("<envelope><body><error>0</error></body></envelope>")
        )
        == "0"
    )
    assert (
        parse_error(
            ET.fromstring(
                "<envelope><body><error>-10028</error></body></envelope>"
            )
        )
        == "-10028"
    )
