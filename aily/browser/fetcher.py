from __future__ import annotations

import asyncio
from pathlib import Path

from aily.browser.manager import BrowserUseManager, BrowserFetchError


class FetchError(Exception):
    pass


class BrowserFetcher:
    def __init__(self, profile_dir: Path | None = None) -> None:
        self._manager = BrowserUseManager(profile_dir=profile_dir)

    async def fetch(self, url: str, timeout: int = 60) -> str:
        await self._manager.start()
        try:
            return await self._manager.fetch(url, timeout=timeout)
        except BrowserFetchError as exc:
            raise FetchError(str(exc)) from exc
        except asyncio.TimeoutError as exc:
            raise FetchError(f"Timeout fetching {url}: {exc}") from exc
        except Exception as exc:
            raise FetchError(str(exc)) from exc

    async def stop(self) -> None:
        await self._manager.stop()
