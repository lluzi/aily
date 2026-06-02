"""Video processor using ffmpeg, configurable multimodal understanding, and Whisper fallback."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import aiohttp
from PIL import Image

from aily.chaos.processors.base import ContentProcessor
from aily.chaos.types import ExtractedContentMultimodal, TimestampedSegment, VisualElement
from aily.config import SETTINGS
from aily.llm.provider_routes import PrimaryLLMRoute, ResolvedLLMRoute

logger = logging.getLogger(__name__)


class VideoProcessor(ContentProcessor):
    """Process video files: extract frames, transcribe audio, analyze visuals."""

    def __init__(self, config, llm_client=None) -> None:
        super().__init__(config, llm_client)
        self._vision_route: ResolvedLLMRoute | None = None

    async def process(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Process video file."""
        logger.info("Processing video: %s", file_path.name)

        try:
            # Get video info
            video_info = await self._get_video_info(file_path)
            duration = video_info.get("duration", 0)

            # Extract frames for visual analysis
            visual_elements = []
            if self.config.video.enable_visual_analysis:
                visual_elements = await self._extract_key_frames(file_path, duration)

            # Transcribe audio
            transcript = None
            segments = []

            # Try Kimi multimodal transcription first, fallback to Whisper.
            transcript_result = await self._transcribe_with_kimi_video(file_path)
            if not transcript_result:
                transcript_result = await self._transcribe_with_whisper(file_path)

            if transcript_result:
                transcript = transcript_result.get("text", "")
                segments = transcript_result.get("segments", [])

            # Build text
            text_parts = []
            if transcript:
                text_parts.append(f"## Transcript\n\n{transcript}")

            if visual_elements:
                text_parts.append(f"\n## Visual Elements\n")
                text_parts.append(f"Extracted {len(visual_elements)} key frames from video")

            text = "\n\n".join(text_parts) if text_parts else f"[Video: {file_path.name}]"

            return ExtractedContentMultimodal(
                text=text,
                title=file_path.stem.replace("_", " ").replace("-", " ").title(),
                source_type="video",
                source_path=file_path,
                visual_elements=visual_elements,
                transcript=transcript,
                segments=segments,
                processing_method="video_pipeline",
                metadata={
                    "duration": duration,
                    "format": file_path.suffix.lower(),
                    "frames_extracted": len(visual_elements),
                    "has_transcript": transcript is not None,
                    "vision_provider": self._resolve_vision_route().provider,
                    "vision_model": self._resolve_vision_route().model,
                    **video_info,
                },
            )

        except Exception as e:
            logger.exception("Failed to process video: %s", e)
            return None

    async def _get_video_info(self, file_path: Path) -> dict:
        """Get video metadata using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration,bit_rate",
                "-show_entries", "format=duration,bit_rate,size",
                "-of", "json",
                str(file_path),
            ]

            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await result.communicate()

            if result.returncode == 0:
                import json
                data = json.loads(stdout.decode())

                stream = data.get("streams", [{}])[0]
                fmt = data.get("format", {})

                return {
                    "width": stream.get("width", 0),
                    "height": stream.get("height", 0),
                    "duration": float(stream.get("duration", 0) or fmt.get("duration", 0)),
                    "bitrate": int(stream.get("bit_rate", 0) or fmt.get("bit_rate", 0)),
                    "size": int(fmt.get("size", 0)),
                }

        except Exception as e:
            logger.warning("Failed to get video info: %s", e)

        return {"duration": 0}

    async def _extract_key_frames(self, file_path: Path, duration: float) -> list[VisualElement]:
        """Extract key frames from video for visual analysis."""
        visual_elements = []

        if duration == 0:
            return visual_elements

        # Calculate frame timestamps
        interval = self.config.video.extract_frames_every_n_seconds
        timestamps = list(range(0, int(duration), interval))

        # Limit max frames
        if len(timestamps) > self.config.video.max_frames_per_video:
            step = len(timestamps) // self.config.video.max_frames_per_video
            timestamps = timestamps[::step][:self.config.video.max_frames_per_video]

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, timestamp in enumerate(timestamps):
                try:
                    frame_path = Path(tmpdir) / f"frame_{i:04d}.jpg"

                    # Extract frame using ffmpeg
                    cmd = [
                        "ffmpeg",
                        "-ss", str(timestamp),
                        "-i", str(file_path),
                        "-vframes", "1",
                        "-q:v", "2",
                        "-f", "image2",
                        str(frame_path),
                    ]

                    result = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await result.wait()

                    if frame_path.exists():
                        # Load and analyze frame
                        base64_frame = await self._load_frame(frame_path)
                        analysis = await self._analyze_frame(base64_frame)

                        element = VisualElement(
                            element_id=f"frame_{i}",
                            element_type="video_frame",
                            description=analysis or f"Frame at {timestamp}s",
                            timestamp=float(timestamp),
                            base64_data=base64_frame[:1000] + "...",
                        )
                        visual_elements.append(element)

                except Exception as e:
                    logger.warning("Failed to extract frame at %ds: %s", timestamp, e)

        return visual_elements

    async def _load_frame(self, frame_path: Path) -> str:
        """Load frame image and convert to base64."""
        image = Image.open(frame_path)

        if image.mode != "RGB":
            image = image.convert("RGB")

        # Resize for LLM
        max_size = self.config.video.resize_frames_to
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    async def _analyze_frame(self, base64_image: str) -> str | None:
        """Analyze a video frame using the configured multimodal route."""
        route = self._resolve_vision_route()
        if not route.api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {route.api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": route.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Briefly describe what's happening in this video frame (1-2 sentences).",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 256,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._chat_completions_url(route),
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        return None

                    result = await response.json()
                    return result["choices"][0]["message"]["content"]

        except Exception as e:
            logger.warning("Frame analysis failed: %s", e)
            return None

    async def _transcribe_with_kimi_video(self, file_path: Path) -> dict | None:
        """Transcribe spoken content using the configured provider's native video input support."""
        route = self._resolve_vision_route()
        if not route.api_key:
            return None

        try:
            video_size = file_path.stat().st_size
            if video_size > 25 * 1024 * 1024:
                logger.info("Skipping Kimi video transcription for %s because file is too large", file_path.name)
                return None

            video_bytes = await asyncio.to_thread(file_path.read_bytes)
            video_mime = file_path.suffix.lower().lstrip(".") or "mp4"
            video_url = f"data:video/{video_mime};base64,{base64.b64encode(video_bytes).decode('utf-8')}"

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": route.model,
                    "messages": [
                        {"role": "system", "content": "You transcribe spoken content from videos and return structured JSON."},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "video_url",
                                    "video_url": {"url": video_url},
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        "Transcribe the spoken audio in this video. "
                                        "Return valid JSON with keys: text (string) and segments (array). "
                                        "If timestamps are unavailable, return an empty segments array."
                                    ),
                                },
                            ],
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 2048,
                }

                async with session.post(
                    self._chat_completions_url(route),
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {route.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    if response.status != 200:
                        logger.warning("Video transcription failed with status %s", response.status)
                        return None

                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]

            import json

            parsed = json.loads(content)
            text = str(parsed.get("text", "")).strip()

            segments = []
            for seg in parsed.get("segments", []):
                if not isinstance(seg, dict):
                    continue
                segments.append(TimestampedSegment(
                    start_time=seg.get("start", 0),
                    end_time=seg.get("end", 0),
                    text=seg.get("text", ""),
                    confidence=seg.get("confidence", 1.0),
                ))

            if not text:
                return None

            return {"text": text, "segments": segments}

        except Exception as e:
            logger.warning("Video transcription failed: %s", e)
            return None

    async def _transcribe_with_whisper(self, file_path: Path) -> dict | None:
        """Fallback transcription using faster-whisper."""
        try:
            from faster_whisper import WhisperModel

            # Load model (auto-downloads on first use)
            model_size = self.config.video.whisper_model
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

            # Extract audio to temp file first (faster-whisper works best with audio files)
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                audio_path = tmp.name

            cmd = [
                "ffmpeg",
                "-i", str(file_path),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                audio_path,
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

            if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
                logger.warning(" faster-whisper: audio extraction failed")
                return None

            # Transcribe in thread pool
            loop = asyncio.get_event_loop()
            segments_iter, info = await loop.run_in_executor(
                None,
                lambda: model.transcribe(audio_path, beam_size=5),
            )

            text_parts = []
            segments = []
            for seg in segments_iter:
                text_parts.append(seg.text.strip())
                segments.append(TimestampedSegment(
                    start_time=seg.start,
                    end_time=seg.end,
                    text=seg.text.strip(),
                    confidence=seg.avg_logprob,
                ))

            os.unlink(audio_path)

            return {"text": " ".join(text_parts), "segments": segments}

        except Exception as e:
            logger.warning(" faster-whisper transcription failed: %s", e)
            return None

    def can_process(self, file_path: Path) -> bool:
        """Check if file is a video."""
        ext = file_path.suffix.lower()
        return ext.lstrip(".") in self.config.video.supported_formats

    def _resolve_vision_route(self) -> ResolvedLLMRoute:
        if self._vision_route is None:
            self._vision_route = PrimaryLLMRoute.resolve_route(SETTINGS, workload="chaos.vision")
        return self._vision_route

    @staticmethod
    def _chat_completions_url(route: ResolvedLLMRoute) -> str:
        return f"{route.base_url.rstrip('/')}/chat/completions"
