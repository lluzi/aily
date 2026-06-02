from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

from aily.queue.db import QueueDB


class ObsidianAPIError(Exception):
    pass


class ObsidianWriter:
    def __init__(
        self,
        api_key: str,
        vault_path: str,
        port: int = 27123,
        draft_folder: str = "Aily Drafts",
        queue_db: Optional[QueueDB] = None,
    ) -> None:
        self.api_key = api_key.strip()
        if self.api_key.lower().startswith("bearer "):
            raise ValueError("Obsidian REST API key must be configured without the literal 'Bearer ' prefix")
        self.vault_path = Path(vault_path)
        self.base_url = f"http://127.0.0.1:{port}"
        self.draft_folder = draft_folder
        self.queue_db = queue_db

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "text/markdown",
        }

    def _file_path(self, title: str) -> str:
        safe = title.replace("/", "_").replace("..", "_")[:120]
        return f"{self.draft_folder}/{safe}.md"

    def _frontmatter(self, source_url: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"---\naily_generated: true\naily_written_at: \"{ts}\"\nsource_url: {json.dumps(source_url)}\n---\n\n"

    async def write_note(
        self,
        title: str,
        markdown: str,
        source_url: str,
    ) -> str:
        path = self._file_path(title)
        payload = self._frontmatter(source_url) + markdown
        async with aiohttp.ClientSession() as session:
            try:
                await self._put_with_retry(session, path, payload, retries=1)
            except aiohttp.ClientConnectionError as exc:
                await asyncio.sleep(2)
                try:
                    await self._put_with_retry(session, path, payload, retries=0)
                except aiohttp.ClientConnectionError:
                    raise ObsidianAPIError(
                        "Obsidian Local REST API plugin is not running. Please start Obsidian and enable the plugin."
                    ) from exc
            except Exception as exc:
                raise ObsidianAPIError(str(exc)) from exc
        if self.queue_db is not None:
            await self.queue_db.save_note_snapshot(path, payload)
        return path

    async def _put_with_retry(
        self,
        session: aiohttp.ClientSession,
        path: str,
        payload: str,
        retries: int,
    ) -> None:
        url = f"{self.base_url}/vault/{path}"
        async with session.put(url, headers=self._headers(), data=payload) as resp:
            if resp.status == 404:
                raise ObsidianAPIError(
                    "Obsidian Local REST API plugin is not running. Please start Obsidian and enable the plugin."
                )
            resp.raise_for_status()
