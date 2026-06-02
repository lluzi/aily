"""PDF processor using Docling with local extraction fallbacks."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from pathlib import Path

import aiohttp
from pdf2image import convert_from_path

from aily.chaos.processors.base import ContentProcessor
from aily.chaos.types import ExtractedContentMultimodal, VisualElement

logger = logging.getLogger(__name__)


class PDFProcessor(ContentProcessor):
    """Process PDF files using Docling primary, with local extraction fallbacks."""

    async def process(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Process PDF file."""
        logger.info("Processing PDF: %s", file_path.name)

        try:
            mineru_result = await self._call_mineru(file_path)
            if mineru_result:
                return mineru_result

            # Method 1: Docling (best quality, handles layout/tables/images)
            docling_result = await self._call_docling(file_path)
            if docling_result and not self._is_docling_poor_quality(docling_result):
                return docling_result

            if docling_result:
                logger.warning(
                    "Docling result for %s was poor quality (%d words, %.2f words/line); using fallback",
                    file_path.name,
                    len(docling_result.text.split()),
                    self._avg_words_per_line(docling_result.text),
                )

            # Method 2: Try the placeholder OCR hook, which currently falls back locally.
            ocr_result = await self._call_glm_ocr(file_path)

            if ocr_result and ocr_result.get("text"):
                text = ocr_result["text"]
                metadata = {
                    "pages": ocr_result.get("pages", 1),
                    "method": "ocr-fallback",
                }
            else:
                # Fallback: basic extraction
                text = await self._fallback_extract(file_path)
                metadata = {"method": "fallback"}

            # Extract visual elements if enabled
            visual_elements = []
            if self.config.pdf.visual_analysis:
                visual_elements = await self._extract_visual_elements(file_path)

            return ExtractedContentMultimodal(
                text=text,
                title=file_path.stem.replace("_", " ").replace("-", " ").title(),
                source_type="pdf",
                source_path=file_path,
                visual_elements=visual_elements,
                processing_method="ocr-fallback" if ocr_result else "pdfminer",
                metadata=metadata,
            )

        except Exception as e:
            logger.exception("Failed to process PDF: %s", e)
            return None

    def _avg_words_per_line(self, text: str) -> float:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return 0.0
        return sum(len(line.split()) for line in lines) / len(lines)

    def _is_docling_poor_quality(self, result: ExtractedContentMultimodal) -> bool:
        """Check if Docling output is too short or too fragmented to be useful."""
        text = result.text or ""
        word_count = len(text.split())
        if word_count < 100:
            return True
        avg_wpl = self._avg_words_per_line(text)
        if avg_wpl < 1.5:
            return True
        return False

    async def _call_docling(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Primary extraction using Docling."""
        try:
            from aily.chaos.processors.docling_processor import DoclingProcessor

            processor = DoclingProcessor(self.config, self.llm_client)
            result = await processor.process(file_path)
            if result:
                logger.info("Docling succeeded for %s", file_path.name)
            return result
        except Exception as e:
            logger.warning("Docling extraction failed for %s: %s", file_path.name, e)
            return None

    async def _call_mineru(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Primary extraction using local MinerU when available."""
        try:
            from aily.chaos.processors.mineru_processor import MinerUProcessor

            processor = MinerUProcessor(self.config, self.llm_client)
            result = await processor.process(file_path)
            if result:
                logger.info("MinerU succeeded for %s", file_path.name)
            return result
        except Exception as e:
            logger.warning("MinerU extraction failed for %s: %s", file_path.name, e)
            return None

    async def _call_glm_ocr(self, file_path: Path) -> dict | None:
        """Placeholder OCR hook; currently falls back to local extraction."""
        api_key = os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY") or os.getenv("LLM_API_KEY")
        if not api_key:
            logger.warning("No Kimi API key for PDF OCR fallback, using local extraction")
            return None

        try:
            # Read file and encode to base64
            file_content = await asyncio.to_thread(file_path.read_bytes)
            base64_content = base64.b64encode(file_content).decode("utf-8")

            # Determine MIME type
            mime_type = "application/pdf"

            # There is no dedicated Kimi OCR endpoint wired here yet, so keep the
            # local extraction fallback instead of calling an obsolete BigModel API.
            return None

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.OCR_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as response:
                    if response.status != 200:
                        logger.warning("OCR API error: %s", response.status)
                        return None

                    result = await response.json()

                    # Parse response
                    if "text" in result:
                        return {
                            "text": result["text"],
                            "pages": result.get("page_count", 1),
                            "layout": result.get("layout_details", []),
                        }
                    return None

        except Exception as e:
            logger.warning("PDF OCR fallback failed: %s", e)
            return None

    async def _fallback_extract(self, file_path: Path) -> str:
        """Fallback PDF extraction using pdfplumber."""
        try:
            import pdfplumber

            text_parts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

            return "\n\n".join(text_parts) if text_parts else ""
        except Exception as e:
            logger.warning("Fallback extraction failed: %s", e)
            return f"[PDF extraction failed for {file_path.name}]"

    async def _extract_visual_elements(self, file_path: Path) -> list[VisualElement]:
        """Extract visual elements from PDF pages."""
        visual_elements = []

        try:
            # Convert PDF pages to images
            images = await asyncio.to_thread(
                convert_from_path,
                str(file_path),
                first_page=1,
                last_page=min(10, self.config.pdf.max_pages_for_visual_analysis),
                dpi=150,
            )

            for i, image in enumerate(images):
                try:
                    # Convert to base64
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    base64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")

                    # Create visual element
                    element = VisualElement(
                        element_id=f"page_{i+1}",
                        element_type="page",
                        description=f"Page {i+1}",
                        source_page=i + 1,
                        base64_data=base64_image[:1000] + "...",  # Truncate for storage
                    )
                    visual_elements.append(element)

                except Exception as e:
                    logger.warning("Failed to process page %d: %s", i + 1, e)

        except Exception as e:
            logger.warning("Visual element extraction failed: %s", e)

        return visual_elements

    def can_process(self, file_path: Path) -> bool:
        """Check if file is PDF."""
        return file_path.suffix.lower() == ".pdf"
