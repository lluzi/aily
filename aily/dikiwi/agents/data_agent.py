"""DataAgent - Stage 1: DATA extraction and markdownization."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import TYPE_CHECKING

from aily.dikiwi.agents.base import DikiwiAgent
from aily.dikiwi.agents.context import AgentContext
from aily.dikiwi.agents.llm_tools import chat_json
from aily.config import SETTINGS
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.sessions.dikiwi_mind import DikiwiStage, StageResult

if TYPE_CHECKING:
    from aily.sessions.dikiwi_mind import DataPoint

logger = logging.getLogger(__name__)


class DataAgent(DikiwiAgent):
    """Stage 1: Extract concept-level data points from raw content."""

    _LONG_DOC_THRESHOLD = 5000
    _CHUNK_SIZE = 4000
    _MAX_CHUNKS = 8

    async def execute(self, ctx: AgentContext) -> StageResult:
        start = time.time()

        try:
            drop = await self._markdownize_drop(ctx)
            content = drop.content
            visual_data_points = self._build_visual_data_points(ctx)

            # Content quality gate: skip LLM extraction on obviously thin content
            quality_check = self._assess_content_quality(content)
            if quality_check["is_too_thin"]:
                if visual_data_points:
                    logger.info(
                        "[DIKIWI] Text quality low for %s but retaining %d visual datapoints",
                        drop.source,
                        len(visual_data_points),
                    )
                else:
                    logger.warning(
                        "[DIKIWI] Content quality too low for %s: %s",
                        drop.source,
                        quality_check["reason"],
                    )
                    processing_time = (time.time() - start) * 1000
                    return StageResult(
                        stage=DikiwiStage.DATA,
                        success=True,
                        items_processed=1,
                        items_output=0,
                        processing_time_ms=processing_time,
                        data={
                            "data_points": [],
                            "doc_title": "",
                            "doc_summary": "",
                            "data_note_id": "",
                            "quality_assessment": "low",
                            "quality_reason": quality_check["reason"],
                        },
                    )

            # Add input to memory
            if ctx.memory:
                ctx.memory.add_user(f"Input to process:\n{content[:3000]}")

            all_data_points: list[DataPoint] = []
            doc_title = ""
            doc_summary = ""

            if len(content) > self._LONG_DOC_THRESHOLD:
                chunks = self._chunk_content(content, self._CHUNK_SIZE)
                logger.info(
                    "[DIKIWI] Long doc (%d chars) split into %d chunks",
                    len(content),
                    min(len(chunks), self._MAX_CHUNKS),
                )
                existing_concepts: list[str] = []
                for i, chunk in enumerate(chunks[: self._MAX_CHUNKS]):
                    dps, meta = await self._llm_extract_chunk(
                        chunk, drop.source, ctx, chunk_index=i, existing_concepts=existing_concepts
                    )
                    all_data_points.extend(dps)
                    existing_concepts.extend(dp.concept for dp in dps if dp.concept)
                    if i == 0:
                        doc_title = meta.get("title", "")
                        doc_summary = meta.get("summary", "")
            else:
                dps, meta = await self._llm_extract_chunk(
                    content, drop.source, ctx, chunk_index=0
                )
                all_data_points.extend(dps)
                doc_title = meta.get("title", "")
                doc_summary = meta.get("summary", "")

            # Let quality gates filter ideas naturally; do not artificially cap volume
            data_points = all_data_points
            if not data_points:
                data_points = await self._llm_fallback_extraction(content, drop.source, ctx)
            if visual_data_points:
                data_points = self._merge_data_points(data_points, visual_data_points)
            data_points = self._filter_data_points(data_points)

            processing_time = (time.time() - start) * 1000

            if ctx.memory:
                ctx.memory.add_assistant(
                    f"STAGE 1 (DATA) Complete: Extracted {len(data_points)} concepts. "
                    f"Examples: {', '.join(dp.concept or dp.content[:50] for dp in data_points[:3])}..."
                )

            data_note_ids: list[str] = []
            data_note_id_map: dict[str, str] = {}
            if ctx.dikiwi_obsidian_writer:
                try:
                    source_paths = ctx.drop.metadata.get("source_paths", [])
                    for data_point in data_points:
                        note_id = await ctx.dikiwi_obsidian_writer.write_data_point_note(
                            data_point=data_point,
                            source=drop.source,
                            source_paths=source_paths,
                        )
                        data_note_ids.append(note_id)
                        data_note_id_map[data_point.id] = note_id
                except Exception as e:
                    logger.warning("[DIKIWI] Failed to write data point notes: %s", e)

            return StageResult(
                stage=DikiwiStage.DATA,
                success=True,
                items_processed=1,
                items_output=len(data_points),
                processing_time_ms=processing_time,
                data={
                    "data_points": data_points,
                    "doc_title": doc_title,
                    "doc_summary": doc_summary,
                    "data_note_id": data_note_ids[0] if data_note_ids else "",
                    "data_note_ids": data_note_ids,
                    "data_note_id_map": data_note_id_map,
                },
            )

        except Exception as exc:
            return StageResult(
                stage=DikiwiStage.DATA,
                success=False,
                error_message=str(exc),
                processing_time_ms=(time.time() - start) * 1000,
            )

    async def _markdownize_drop(self, ctx: AgentContext):
        from dataclasses import replace, is_dataclass
        import re

        drop = ctx.drop
        metadata = getattr(drop, "metadata", {})
        if (
            metadata.get("source_type") in {"url_markdown", "chaos_markdown"}
            or metadata.get("processing_method") == "browser_url_markdown_fetch"
        ):
            logger.info(
                "[DIKIWI] Skipping markdownize for pre-normalized content type=%s",
                metadata.get("source_type", "unknown"),
            )
            return drop

        content = drop.content
        # Extract URLs from markdown links/images first
        md_urls = re.findall(r"!\[.*?\]\((https?://[^\)]+)\)", content)
        md_urls += re.findall(r"\[.*?\]\((https?://[^\)]+)\)", content)
        # Extract bare URLs, stopping at common markdown delimiters
        bare_urls = re.findall(r"https?://[^\s\)\]\>\"']+", content)
        urls = []
        for url in md_urls + bare_urls:
            # Final cleanup: strip trailing punctuation
            url = url.rstrip(").,;:!?*\"']")
            if url and url not in urls:
                urls.append(url)
        if not urls:
            return drop
        if (
            not SETTINGS.follow_external_links_for_uploads
            and metadata.get("source_type") in {"pdf", "document"}
            and str(getattr(drop, "source", "")).startswith("web_upload")
        ):
            logger.info(
                "[DIKIWI] Treating %d external URLs as references for uploaded %s; enrichment disabled",
                len(urls),
                metadata.get("source_type"),
            )
            return drop

        logger.info("[DIKIWI] Markdownizing %d URLs", len(urls))
        markdown_parts = []
        for url in urls:
            try:
                md_content = await ctx.markdownizer.process_url(url)
                if md_content.markdown:
                    markdown_parts.append(f"## Content from {url}\n\n{md_content.markdown}")
                else:
                    markdown_parts.append(f"## {url}\n\n[Could not extract content]")
            except Exception as e:
                logger.warning("[DIKIWI] Failed to markdownize %s: %s", url, e)
                markdown_parts.append(f"## {url}\n\n[Error: {e}]")

        text_without_urls = re.sub(r"https?://\S+", "", content).strip()
        combined = []
        if text_without_urls:
            # Only include original text if it's meaningful (not a landing-page wrapper)
            quality = self._assess_content_quality(text_without_urls)
            if not quality["is_too_thin"]:
                combined.append(f"## User Message\n\n{text_without_urls}")
            else:
                logger.info("[DIKIWI] Omitting thin original text: %s", quality["reason"])
        combined.extend(markdown_parts)
        markdownized_content = "\n\n---\n\n".join(combined)
        if is_dataclass(drop):
            return replace(drop, content=markdownized_content)
        drop.content = markdownized_content
        return drop

    @staticmethod
    def _assess_content_quality(content: str) -> dict[str, str | bool]:
        """Fast heuristic to detect landing-page or wrapper content with no substance."""
        import re

        text = re.sub(r"https?://\S+", "", content)
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
        text = re.sub(r"\[.*?\]\(.*?\)", "", text)
        text = re.sub(r"[#*_`>\-|]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < 200:
            return {"is_too_thin": True, "reason": f"Text too short ({len(text)} chars) after stripping markup"}

        # Generic landing-page markers that indicate no real content was fetched
        generic_markers = [
            "monica - your gpt ai assistant chrome extension",
            "monicamarch",
            "download#",
            "continue in chat",
            "sharecontinue in chat",
            "checking your browser",
            "just a moment",
            "access denied",
        ]
        lower = text.lower()
        marker_hits = sum(1 for m in generic_markers if m in lower)
        if marker_hits >= 2:
            return {"is_too_thin": True, "reason": f"Detected {marker_hits} generic landing-page markers"}

        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        meaningful = [p for p in paragraphs if len(p) > 60 and not p.startswith("!") and not p.startswith("[")]
        if len(meaningful) < 2:
            return {"is_too_thin": True, "reason": f"Only {len(meaningful)} meaningful paragraphs found"}

        avg_para_len = sum(len(p) for p in meaningful) / max(len(meaningful), 1)
        if avg_para_len < 80:
            return {"is_too_thin": True, "reason": f"Average paragraph length too short ({avg_para_len:.0f} chars)"}

        return {"is_too_thin": False, "reason": "ok"}

    @staticmethod
    def _chunk_content(content: str, chunk_size: int = 4000) -> list[str]:
        if len(content) <= chunk_size:
            return [content]
        paragraphs = content.split("\n\n")
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for para in paragraphs:
            para_len = len(para) + 2
            if current_len + para_len > chunk_size and current:
                chunks.append("\n\n".join(current))
                overlap_target = chunk_size // 10
                overlap: list[str] = []
                overlap_len = 0
                for p in reversed(current):
                    if overlap_len + len(p) > overlap_target:
                        break
                    overlap.insert(0, p)
                    overlap_len += len(p) + 2
                current = overlap
                current_len = overlap_len
            current.append(para)
            current_len += para_len
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    async def _llm_extract_chunk(
        self,
        content: str,
        source: str,
        ctx: AgentContext,
        chunk_index: int = 0,
        existing_concepts: list[str] | None = None,
    ):
        from aily.sessions.dikiwi_mind import DataPoint

        memory_context = (
            DikiwiPromptRegistry.render_memory(ctx.memory, limit=1200)
            if ctx.memory
            else ""
        )
        messages = DikiwiPromptRegistry.data_extraction(
            source=source,
            content=content,
            memory_context=memory_context,
            existing_concepts=existing_concepts or [],
        )
        stage_key = f"data:chunk{chunk_index}:{hashlib.sha1(content[:200].encode()).hexdigest()[:8]}"

        try:
            result = await chat_json(
                llm_client=ctx.llm_client,
                stage="data",
                stage_key=stage_key,
                messages=messages,
                temperature=0.2,
                budget=ctx.budget,
            )
        except Exception as exc:
            logger.warning("[DIKIWI] Chunk %d extraction failed: %s", chunk_index, exc)
            return [], {}

        if not isinstance(result, dict):
            return [], {}

        meta = {
            "title": str(result.get("title", "")),
            "summary": str(result.get("summary", "")),
            "quality_assessment": str(result.get("quality_assessment", "medium")).lower(),
        }
        data_points: list[DataPoint] = []
        for i, pd in enumerate(result.get("data_points", [])):
            if not isinstance(pd, dict) or not pd.get("content"):
                continue
            data_points.append(
                DataPoint(
                    id=f"dp_{uuid.uuid4().hex[:8]}_{chunk_index}_{i}",
                    content=pd["content"].strip(),
                    context=pd.get("context", ""),
                    source=source,
                    confidence=float(pd.get("confidence", 0.8)),
                    concept=str(pd.get("concept", "")),
                    source_evidence=[
                        str(e).strip()
                        for e in pd.get("source_evidence", [])
                        if isinstance(e, str) and str(e).strip()
                    ][:3],
                )
            )
        return data_points, meta

    async def _llm_fallback_extraction(self, content: str, source: str, ctx: AgentContext):
        from aily.sessions.dikiwi_mind import DataPoint

        messages = DikiwiPromptRegistry.fallback_extraction(
            source=source,
            content_preview=f"{content[:2000]}...",
        )
        try:
            result = await chat_json(
                llm_client=ctx.llm_client,
                stage="data_fallback",
                stage_key=f"data_fallback:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}",
                messages=messages,
                temperature=0.3,
                budget=ctx.budget,
            )
            if isinstance(result, dict):
                summary = result.get("summary") or result.get("key_takeaway", "")
                if summary:
                    return [
                        DataPoint(
                            id=f"dp_{uuid.uuid4().hex[:8]}",
                            content=summary,
                            source=source,
                            confidence=result.get("confidence", 0.5),
                        )
                    ]
        except Exception as exc:
            logger.warning("[DIKIWI] Fallback extraction failed for %s: %s", source, exc)
        return [
            DataPoint(
                id=f"dp_{uuid.uuid4().hex[:8]}",
                content=f"[Content from {source} - extraction failed, manual review needed]",
                source=source,
                confidence=0.0,
            )
        ]

    def _filter_data_points(self, data_points: list[DataPoint]) -> list[DataPoint]:
        filtered: list[DataPoint] = []
        seen: set[str] = set()
        for data_point in data_points:
            content = getattr(data_point, "content", "").strip()
            if not content:
                continue
            reason = self._data_point_rejection_reason(data_point)
            if reason:
                logger.info("[DIKIWI] Dropping low-quality datapoint %s: %s", data_point.id, reason)
                continue
            key = " ".join(content.lower().split())
            if key in seen:
                continue
            seen.add(key)
            filtered.append(data_point)
        return filtered

    def _data_point_rejection_reason(self, data_point: DataPoint) -> str | None:
        content = " ".join(data_point.content.split())
        if self._is_generic_page_datapoint(data_point):
            return "generic page/slide container"
        if data_point.modality == "visual":
            if len(content) < 20:
                return "visual datapoint too short"
            return None

        words = content.split()
        if len(words) < 7:
            return "too few words"
        if len(content) < 45:
            return "too short"

        alpha_chars = sum(ch.isalpha() for ch in content)
        punctuation_chars = sum(not ch.isalnum() and not ch.isspace() for ch in content)
        if alpha_chars < 0.45 * max(len(content), 1):
            return "too few alphabetic characters"
        if punctuation_chars > 0.18 * max(len(content), 1):
            return "too much punctuation noise"

        long_words = [word for word in words if len(word) >= 4]
        if len(long_words) < 3:
            return "not enough meaningful tokens"

        avg_word_len = sum(len(word) for word in words) / max(len(words), 1)
        if avg_word_len < 3.2:
            return "word shapes look like OCR noise"

        generic_fillers = (
            "plays a crucial role",
            "is important because",
            "has significant implications",
            "strategic pathways",
            "advantages for specialized hardware",
            "involves various aspects",
        )
        lowered = content.lower()
        if any(phrase in lowered for phrase in generic_fillers):
            return "generic abstraction filler"

        return None

    @staticmethod
    def _is_generic_page_datapoint(data_point: DataPoint) -> bool:
        import re

        concept = " ".join(str(getattr(data_point, "concept", "") or "").split())
        content = " ".join(str(getattr(data_point, "content", "") or "").split())
        context = " ".join(str(getattr(data_point, "context", "") or "").split())
        page_pattern = re.compile(r"^(page|slide)\s+\d{1,4}$", re.IGNORECASE)
        if page_pattern.match(concept):
            return True
        if page_pattern.match(content):
            return True
        if page_pattern.match(context):
            return True
        if re.match(r"^(page|slide)\s+\d{1,4}\s*[:\-]", concept, re.IGNORECASE):
            return True
        if re.match(r"^(page|slide)\s+\d{1,4}\s*[:\-]\s*(figure|image|table|content|overview)?\s*$", content, re.IGNORECASE):
            return True
        return False

    def _build_visual_data_points(self, ctx: AgentContext) -> list[DataPoint]:
        from aily.sessions.dikiwi_mind import DataPoint

        metadata = getattr(ctx.drop, "metadata", {}) or {}
        raw_visuals = metadata.get("visual_elements", [])
        if not isinstance(raw_visuals, list):
            return []

        asset_map: dict[str, str] = {}
        for entry in metadata.get("chaos_visual_assets", []):
            if not isinstance(entry, dict):
                continue
            asset_path = entry.get("asset_path")
            wikilink = entry.get("wikilink")
            if isinstance(asset_path, str) and isinstance(wikilink, str):
                asset_map[asset_path] = wikilink

        visual_points: list[DataPoint] = []
        for index, item in enumerate(raw_visuals):
            if not isinstance(item, dict):
                continue

            description = str(item.get("description", "")).strip()
            ocr_text = str(item.get("ocr_text", "")).strip()
            analysis = str(item.get("llm_analysis", "")).strip()
            element_type = str(item.get("element_type", "")).strip() or "visual"
            asset_path = str(item.get("asset_path", "")).strip()

            parts = []
            if description:
                parts.append(description)
            if ocr_text:
                parts.append(f"OCR: {ocr_text}")
            if analysis:
                parts.append(f"Analysis: {analysis}")
            content = "\n".join(parts).strip()
            if not content:
                continue

            source_page = item.get("source_page")
            if not isinstance(source_page, int):
                source_page = None

            concept = description or f"{element_type.title()} datum {index + 1}"
            context_bits = [f"Visual datapoint extracted from {element_type}"]
            if source_page is not None:
                context_bits.append(f"page {source_page}")

            embeds = []
            if asset_path and asset_path in asset_map:
                embeds.append(asset_map[asset_path])

            visual_points.append(
                DataPoint(
                    id=f"dpv_{uuid.uuid4().hex[:8]}_{index}",
                    content=content,
                    source=ctx.drop.source,
                    context=", ".join(context_bits),
                    confidence=0.95,
                    concept=concept,
                    modality="visual",
                    source_page=source_page,
                    visual_type=element_type,
                    asset_embeds=embeds,
                )
            )

        return visual_points

    @staticmethod
    def _merge_data_points(primary: list[DataPoint], additional: list[DataPoint]) -> list[DataPoint]:
        seen = {
            " ".join(dp.content.lower().split())
            for dp in primary
            if getattr(dp, "content", "").strip()
        }
        merged = list(primary)
        for data_point in additional:
            key = " ".join(data_point.content.lower().split())
            if key in seen:
                continue
            seen.add(key)
            merged.append(data_point)
        return merged
