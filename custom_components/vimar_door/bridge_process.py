"""Managed QVII -> RTSP bridge subprocess for the embedded HA setup."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_RTSP_PORT,
    PORT_OFFSET_INNER,
    PORT_OFFSET_STATUS,
    PORT_OFFSET_TALK,
)

_LOGGER = logging.getLogger(__name__)

BRIDGE_DIR = Path(__file__).with_name("bridge")
RESTART_DELAY = 5.0
STOP_TIMEOUT = 12.0


class BridgeProcess:
    """Run the bridge inside the HA container, as an isolated subprocess."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        host: str,
        credentials_path: str,
        cloud_path: str,
        ring_webhook_url: str = "",
        rtsp_port: int = DEFAULT_RTSP_PORT,
    ) -> None:
        self._hass = hass
        self._host = host
        self._credentials_path = credentials_path
        self._cloud_path = cloud_path
        self._ring_webhook_url = ring_webhook_url
        self._rtsp_port = rtsp_port
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        """Start the managed loop once."""
        if self._task is not None:
            return
        self._task = self._hass.async_create_background_task(
            self._run(), name="vimar-door-bridge"
        )

    async def stop(self) -> None:
        """Stop the subprocess and the restart loop."""
        self._stopping = True
        process = self._process
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._process = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                self._process = await self._spawn()
                _LOGGER.info("Vimar bridge started (pid %s)", self._process.pid)
                await self._pump(self._process)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the bridge supervised
                _LOGGER.exception("Vimar bridge failed to start")
            finally:
                process, self._process = self._process, None
                if process is not None and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
            if self._stopping:
                break
            _LOGGER.warning(
                "Vimar bridge exited; restarting in %.0fs", RESTART_DELAY
            )
            await asyncio.sleep(RESTART_DELAY)

    async def _spawn(self) -> asyncio.subprocess.Process:
        env = os.environ.copy()
        env.update(
            {
                "CREDENTIALS": self._credentials_path,
                "CLOUD_ACCOUNT": self._cloud_path,
                "CAMERA_DEVICE_HOST": self._host,
                "CAMERA_STREAM_PORT": "34567",
                "CAMERA_CHANNEL": "1",
                "CAMERA_STREAM_ID": "2",
                "RTSP_BIND": "0.0.0.0",
                "RTSP_PORT": str(self._rtsp_port),
                "INNER_PORT": str(self._rtsp_port + PORT_OFFSET_INNER),
                "STATUS_PORT": str(self._rtsp_port + PORT_OFFSET_STATUS),
                "TALK_PORT": str(self._rtsp_port + PORT_OFFSET_TALK),
                "AUDIO": "1",
                "TALK": "1",
                "KEY_REFRESH": "1",
                "RING_WATCH": "1" if self._ring_webhook_url else "0",
                "RING_WEBHOOK_URL": self._ring_webhook_url,
                "PYTHONUNBUFFERED": "1",
            }
        )
        # HA installs integration requirements in config/deps; a fresh
        # subprocess does not inherit that path automatically.
        python_path = [
            str(BRIDGE_DIR),
            str(Path(self._hass.config.path("deps"))),
            env.get("PYTHONPATH", ""),
        ]
        env["PYTHONPATH"] = os.pathsep.join(
            item for item in python_path if item
        )
        return await asyncio.create_subprocess_exec(
            sys.executable,
            str(BRIDGE_DIR / "vimar_bridge.py"),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            close_fds=False,
        )

    async def _pump(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        async for line in process.stdout:
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                _LOGGER.info("Vimar bridge: %s", text)
        await process.wait()
        if process.returncode not in (0, -signal.SIGTERM):
            _LOGGER.warning(
                "Vimar bridge exited with status %s", process.returncode
            )
