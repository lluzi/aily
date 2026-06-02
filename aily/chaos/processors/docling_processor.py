"""Unified document processor using Docling."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import ConversionResult
from docling.document_converter import DocumentConverter, PdfFormatOption

from aily.chaos.processors.base import ContentProcessor
from aily.chaos.types import ExtractedContentMultimodal, VisualElement

logger = logging.getLogger(__name__)


class DoclingProcessor(ContentProcessor):
    """Process documents (PDF, DOCX, PPTX, images) using Docling."""

    def __init__(self, config, llm_client=None):
        super().__init__(config, llm_client)
        self._converter: DocumentConverter | None = None

    def _get_converter(self) -> DocumentConverter:
        if self._converter is None:
            self._converter = DocumentConverter()
        return self._converter

    async def process(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Process a document using Docling."""
        logger.info("[Docling] Processing %s", file_path.name)

        try:
            converter = self._get_converter()
            result = await asyncio.to_thread(converter.convert, str(file_path))

            if result.status.value != "success":
                logger.warning("[Docling] Conversion failed for %s: %s", file_path.name, result.status)
                return None

            # Export to markdown
            markdown = result.document.export_to_markdown()

            # Extract title from markdown or filename
            title = self._extract_title(markdown, file_path)

            # Extract visual elements from pictures/tables
            visual_elements = []
            if getattr(self.config, "pdf", None) and self.config.pdf.visual_analysis:
                visual_elements = await self._extract_visual_elements(result, file_path)

            source_type = self._source_type_for_extension(file_path.suffix.lower())

            return ExtractedContentMultimodal(
                text=markdown,
                title=title,
                source_type=source_type,
                source_path=file_path,
                visual_elements=visual_elements,
                processing_method="docling",
                metadata={
                    "pages": len(result.pages) if hasattr(result, "pages") else 1,
                    "docling_status": result.status.value,
                },
            )

        except Exception as e:
            logger.exception("[Docling] Failed to process %s: %s", file_path.name, e)
            return None

    def _extract_title(self, markdown: str, file_path: Path) -> str:
        """Extract title from first markdown heading or filename."""
        lines = markdown.split("\n")
        for line in lines[:20]:
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
            if line.startswith("## "):
                return line[3:].strip()
        return file_path.stem.replace("_", " ").replace("-", " ").title()

    def _source_type_for_extension(self, ext: str) -> str:
        mapping = {
            ".pdf": "pdf",
            ".docx": "docx",
            ".pptx": "presentation",
            ".xlsx": "spreadsheet",
            ".html": "html",
            ".png": "image",
            ".jpg": "image",
            ".jpeg": "image",
            ".gif": "image",
            ".webp": "image",
            ".bmp": "image",
            ".tiff": "image",
        }
        return mapping.get(ext, "document")

    async def _extract_visual_elements(
        self, result: ConversionResult, file_path: Path
    ) -> list[VisualElement]:
        """Extract pictures and tables as visual elements."""
        visual_elements: list[VisualElement] = []

        try:
            # Get all pictures from the document
            pictures = result.document.pictures if hasattr(result.document, "pictures") else []
            for i, picture in enumerate(pictures[:20]):
                try:
                    # Try to get image data
                    if hasattr(picture, "get_image"):
                        img = picture.get_image(result.document)
                        if img:
                            buffer = io.BytesIO()
                            img.save(buffer, format="PNG")
                            base64_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                            visual_elements.append(
                                VisualElement(
                                    element_id=f"pic_{i}",
                                    element_type="image",
                                    description=getattr(picture, "caption", f"Picture {i+1}") or f"Picture {i+1}",
                                    source_page=getattr(picture, "page_no", None),
                                    base64_data=base64_data[:1000] + "...",
                                )
                            )
                except Exception as e:
                    logger.debug("[Docling] Failed to extract picture %d: %s", i, e)

            # Get tables
            tables = result.document.tables if hasattr(result.document, "tables") else []
            for i, table in enumerate(tables[:10]):
                try:
                    caption = getattr(table, "caption", f"Table {i+1}") or f"Table {i+1}"
                    visual_elements.append(
                        VisualElement(
                            element_id=f"table_{i}",
                            element_type="table",
                            description=caption,
                            source_page=getattr(table, "page_no", None),
                        )
                    )
                except Exception as e:
                    logger.debug("[Docling] Failed to extract table %d: %s", i, e)

        except Exception as e:
            logger.warning("[Docling] Visual element extraction failed: %s", e)

        return visual_elements

    def can_process(self, file_path: Path) -> bool:
        """Check if Docling can handle this file type."""
        ext = file_path.suffix.lower()
        supported = {
            ".pdf", ".docx", ".pptx", ".xlsx", ".html",
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff",
        }
        return ext in supported
