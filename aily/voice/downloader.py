from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class VoiceDownloadResult:
    file_path: Path
    file_name: str
    mime_type: str


class FeishuVoiceDownloader:
    """Download voice messages from Feishu Drive API."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        base_url: str = "https://open.feishu.cn/open-apis",
        temp_dir: Path | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url
        self.temp_dir = temp_dir or Path("/tmp/aily_voice")
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    async def _get_tenant_access_token(self) -> str:
        """Get or refresh tenant access token."""
        import time

        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

                if data.get("code") != 0:
                    raise FeishuVoiceError(f"Token fetch failed: {data.get('msg')}")

                self._access_token = data["tenant_access_token"]
                self._token_expires_at = time.time() + data["expire"]
                return self._access_token

    async def download_voice(
        self,
        file_key: str,
        file_name: str | None = None,
    ) -> VoiceDownloadResult:
        """Download a voice file from Feishu Drive."""
        token = await self._get_tenant_access_token()

        # Feishu Drive media download API
        url = f"{self.base_url}/drive/v1/medias/{file_key}/download"

        headers = {"Authorization": f"Bearer {token}"}

        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Use provided name or generate one
        safe_name = file_name or f"voice_{file_key}.mp3"
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in "._-")
        file_path = self.temp_dir / safe_name

        logger.info("Downloading voice file: %s -> %s", file_key, file_path)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    raise FeishuVoiceError(f"File not found or was deleted: {file_key}")
                resp.raise_for_status()

                mime_type = resp.headers.get("Content-Type", "audio/mpeg")

                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)

        logger.info("Voice file downloaded: %s (%s bytes)", file_path, file_path.stat().st_size)

        return VoiceDownloadResult(
            file_path=file_path,
            file_name=safe_name,
            mime_type=mime_type,
        )


class FeishuVoiceError(Exception):
    """Raised when voice download fails."""

    pass
