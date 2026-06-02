from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration_seconds: float | None


class WhisperTranscriber:
    """Transcribe audio using OpenAI Whisper API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "whisper-1",
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout),
        )

    async def transcribe(
        self,
        audio_path: Path,
        prompt: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file to text."""
        url = f"{self.base_url}/audio/transcriptions"

        logger.info("Transcribing audio: %s", audio_path)

        # Build form data
        data: dict[str, str] = {"model": self.model}
        if prompt:
            data["prompt"] = prompt
        if language:
            data["language"] = language

        try:
            with open(audio_path, "rb") as audio_file:
                files = {"file": (audio_path.name, audio_file, "audio/mpeg")}
                response = await self._client.post(url, data=data, files=files)

            response.raise_for_status()
            result = response.json()

            text = result.get("text", "").strip()
            detected_language = result.get("language")
            duration = result.get("duration")

            logger.info(
                "Transcription complete: %s chars, language=%s",
                len(text),
                detected_language,
            )

            return TranscriptionResult(
                text=text,
                language=detected_language,
                duration_seconds=duration,
            )

        except httpx.HTTPStatusError as e:
            logger.error("Transcription failed: %s - %s", e.response.status_code, e.response.text)
            raise TranscriptionError(f"Whisper API error: {e.response.status_code}") from e
        except Exception as e:
            logger.exception("Transcription failed")
            raise TranscriptionError(f"Transcription failed: {e}") from e

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class TranscriptionError(Exception):
    """Raised when transcription fails."""

    pass
