"""Text and document processors."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path

from aily.browser.manager import BrowserUseManager
from aily.chaos.processors.base import ContentProcessor
from aily.chaos.types import ExtractedContentMultimodal, VisualElement
from aily.processing.markdownize import MarkdownizeProcessor

logger = logging.getLogger(__name__)


class TextProcessor(ContentProcessor):
    """Processor for text and markdown files."""

    URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]{}\"']+")

    def __init__(
        self,
        config,
        llm_client=None,
        browser_manager: BrowserUseManager | None = None,
    ) -> None:
        super().__init__(config, llm_client)
        self.browser_manager = browser_manager

    def _get_browser_manager(self) -> BrowserUseManager:
        if self.browser_manager is None:
            self.browser_manager = BrowserUseManager()
        return self.browser_manager

    async def process(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Process text/markdown file."""
        try:
            text = await asyncio.to_thread(self._read_file, file_path)
            if self._looks_like_mineru_markdown(file_path):
                return self._process_mineru_markdown(file_path, text)

            if file_path.suffix.lower() in {".md", ".markdown"}:
                try:
                    url_result = await self._maybe_fetch_urls_to_markdown(file_path, text)
                    if url_result is not None:
                        return url_result
                except Exception as exc:
                    logger.warning(
                        "URL markdown fetch failed for %s, falling back to plain markdown: %s",
                        file_path.name,
                        exc,
                    )

            # Extract title from first heading
            title = self._extract_title(text, file_path)

            return ExtractedContentMultimodal(
                text=text,
                title=title,
                source_type="text",
                source_path=file_path,
                processing_method="text_reader",
            )
        except Exception as e:
            logger.exception("Failed to process text file: %s", e)
            return None

    def _looks_like_mineru_markdown(self, file_path: Path) -> bool:
        """Detect MinerU markdown exports by filename or sibling artifacts."""
        if file_path.suffix.lower() not in {".md", ".markdown"}:
            return False
        if file_path.name.lower() == "full.md":
            return True

        sibling_names = {path.name.lower() for path in file_path.parent.iterdir() if path.is_file()}
        mineru_sidecars = {"layout.json", "middle.json", "content_list.json", "model.json", "main.html"}
        return bool(sibling_names.intersection(mineru_sidecars)) or any(
            name.endswith("_content_list.json") or name.endswith("_model.json")
            for name in sibling_names
        )

    def _process_mineru_markdown(
        self,
        file_path: Path,
        text: str,
    ) -> ExtractedContentMultimodal:
        """Normalize MinerU export markdown into a Chaos content item."""
        title = self._extract_title(text, file_path)
        sidecar = self._load_mineru_sidecar_metadata(file_path)
        source_root = self._derive_mineru_source_root(file_path)

        if title == source_root.replace("_", " ").replace("-", " ").title():
            title = self._derive_mineru_title(text, source_root)
        else:
            title = self._derive_mineru_title(text, title)

        metadata = {
            "mineru_output_dir": str(file_path.parent),
            "mineru_source_root": source_root,
            "mineru_sidecar_files": sorted(sidecar["artifact_files"]),
            "chaos_base_name": source_root,
        }
        if sidecar["page_count"] is not None:
            metadata["pages"] = sidecar["page_count"]
        if sidecar["content_items"] is not None:
            metadata["mineru_content_items"] = sidecar["content_items"]

        return ExtractedContentMultimodal(
            text=text,
            title=title,
            source_type="mineru_markdown",
            source_path=file_path,
            processing_method="mineru_import",
            metadata=metadata,
            visual_elements=sidecar["visual_elements"],
            tags=["mineru", "markdown-import"],
        )

    def _load_mineru_sidecar_metadata(self, file_path: Path) -> dict[str, object]:
        """Collect page and artifact hints from common MinerU sidecars."""
        artifact_files: set[str] = set()
        page_count: int | None = None
        content_items: int | None = None
        visual_elements: list[VisualElement] = []

        for sibling in file_path.parent.iterdir():
            if not sibling.is_file() or sibling == file_path:
                continue

            name = sibling.name.lower()
            if name in {"layout.json", "middle.json", "content_list.json", "model.json", "main.html"} or (
                name.endswith("_content_list.json") or name.endswith("_model.json")
            ):
                artifact_files.add(sibling.name)

            if sibling.suffix.lower() != ".json":
                continue

            try:
                data = json.loads(sibling.read_text(encoding="utf-8"))
            except Exception:
                continue

            if page_count is None:
                page_count = self._extract_page_count(data)
            if content_items is None and isinstance(data, list):
                content_items = len(data)
            if not visual_elements and "content_list" in name and isinstance(data, list):
                visual_elements = self._extract_mineru_visual_elements(data)

        return {
            "artifact_files": artifact_files,
            "page_count": page_count,
            "content_items": content_items,
            "visual_elements": visual_elements,
        }

    def _extract_page_count(self, data) -> int | None:
        """Search nested MinerU JSON for a page count."""
        if isinstance(data, dict):
            for key in ("page_count", "page_cnt", "pages", "num_pages"):
                value = data.get(key)
                if isinstance(value, int):
                    return value
            for value in data.values():
                page_count = self._extract_page_count(value)
                if page_count is not None:
                    return page_count
        elif isinstance(data, list):
            page_indexes = [
                item.get("page_idx")
                for item in data
                if isinstance(item, dict) and isinstance(item.get("page_idx"), int)
            ]
            if page_indexes:
                return max(page_indexes) + 1
            for value in data:
                page_count = self._extract_page_count(value)
                if page_count is not None:
                    return page_count
        return None

    def _derive_mineru_source_root(self, file_path: Path) -> str:
        """Use the export directory name as the stable base for Chaos note naming."""
        if file_path.name.lower() == "full.md":
            return file_path.parent.name
        return file_path.stem

    def _derive_mineru_title(self, text: str, fallback: str) -> str:
        """Prefer the first H1, otherwise derive a human title from the export folder."""
        heading = self._extract_title(text, Path(fallback))
        if heading and heading != Path(fallback).stem.replace("_", " ").replace("-", " ").title():
            return heading
        return fallback.replace("_", " ").replace("-", " ").strip().title()

    def _extract_mineru_visual_elements(self, items: list[dict]) -> list[VisualElement]:
        """Convert key MinerU content blocks into Chaos visual elements."""
        visual_elements: list[VisualElement] = []

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).lower()
            if item_type not in {"image", "table", "chart"}:
                continue

            description_parts: list[str] = []
            for key in (
                "image_caption",
                "table_caption",
                "chart_caption",
                "image_footnote",
                "table_footnote",
                "chart_footnote",
                "content",
            ):
                value = item.get(key)
                description_parts.extend(self._flatten_mineru_text(value))

            content = item.get("content")
            if isinstance(content, dict):
                for key in (
                    "image_caption",
                    "table_caption",
                    "chart_caption",
                    "image_footnote",
                    "table_footnote",
                    "chart_footnote",
                    "html",
                    "text",
                ):
                    description_parts.extend(self._flatten_mineru_text(content.get(key)))

            description = " ".join(description_parts).strip() or item_type.title()
            asset_path = self._extract_mineru_asset_path(item)
            visual_elements.append(
                VisualElement(
                    element_id=f"mineru_{item_type}_{idx}",
                    element_type=item_type,
                    description=description[:500],
                    source_page=item.get("page_idx") + 1 if isinstance(item.get("page_idx"), int) else None,
                    asset_path=asset_path,
                )
            )
            if len(visual_elements) >= 60:
                break

        return visual_elements

    def _extract_mineru_asset_path(self, item: dict) -> str | None:
        """Extract the relative asset path from old or v2 MinerU content-list items."""
        img_path = item.get("img_path")
        if isinstance(img_path, str) and img_path.strip():
            return img_path.strip()

        content = item.get("content")
        if isinstance(content, dict):
            image_source = content.get("image_source")
            if isinstance(image_source, dict):
                path = image_source.get("path")
                if isinstance(path, str) and path.strip():
                    return path.strip()

        return None

    def _flatten_mineru_text(self, value: object) -> list[str]:
        """Normalize MinerU caption/footnote fields into plain text fragments."""
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        if isinstance(value, dict):
            flattened: list[str] = []
            for key in ("content", "text", "html"):
                flattened.extend(self._flatten_mineru_text(value.get(key)))
            return flattened
        if isinstance(value, list):
            flattened: list[str] = []
            for item in value:
                flattened.extend(self._flatten_mineru_text(item))
            return flattened
        return []

    async def _maybe_fetch_urls_to_markdown(
        self,
        file_path: Path,
        text: str,
    ) -> ExtractedContentMultimodal | None:
        """If markdown contains URLs, fetch them and return browser-backed markdown."""
        urls = self._extract_urls(text)
        if not urls:
            return None

        logger.info("Markdown file %s contains %s URL(s); fetching content", file_path.name, len(urls))
        markdownizer = MarkdownizeProcessor(self._get_browser_manager())

        fetched_urls: list[str] = []
        failed_urls: list[str] = []
        fetched_items: list[dict[str, str]] = []

        for url in urls:
            try:
                result = await self._fetch_url_as_markdown(markdownizer, url)
                fetched_urls.append(url)
                item_markdown = getattr(result, "markdown", "").strip()
                item_title = getattr(result, "title", None) or self._extract_title(item_markdown, file_path)
                fetched_items.append(
                    {
                        "url": url,
                        "title": item_title,
                        "markdown": item_markdown,
                    }
                )
            except Exception as exc:
                logger.warning("Browser fetch failed for %s: %s", url, exc)
                failed_urls.append(url)

        if not fetched_items:
            return None

        combined_markdown = self._combine_original_and_fetched_markdown(
            file_path=file_path,
            original_text=text,
            fetched_sections=[item["markdown"] for item in fetched_items],
        )
        title = self._extract_title(combined_markdown, file_path)

        tags = ["url-import", "markdown"]
        metadata = {
            "source_urls": fetched_urls,
            "failed_source_urls": failed_urls,
            "source_file_name": file_path.name,
            "fetched_url_count": len(fetched_urls),
            "url_import_items": fetched_items,
        }

        return ExtractedContentMultimodal(
            text=combined_markdown,
            title=title,
            source_type="url_markdown",
            source_path=file_path,
            processing_method="browser_url_markdown_fetch",
            tags=tags,
            metadata=metadata,
        )

    async def _fetch_url_as_markdown(
        self,
        markdownizer: MarkdownizeProcessor,
        url: str,
    ):
        """Fetch URL content with the best available strategy for Chaos imports."""
        # Monica share links require browser rendering; let markdownizer route them.
        return await markdownizer.process_url(url, use_browser=True)

    def _read_file(self, file_path: Path) -> str:
        """Read file contents."""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def _extract_title(self, text: str, file_path: Path) -> str:
        """Extract title from first markdown heading or filename."""
        lines = text.split("\n")
        for line in lines[:10]:  # Check first 10 lines
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
            if line.startswith("## "):
                return line[3:].strip()
        # Fallback to filename
        return file_path.stem.replace("_", " ").replace("-", " ").title()

    def _extract_urls(self, text: str) -> list[str]:
        """Extract unique URLs from markdown content in source order."""
        seen: set[str] = set()
        urls: list[str] = []
        for match in self.URL_PATTERN.findall(text):
            url = match.rstrip(".,);!?]>")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _combine_original_and_fetched_markdown(
        self,
        *,
        file_path: Path,
        original_text: str,
        fetched_sections: list[str],
    ) -> str:
        """Blend original markdown note with fetched URL content."""
        original = original_text.strip()
        parts: list[str] = []

        if original and len(original) > 20:
            parts.append(f"# URL Import: {file_path.stem.replace('_', ' ').replace('-', ' ').title()}")
            parts.append("")
            parts.append("## Original Note")
            parts.append("")
            parts.append(original)

        if fetched_sections:
            if parts:
                parts.extend(["", "## Fetched Content", ""])
            parts.append("\n\n---\n\n".join(section for section in fetched_sections if section))

        return "\n".join(part for part in parts if part is not None).strip()

    def split_url_import_items(
        self,
        content: ExtractedContentMultimodal,
    ) -> list[ExtractedContentMultimodal]:
        """Split a multi-URL markdown import into one extracted item per fetched URL."""
        items = content.metadata.get("url_import_items", [])
        if not items:
            return [content]

        split_contents: list[ExtractedContentMultimodal] = []
        for item in items:
            url = item.get("url", "")
            title = item.get("title") or content.title
            markdown = item.get("markdown", "").strip()
            if not markdown:
                continue

            source_path = content.source_path
            item_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10] if url else ""
            split_contents.append(
                ExtractedContentMultimodal(
                    text=markdown,
                    title=title,
                    source_type="url_markdown_item",
                    source_path=source_path,
                    processing_method="browser_url_markdown_fetch",
                    tags=list(dict.fromkeys([*content.tags, "url-import-item"])),
                    metadata={
                        **content.metadata,
                        "source_url": url,
                        "source_urls": [url] if url else [],
                        "url_import_item_id": item_hash,
                    },
                )
            )

        return split_contents or [content]

    def can_process(self, file_path: Path) -> bool:
        """Check if file is text."""
        ext = file_path.suffix.lower()
        return ext in {".txt", ".md", ".markdown", ".rst", ".text"}


class GenericDocumentProcessor(ContentProcessor):
    """Fallback processor for unknown file types."""

    async def process(self, file_path: Path) -> ExtractedContentMultimodal | None:
        """Process generic file - just captures metadata."""
        try:
            stat = file_path.stat()
            text = f"""File: {file_path.name}
Type: {file_path.suffix}
Size: {stat.st_size} bytes

This file type is not directly processable. The original file is preserved.
"""
            return ExtractedContentMultimodal(
                text=text,
                title=file_path.stem,
                source_type="generic",
                source_path=file_path,
                processing_method="generic",
                metadata={
                    "file_size": stat.st_size,
                    "extension": file_path.suffix,
                },
            )
        except Exception as e:
            logger.exception("Failed to process generic file: %s", e)
            return None

    def can_process(self, file_path: Path) -> bool:
        """Can process any file."""
        return True
