"""Tests for the managed-bridge free-port scan."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from custom_components.vimar_door.const import (
    BRIDGE_PORT_OFFSETS,
    CONF_STREAM_URL,
    DOMAIN,
    STREAM_PORT_ATTEMPTS,
    STREAM_PORT_MAX,
    STREAM_PORT_MIN,
)
from custom_components.vimar_door.ports import (
    block_free,
    entry_stream_url,
    find_stream_port,
    port_free,
    stream_url,
)

ENTRY_ID = "entry1"
STORED_URL = "rtsp://127.0.0.1:8556/live"
RUNNING_URL = "rtsp://127.0.0.1:8560/live"


def _entry() -> SimpleNamespace:
    """A config entry with the stored (preferred) stream URL."""
    return SimpleNamespace(
        entry_id=ENTRY_ID,
        data={CONF_STREAM_URL: STORED_URL},
        options={},
    )


def _free_run(start: int, length: int) -> int:
    """Return the first port where ``length`` consecutive ports are usable."""
    candidate = start
    while candidate + length < 65000:
        if all(port_free(candidate + i) for i in range(length)):
            return candidate
        candidate += 1
    raise AssertionError("no free port run found")


@pytest.fixture
def hoard():
    """Bind sockets on demand and close them on teardown."""
    sockets: list[socket.socket] = []

    def _take(port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", port))
        sock.listen(1)
        sockets.append(sock)
        return sock

    yield _take
    for sock in sockets:
        sock.close()


def test_stream_url_matches_default_shape() -> None:
    """The URL follows the bridge's single-stream layout."""
    assert stream_url(8556) == "rtsp://127.0.0.1:8556/live"
    assert stream_url(9090, "10.0.0.5") == "rtsp://10.0.0.5:9090/live"


def test_whole_block_must_be_free(hoard) -> None:
    """A block is unusable when any of its ports is taken."""
    base = _free_run(48000, 20)
    assert block_free(base)
    hoard(base + 2)
    assert not block_free(base)


def test_scan_walks_forward_to_the_next_free_block(hoard) -> None:
    """An occupied base port moves the bridge to the next free block."""
    base = _free_run(48000, 20)
    hoard(base)
    found = find_stream_port(base, attempts=STREAM_PORT_ATTEMPTS)
    assert found is not None
    assert found > base
    assert block_free(found)


def test_scan_gives_up_after_the_attempt_budget(hoard) -> None:
    """An exhausted budget returns None instead of a busy port."""
    base = _free_run(48000, 20)
    hoard(base)
    assert find_stream_port(base, attempts=1) is None


def test_reserved_blocks_are_skipped() -> None:
    """A second doorbell never reuses another entry's port block."""
    base = _free_run(48000, 20)
    found = find_stream_port(base, reserved={base})
    assert found is not None
    assert found - base > max(BRIDGE_PORT_OFFSETS)


def test_out_of_range_preferred_port_is_rejected() -> None:
    """Low or high base ports cannot produce a valid block."""
    assert find_stream_port(80) is None
    assert find_stream_port(STREAM_PORT_MAX + 5) is None
    assert STREAM_PORT_MIN < STREAM_PORT_MAX


def test_entry_stream_url_prefers_the_running_port() -> None:
    """The camera uses the port the bridge actually bound."""
    hass = SimpleNamespace(
        data={DOMAIN: {f"stream_url:{ENTRY_ID}": RUNNING_URL}}
    )
    assert entry_stream_url(hass, _entry()) == RUNNING_URL
    hass.data[DOMAIN].clear()
    assert entry_stream_url(hass, _entry()) == STORED_URL
