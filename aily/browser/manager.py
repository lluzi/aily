from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from multiprocessing.connection import Client
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BrowserFetchError(Exception):
    pass


class BrowserUseManager:
    """Manages browser automation via subprocess with support for agent-based extraction."""

    def __init__(
        self,
        profile_dir: Path | None = None,
        authkey: bytes = b"aily-browser",
        worker_type: str = "playwright",
        llm_config: dict[str, Any] | None = None,
    ) -> None:
        self.profile_dir = profile_dir or (Path.home() / ".aily" / "browser_profile")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.authkey = authkey
        self.worker_type = worker_type
        self.llm_config = llm_config or {}
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                await self._spawn()

    async def stop(self) -> None:
        async with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    await asyncio.wait_for(
                        self._send({"type": "shutdown"}),
                        timeout=5,
                    )
                except Exception:
                    pass
                self._proc.terminate()
                try:
                    await asyncio.to_thread(self._proc.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    await asyncio.to_thread(self._proc.wait)
            self._proc = None
            self._port = None

    async def fetch(self, url: str, timeout: int = 60, use_personal_profile: bool = False) -> str:
        """
        Fetch content from a URL using browser automation.

        Args:
            url: The URL to fetch
            timeout: Maximum time to spend on the task
            use_personal_profile: If True, use the user's Chrome profile with existing logins

        Returns:
            Extracted text content
        """
        async with self._lock:
            # Pass authentication options via llm_config
            fetch_config = self.llm_config.copy()
            if use_personal_profile:
                fetch_config['use_personal_profile'] = True
                fetch_config['headless'] = False
            result = await self._fetch_with_retry(url, timeout, fetch_config)
            if isinstance(result, dict):
                return result.get("text", "")
            return result

    async def _fetch_with_retry(self, url: str, timeout: int, fetch_config: dict | None = None):
        for attempt in range(2):
            if self._proc is None or self._proc.poll() is not None:
                await self._spawn()
            try:
                msg = {
                    "type": "fetch",
                    "url": url,
                    "timeout": timeout,
                    "profile_dir": str(self.profile_dir),
                }
                if self.worker_type == "agent":
                    # Use provided fetch_config or fall back to default llm_config
                    msg["llm_config"] = fetch_config if fetch_config is not None else self.llm_config
                return await self._send(msg, timeout=timeout + 5)
            except (ConnectionRefusedError, ConnectionResetError, EOFError, OSError) as exc:
                logger.warning("Subprocess connection lost (attempt %s): %s", attempt + 1, exc)
                self._proc = None
                self._port = None
                if attempt == 0:
                    continue
                raise BrowserFetchError("Browser subprocess crashed") from exc
            except BrowserFetchError:
                raise
            except Exception as exc:
                raise BrowserFetchError(str(exc)) from exc
        raise BrowserFetchError("Browser subprocess unavailable")

    async def _send(self, msg: dict, timeout: float = 65):
        if self._port is None:
            raise BrowserFetchError("Browser subprocess not started")
        loop = asyncio.get_running_loop()
        def _call():
            with Client(("localhost", self._port), authkey=self.authkey) as conn:
                conn.send(msg)
                return conn.recv()
        try:
            response = await asyncio.wait_for(loop.run_in_executor(None, _call), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise BrowserFetchError("IPC timeout waiting for browser subprocess") from exc
        if response.get("status") == "error":
            raise BrowserFetchError(response.get("message", "Unknown error"))
        if response.get("status") != "ok":
            raise BrowserFetchError(f"Unexpected response: {response}")
        return response

    async def _spawn(self) -> None:
        if self.worker_type == "agent":
            script = Path(__file__).parent / "agent_worker.py"
        else:
            script = Path(__file__).parent / "subprocess_worker.py"
        if not script.exists():
            raise BrowserFetchError(f"Worker script not found: {script}")
        cmd = [
            sys.executable,
            str(script),
            "--profile-dir", str(self.profile_dir),
            "--authkey", self.authkey.decode(),
        ]
        # Set up environment with virtualenv site-packages for subprocess
        env = dict(subprocess.os.environ)
        venv_site = Path(sys.executable).parent.parent / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        if venv_site.exists():
            env["PYTHONPATH"] = str(venv_site) + (":" + env.get("PYTHONPATH", ""))
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        assert self._proc.stdout is not None
        try:
            line = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, self._proc.stdout.readline),
                timeout=30,
            )
        except asyncio.TimeoutError as exc:
            if self._proc:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            raise BrowserFetchError("Browser subprocess failed to report READY in time") from exc
        if not line.startswith("READY"):
            self._proc.kill()
            stdout = line.strip()
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise BrowserFetchError(f"Browser subprocess failed to start: {stdout} {stderr}")
        try:
            self._port = int(line.split()[1])
        except (IndexError, ValueError) as exc:
            self._proc.kill()
            raise BrowserFetchError(f"Browser subprocess reported invalid port: {line.strip()}") from exc
        logger.info("Browser worker started on port %s (type=%s)", self._port, self.worker_type)