"""Core types for multimodal content processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any


class ProcessingError(Enum):
    """Types of processing errors."""

    UNSUPPORTED_TYPE = "unsupported_type"
    FILE_TOO_LARGE = "file_too_large"
    CORRUPT_FILE = "corrupt_file"
    OCR_FAILED = "ocr_failed"
    TRANSCRIPTION_FAILED = "transcription_failed"
    LLM_TIMEOUT = "llm_timeout"
    LLM_RATE_LIMIT = "llm_rate_limit"
    DIKIWI_FAILED = "dikiwi_failed"
    UNKNOWN = "unknown"


class ProcessingStatus(Enum):
    """Processing job status."""

    PENDING = auto()
    PROCESSING = auto()
    COMPLETED = auto()
    FAILED = auto()
    RETRYING = auto()


@dataclass
class VisualElement:
    """A visual element extracted from content (image, chart, diagram)."""

    element_id: str
    element_type: str  # "image", "chart", "diagram", "table", "figure"
    description: str
    source_page: int | None = None  # For documents
    timestamp: float | None = None  # For video (seconds)
    base64_data: str | None = None  # Thumbnail or full image
    asset_path: str | None = None  # Relative or absolute extracted asset path
    ocr_text: str | None = None  # Text extracted via OCR
    llm_analysis: str | None = None  # GPT-4V description

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "element_id": self.element_id,
            "element_type": self.element_type,
            "description": self.description,
            "source_page": self.source_page,
            "timestamp": self.timestamp,
            "asset_path": self.asset_path,
            "ocr_text": self.ocr_text,
            "llm_analysis": self.llm_analysis,
            # Skip base64_data in dict (too large)
            "has_image": self.base64_data is not None,
        }


@dataclass
class TimestampedSegment:
    """A timestamped segment for audio/video content."""

    start_time: float  # seconds
    end_time: float  # seconds
    text: str
    summary: str | None = None
    speaker: str | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
            "summary": self.summary,
            "speaker": self.speaker,
            "confidence": self.confidence,
        }


@dataclass
class ExtractedContentMultimodal:
    """Enhanced extracted content with multimodal support."""

    text: str
    title: str | None = None
    source_type: str = "unknown"
    source_path: Path | None = None

    # Core metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Multimodal elements
    visual_elements: list[VisualElement] = field(default_factory=list)
    transcript: str | None = None
    segments: list[TimestampedSegment] = field(default_factory=list)

    # Auto-generated tags
    tags: list[str] = field(default_factory=list)

    # Processing info
    processing_timestamp: datetime = field(default_factory=datetime.utcnow)
    processing_method: str = "unknown"
    extraction_confidence: float = 1.0

    def __post_init__(self):
        """Ensure defaults are set."""
        if self.metadata is None:
            self.metadata = {}
        if self.visual_elements is None:
            self.visual_elements = []
        if self.segments is None:
            self.segments = []
        if self.tags is None:
            self.tags = []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "text": self.text,
            "title": self.title,
            "source_type": self.source_type,
            "source_path": str(self.source_path) if self.source_path else None,
            "metadata": self.metadata,
            "visual_elements": [e.to_dict() for e in self.visual_elements],
            "transcript": self.transcript,
            "segments": [s.to_dict() for s in self.segments],
            "tags": self.tags,
            "processing_timestamp": self.processing_timestamp.isoformat(),
            "processing_method": self.processing_method,
            "extraction_confidence": self.extraction_confidence,
        }

    def get_full_text(self) -> str:
        """Get combined text from all sources."""
        parts = [self.text]

        if self.transcript:
            parts.append("\n\n## Transcript\n")
            parts.append(self.transcript)

        if self.visual_elements:
            parts.append("\n\n## Visual Elements\n")
            for elem in self.visual_elements:
                parts.append(f"\n### {elem.element_type}: {elem.description}")
                if elem.llm_analysis:
                    parts.append(f"\nAnalysis: {elem.llm_analysis}")
                if elem.ocr_text:
                    parts.append(f"\nOCR: {elem.ocr_text}")

        return "\n".join(parts)


@dataclass
class ProcessingJob:
    """A file processing job."""

    job_id: str
    file_path: Path
    status: ProcessingStatus = ProcessingStatus.PENDING
    priority: int = 5  # 1-10, lower is higher priority

    # Content info (detected)
    mime_type: str | None = None
    file_size: int = 0

    # Processing results
    extracted: ExtractedContentMultimodal | None = None
    error: ProcessingError | None = None
    error_message: str | None = None

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Retry logic
    retry_count: int = 0
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "job_id": self.job_id,
            "file_path": str(self.file_path),
            "status": self.status.name,
            "priority": self.priority,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "extracted": self.extracted.to_dict() if self.extracted else None,
            "error": self.error.value if self.error else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retry_count": self.retry_count,
        }
