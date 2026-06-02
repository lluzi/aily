"""Drainage system - collects all inputs (rain) into Aily.

Every drop of information enters here. No leaks. All rain is captured
and channeled into the appropriate stream.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RainType(Enum):
    """Types of information rain."""

    CHAT = auto()  # Simple conversation
    URL = auto()  # Link to external content
    DOCUMENT = auto()  # PDF, DOC, etc
    VOICE = auto()  # Audio message
    IMAGE = auto()  # Image for OCR
    CLIPBOARD = auto()  # Passive capture
    SESSION = auto()  # Agent/session transcript


class StreamType(Enum):
    """Processing streams for different content."""

    DIRECT = auto()  # Chat → immediate response
    FETCH_ANALYZE = auto()  # URL → fetch → analyze
    TRANSCRIBE_ANALYZE = auto()  # Voice → transcribe → analyze
    EXTRACT_ANALYZE = auto()  # Document → extract → analyze
    OCR_ANALYZE = auto()  # Image → OCR → analyze
    BATCH_DIGEST = auto()  # Accumulated → daily digest


@dataclass
class RainDrop:
    """A single unit of information entering the system.

    Every input becomes a RainDrop. Immutable. Tracked.
    """

    id: str
    rain_type: RainType
    content: str
    raw_bytes: Optional[bytes] = None
    source: str = ""  # feishu, clipboard, manual, etc
    source_id: str = ""  # message_id, file_key, etc
    creator_id: str = ""  # open_id, user_id
    created_at: datetime = field(default_factory=datetime.utcnow)
    stream_type: StreamType = StreamType.DIRECT
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = self._generate_id()

    def _generate_id(self) -> str:
        """Generate unique ID from content + timestamp."""
        content_hash = hashlib.sha256(
            f"{self.content}:{self.created_at.isoformat()}".encode()
        ).hexdigest()[:16]
        return f"drop_{content_hash}"

    @property
    def is_analyzable(self) -> bool:
        """Can this drop be analyzed for insights?"""
        return self.stream_type in (
            StreamType.FETCH_ANALYZE,
            StreamType.TRANSCRIBE_ANALYZE,
            StreamType.EXTRACT_ANALYZE,
            StreamType.OCR_ANALYZE,
        )

    @property
    def requires_immediate_response(self) -> bool:
        """Does this drop need immediate acknowledgment?"""
        return self.rain_type in (RainType.CHAT, RainType.URL)


@dataclass
class Stream:
    """A processing channel for RainDrops.

    Streams collect drops of similar type and flow them
    toward the reservoir.
    """

    stream_type: StreamType
    drops: list[RainDrop] = field(default_factory=list)
    flow_rate: int = 10  # drops per batch
    created_at: datetime = field(default_factory=datetime.utcnow)

    def add_drop(self, drop: RainDrop) -> None:
        """Add a drop to this stream."""
        self.drops.append(drop)
        logger.info(
            "[Stream:%s] Added drop %s (total: %d)",
            self.stream_type.name,
            drop.id[:12],
            len(self.drops),
        )

    def is_full(self) -> bool:
        """Has stream reached batch size?"""
        return len(self.drops) >= self.flow_rate

    def flush(self) -> list[RainDrop]:
        """Remove all drops and return them."""
        flushed = self.drops.copy()
        self.drops.clear()
        logger.info(
            "[Stream:%s] Flushed %d drops to reservoir",
            self.stream_type.name,
            len(flushed),
        )
        return flushed


class DrainageSystem:
    """Collects all information rain and channels it into streams.

    This is the entry point. No information enters Aily without
    passing through the drainage system.
    """

    def __init__(self) -> None:
        """Initialize drainage with all stream types."""
        self.streams: dict[StreamType, Stream] = {
            StreamType.DIRECT: Stream(StreamType.DIRECT),
            StreamType.FETCH_ANALYZE: Stream(StreamType.FETCH_ANALYZE, flow_rate=1),
            StreamType.TRANSCRIBE_ANALYZE: Stream(StreamType.TRANSCRIBE_ANALYZE, flow_rate=1),
            StreamType.EXTRACT_ANALYZE: Stream(StreamType.EXTRACT_ANALYZE, flow_rate=1),
            StreamType.OCR_ANALYZE: Stream(StreamType.OCR_ANALYZE, flow_rate=1),
            StreamType.BATCH_DIGEST: Stream(StreamType.BATCH_DIGEST, flow_rate=50),
        }
        self._flowing = False
        self._flow_task: Optional[asyncio.Task] = None

    async def collect(
        self,
        rain_type: RainType,
        content: str,
        source: str = "",
        source_id: str = "",
        creator_id: str = "",
        raw_bytes: Optional[bytes] = None,
        metadata: Optional[dict] = None,
    ) -> RainDrop:
        """Collect a drop of information rain.

        All inputs MUST go through this method. No exceptions.

        Args:
            rain_type: Type of information
            content: Text content or description
            source: Origin (feishu, clipboard, manual)
            source_id: Original ID (message_id, file_key)
            creator_id: Who created it (open_id, user_id)
            raw_bytes: Binary content if applicable
            metadata: Additional context

        Returns:
            RainDrop with assigned stream
        """
        # Determine which stream this drop belongs to
        stream_type = self._route_to_stream(rain_type, content)

        drop = RainDrop(
            id="",
            rain_type=rain_type,
            content=content,
            raw_bytes=raw_bytes,
            source=source,
            source_id=source_id,
            creator_id=creator_id,
            stream_type=stream_type,
            metadata=metadata or {},
        )

        # Add to appropriate stream
        stream = self.streams[stream_type]
        stream.add_drop(drop)

        logger.info(
            "[Drainage] Collected %s drop %s → Stream:%s",
            rain_type.name,
            drop.id[:12],
            stream_type.name,
        )

        # Immediate flow for single-drop streams
        if stream_type in (
            StreamType.FETCH_ANALYZE,
            StreamType.TRANSCRIBE_ANALYZE,
            StreamType.EXTRACT_ANALYZE,
            StreamType.OCR_ANALYZE,
        ):
            await self._flow_single(drop)

        return drop

    def _route_to_stream(self, rain_type: RainType, content: str) -> StreamType:
        """Determine which stream a drop belongs to."""
        routing = {
            RainType.CHAT: StreamType.DIRECT,
            RainType.URL: StreamType.FETCH_ANALYZE,
            RainType.DOCUMENT: StreamType.EXTRACT_ANALYZE,
            RainType.VOICE: StreamType.TRANSCRIBE_ANALYZE,
            RainType.IMAGE: StreamType.OCR_ANALYZE,
            RainType.CLIPBOARD: StreamType.BATCH_DIGEST,
            RainType.SESSION: StreamType.DIRECT,
        }

        # Special case: URL with analysis keywords
        if rain_type == RainType.URL:
            analysis_keywords = [
                "analyze", "analysis", "think", "拆解", "分析",
                "research", "评估", "review", "triz", "mckinsey", "gstack",
            ]
            content_lower = content.lower()
            if any(kw in content_lower for kw in analysis_keywords):
                return StreamType.FETCH_ANALYZE
            else:
                # Simple URL save - still goes to fetch but marked differently
                return StreamType.FETCH_ANALYZE

        return routing.get(rain_type, StreamType.DIRECT)

    async def _flow_single(self, drop: RainDrop) -> None:
        """Flow a single drop immediately to processing."""
        # This will be connected to the reservoir
        logger.info("[Drainage] Flowing drop %s to reservoir", drop.id[:12])

    async def start_flow(self) -> None:
        """Start the continuous flow system."""
        self._flowing = True
        self._flow_task = asyncio.create_task(self._flow_loop())
        logger.info("[Drainage] Flow system started")

    async def stop_flow(self) -> None:
        """Stop the flow system and flush remaining drops."""
        self._flowing = False
        if self._flow_task:
            self._flow_task.cancel()
            try:
                await self._flow_task
            except asyncio.CancelledError:
                pass

        # Flush all remaining streams
        for stream in self.streams.values():
            if stream.drops:
                await self._flow_stream(stream)

        logger.info("[Drainage] Flow system stopped")

    async def _flow_loop(self) -> None:
        """Continuously check streams and flow when full."""
        while self._flowing:
            for stream in self.streams.values():
                if stream.is_full():
                    await self._flow_stream(stream)
            await asyncio.sleep(1)  # Check every second

    async def _flow_stream(self, stream: Stream) -> None:
        """Flow a full stream to the reservoir."""
        drops = stream.flush()
        if drops:
            logger.info(
                "[Drainage] Flowing %d drops from %s to reservoir",
                len(drops),
                stream.stream_type.name,
            )
            # This will be connected to reservoir.ingest()

    def get_stats(self) -> dict[str, Any]:
        """Get drainage system statistics."""
        return {
            "streams": {
                st.name: len(s.drops)
                for st, s in self.streams.items()
            },
            "total_collected": sum(
                len(s.drops) for s in self.streams.values()
            ),
            "flowing": self._flowing,
        }
