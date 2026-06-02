"""Content type detection for universal processing.

Detects content type from magic bytes, extensions, or HTTP headers.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass
class ContentType:
    mime_type: str
    extension: str | None = None
    confidence: float = 1.0  # 0-1, how sure we are


class ContentTypeDetector:
    """Detect content type from various sources."""

    # Magic bytes signatures for common formats
    MAGIC_BYTES = {
        b"%PDF": "application/pdf",
        b"\x89PNG": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"PK\x03\x04": "application/zip",  # docx, xlsx, etc are zip-based
        b"\x1f\x8b": "application/gzip",
        b"RIFF": "audio/wav",  # Could be other RIFF formats too
    }

    # Map common extensions to processors
    EXTENSION_MAP = {
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".html": "text/html",
        ".htm": "text/html",
    }

    @classmethod
    def detect_from_bytes(cls, data: bytes, filename: str | None = None) -> ContentType:
        """Detect content type from file bytes.

        Args:
            data: First N bytes of file (usually 4KB is enough)
            filename: Optional filename for extension-based detection

        Returns:
            ContentType with mime_type and confidence
        """
        # 1. Check magic bytes (most reliable)
        for magic, mime_type in cls.MAGIC_BYTES.items():
            if data.startswith(magic):
                return ContentType(
                    mime_type=mime_type,
                    extension=cls._get_extension(filename) if filename else None,
                    confidence=0.95,
                )

        # 2. Check filename extension
        if filename:
            ext = Path(filename).suffix.lower()
            if ext in cls.EXTENSION_MAP:
                return ContentType(
                    mime_type=cls.EXTENSION_MAP[ext],
                    extension=ext,
                    confidence=0.8,
                )

        # 3. Try mimetypes library
        if filename:
            guessed, _ = mimetypes.guess_type(filename)
            if guessed:
                return ContentType(
                    mime_type=guessed,
                    extension=cls._get_extension(filename),
                    confidence=0.6,
                )

        # 4. Content sniffing for text vs binary
        if cls._is_text(data):
            # Try to detect if it's HTML or Markdown
            text = data[:2048].decode("utf-8", errors="ignore").lower()
            if text.strip().startswith(("<!doctype html", "<html")):
                return ContentType("text/html", confidence=0.7)
            if text.strip().startswith(("#", "-", "|", "[")):
                # Could be markdown - low confidence
                return ContentType("text/markdown", confidence=0.4)
            return ContentType("text/plain", confidence=0.5)

        return ContentType("application/octet-stream", confidence=0.1)

    @classmethod
    def detect_from_http_headers(cls, content_type_header: str | None) -> ContentType | None:
        """Detect from HTTP Content-Type header."""
        if not content_type_header:
            return None

        # Strip charset, etc: "text/html; charset=utf-8" -> "text/html"
        mime_type = content_type_header.split(";")[0].strip().lower()

        # Normalize common variants
        normalizations = {
            "application/x-pdf": "application/pdf",
            "image/jpg": "image/jpeg",
            "text/x-markdown": "text/markdown",
        }
        mime_type = normalizations.get(mime_type, mime_type)

        return ContentType(mime_type=mime_type, confidence=0.9)

    @classmethod
    def detect(
        cls,
        data: bytes | None = None,
        filename: str | None = None,
        http_content_type: str | None = None,
    ) -> ContentType:
        """Best-effort content type detection.

        Priority:
        1. HTTP Content-Type (if provided by server)
        2. Magic bytes (file signature)
        3. Filename extension
        4. Content sniffing

        Args:
            data: File content (first few KB)
            filename: Original filename
            http_content_type: HTTP Content-Type header

        Returns:
            ContentType with confidence score
        """
        # HTTP header is high confidence if present
        if http_content_type:
            detected = cls.detect_from_http_headers(http_content_type)
            if detected and detected.confidence > 0.8:
                return detected

        # If we have file data, use magic bytes
        if data:
            return cls.detect_from_bytes(data, filename)

        # Fallback to extension only
        if filename:
            ext = Path(filename).suffix.lower()
            if ext in cls.EXTENSION_MAP:
                return ContentType(
                    mime_type=cls.EXTENSION_MAP[ext],
                    extension=ext,
                    confidence=0.5,  # Lower without magic bytes
                )

        return ContentType("application/octet-stream", confidence=0.0)

    @staticmethod
    def _get_extension(filename: str | None) -> str | None:
        """Extract extension from filename."""
        if not filename:
            return None
        ext = Path(filename).suffix.lower()
        return ext if ext else None

    @staticmethod
    def _is_text(data: bytes, sample_size: int = 2048) -> bool:
        """Heuristic: is this text or binary data?"""
        sample = data[:sample_size]

        # Check for null bytes (unlikely in text)
        if b"\x00" in sample:
            return False

        # Check for high ratio of printable characters
        try:
            text = sample.decode("utf-8")
            printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
            return printable / len(text) > 0.9 if text else True
        except UnicodeDecodeError:
            return False
