"""Offline tests for the cloud CALL journal ring watcher."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from homeassistant import vimar_ring


def _envelope(header: str, content: str = "") -> ET.Element:
    return ET.fromstring(
        "<envelope><header>"
        + header
        + "</header><content>"
        + content
        + "</content></envelope>"
    )


def _login_response():
    return _envelope(
        "<result>0</result><session><id>session</id></session>",
        "<account-id>42</account-id>",
    )


def _alarm_response():
    return _envelope("<result>0</result>")


def _history(*records: tuple[str, int, str]) -> ET.Element:
    rows = ""
    for record_id, event, devid in records:
        rows += (
            "<data><id>"
            + record_id
            + "</id><event>"
            + str(event)
            + "</event><devid>"
            + devid
            + "</devid><chno>0</chno><time>2026-11-21 20:00:00</time>"
            + "<alarmid>"
            + record_id
            + "</alarmid><recordsubresurl></recordsubresurl></data>"
        )
    return _envelope("<result>0</result>", "<record>" + rows + "</record>")


def test_baseline_then_one_ring_and_dedupe(monkeypatch, tmp_path: Path) -> None:
    account = tmp_path / "cloud.json"
    account.write_text(
        '{"account":"[vimar]1","password":"' + "a" * 64 + '","rgn":1}',
        encoding="utf-8",
    )
    responses = [
        _login_response(),
        _alarm_response(),
        _history(("old", 19, "UID")),
        _login_response(),
        _alarm_response(),
        _history(("new", 19, "UID"), ("old", 19, "UID")),
        _login_response(),
        _alarm_response(),
        _history(("new", 19, "UID"), ("old", 19, "UID")),
    ]

    class FakeSession:
        def post(self, _url: str, _xml: bytes) -> ET.Element:
            return responses.pop(0)

    monkeypatch.setattr(vimar_ring, "_CloudSession", FakeSession)
    watcher = vimar_ring.RingWatcher(str(account), "UID", webhook_url="http://ha/api/webhook/test", poll_seconds=2)
    posted = []
    watcher._post_webhook = posted.append  # type: ignore[method-assign]

    watcher._poll_once()
    assert watcher.status()["ring_count"] == 0
    assert watcher.status()["baseline_ready"] is True

    watcher._poll_once()
    assert watcher.status()["ring_count"] == 1
    assert posted[0]["record_id"] == "new"
    assert posted[0]["channel"] == 1
    assert posted[0]["event"] == "ring"

    watcher._poll_once()
    assert watcher.status()["ring_count"] == 1
    assert len(posted) == 1


def test_ignores_event_and_foreign_device(monkeypatch, tmp_path: Path) -> None:
    account = tmp_path / "cloud.json"
    account.write_text(
        '{"account":"[vimar]1","password":"' + "a" * 64 + '","rgn":1}',
        encoding="utf-8",
    )
    responses = [
        _login_response(),
        _alarm_response(),
        _history(("accepted", 20, "UID"), ("foreign", 19, "OTHER")),
        _login_response(),
        _alarm_response(),
        _history(("accepted", 20, "UID"), ("foreign", 19, "OTHER")),
    ]

    class FakeSession:
        def post(self, _url: str, _xml: bytes) -> ET.Element:
            return responses.pop(0)

    monkeypatch.setattr(vimar_ring, "_CloudSession", FakeSession)
    watcher = vimar_ring.RingWatcher(str(account), "UID", webhook_url="http://ha/api/webhook/test", poll_seconds=2)
    watcher._post_webhook = lambda _payload: True  # type: ignore[method-assign]
    watcher._poll_once()
    watcher._poll_once()
    assert watcher.status()["ring_count"] == 0
