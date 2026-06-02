from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aily.processing.markdownize import MarkdownizeProcessor
from aily.processing.processors import ExtractedContent
from aily.source_store import SourceStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanonicalMarkdownPackage:
    source_id: str
    package_id: str
    markdown: str
    markdown_sha256: str
    package_path: str
    title: str
    source_type: str
    metadata: dict[str, Any]


class CanonicalMarkdownConverter:
    """Convert extracted source content into Aily's canonical Markdown package."""

    def __init__(
        self,
        *,
        source_store: SourceStore,
        markdownize_processor: MarkdownizeProcessor | None = None,
    ) -> None:
        self.source_store = source_store
        self.markdownize_processor = markdownize_processor or MarkdownizeProcessor()

    async def convert_extracted(
        self,
        *,
        source_id: str,
        extracted: ExtractedContent,
        fallback_title: str = "",
        source_url: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CanonicalMarkdownPackage:
        markdown_content = await self.markdownize_processor.process_content(extracted)
        markdown = self._normalize_markdown(markdown_content.markdown)
        title = markdown_content.title or extracted.title or fallback_title
        merged_metadata = {
            "converter": "local_markdownize",
            "extracted_source_type": extracted.source_type,
            "source_url": source_url,
            **(extracted.metadata or {}),
            **(markdown_content.metadata or {}),
            **(metadata or {}),
        }
        stored = await self.source_store.store_markdown_package(
            source_id=source_id,
            markdown=markdown,
            title=title,
            source_type=markdown_content.source_type or extracted.source_type,
            metadata=merged_metadata,
        )
        logger.info(
            "Canonical Markdown package created for %s: %s",
            source_id,
            stored["package_id"],
        )
        return CanonicalMarkdownPackage(
            source_id=source_id,
            package_id=str(stored["package_id"]),
            markdown=markdown,
            markdown_sha256=str(stored["markdown_sha256"]),
            package_path=str(stored["package_path"]),
            title=title,
            source_type=str(stored["source_type"]),
            metadata=merged_metadata,
        )

    def _normalize_markdown(self, markdown: str) -> str:
        normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("Canonical Markdown conversion produced empty output")
        return normalized
