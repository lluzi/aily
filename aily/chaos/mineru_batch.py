"""Batch ingest documents through MinerU directly into 00-Chaos and DIKIWI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aily.chaos.config import ChaosConfig
from aily.chaos.dikiwi_bridge import ChaosDikiwiBridge
from aily.chaos.chaos_markdown import render_chaos_markdown
from aily.chaos.processors.mineru_processor import MinerUProcessor, _MinerULocalAPIService
from aily.chaos.types import ExtractedContentMultimodal
from aily.config import SETTINGS
from aily.graph.db import GraphDB
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.sessions.dikiwi_mind import DikiwiMind
from aily.sessions.entrepreneur_scheduler import EntrepreneurScheduler
from aily.sessions.reactor_scheduler import ReactorScheduler
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter

logger = logging.getLogger(__name__)


GENERIC_CHAOS_TITLES = {
    "agenda",
    "contents",
    "content",
    "introduction",
    "overview",
    "summary",
    "synopsys",
    "synopsys new thinking",
    "synopsys xin si",
    "untitled",
}


@dataclass
class MinerUBatchItemResult:
    """Processing result for one source document."""

    source_path: str
    title: str | None
    status: str
    error: str | None = None
    stage: str | None = None
    zettels_created: int = 0
    insights: int = 0
    transcript_path: str | None = None
    extraction_md_path: str | None = None
    extraction_json_path: str | None = None


@dataclass
class MinerUBatchSummary:
    """Aggregate batch result."""

    total: int = 0
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    business_enabled: bool = False
    reactor_screened_limit: int | None = None
    reactor_approved: int = 0
    entrepreneur_evaluated: int = 0
    entrepreneur_approved: int = 0
    business_error: str | None = None
    results: list[MinerUBatchItemResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "processed": self.processed,
            "failed": self.failed,
            "skipped": self.skipped,
            "business_enabled": self.business_enabled,
            "reactor_screened_limit": self.reactor_screened_limit,
            "reactor_approved": self.reactor_approved,
            "entrepreneur_evaluated": self.entrepreneur_evaluated,
            "entrepreneur_approved": self.entrepreneur_approved,
            "business_error": self.business_error,
            "results": [item.__dict__ for item in self.results],
        }


class MinerUChaosBatchRunner:
    """Run a whole folder through MinerU and DIKIWI with one shared runtime."""

    def __init__(
        self,
        *,
        source_folder: Path,
        vault_path: Path,
        processed_folder: Path | None = None,
        run_dikiwi: bool = True,
        run_business: bool = False,
        business_max_per_session: int | None = None,
        business_screening_limit: int | None = None,
        skip_existing: bool = False,
    ) -> None:
        if run_business and not run_dikiwi:
            raise ValueError("run_business requires run_dikiwi=True")

        self.source_folder = source_folder.expanduser().resolve()
        self.vault_path = vault_path.expanduser().resolve()
        self.processed_folder = (
            processed_folder.expanduser().resolve()
            if processed_folder is not None
            else (self.source_folder / ".processed").resolve()
        )
        self.run_dikiwi = run_dikiwi
        self.run_business = run_business
        self.business_max_per_session = business_max_per_session
        self.business_screening_limit = business_screening_limit
        self.skip_existing = skip_existing

        self.config = ChaosConfig(
            watch_folder=self.source_folder,
            processed_folder=self.processed_folder,
            failed_folder=self.source_folder / ".failed",
        )
        self.processor = MinerUProcessor(self.config)

        self._graph_db: GraphDB | None = None
        self._bridge: ChaosDikiwiBridge | None = None
        self._llm_client: Any | None = None
        self._obsidian_writer: DikiwiObsidianWriter | None = None

    def discover_files(self) -> list[Path]:
        """Discover MinerU-supported documents under the source folder."""
        files: list[Path] = []
        supported = MinerUProcessor.SUPPORTED_SUFFIXES
        skip_dirs = {".processed", ".failed", ".git", "__pycache__", "node_modules", ".venv", "venv"}

        for path in self.source_folder.rglob("*"):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.parts if part != ".processed"):
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            if path.suffix.lower() in supported:
                files.append(path)

        return sorted(files)

    async def initialize(self) -> None:
        """Initialize the shared DIKIWI runtime when needed."""
        if not self.run_dikiwi or self._bridge is not None:
            return

        llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
        llm_client = llm_resolver("dikiwi")
        self._llm_client = llm_client

        self._graph_db = GraphDB(db_path=SETTINGS.graph_db_path)
        await self._graph_db.initialize()

        obsidian_writer = DikiwiObsidianWriter(vault_path=self.vault_path)
        self._obsidian_writer = obsidian_writer
        dikiwi_mind = DikiwiMind(
            graph_db=self._graph_db,
            llm_client=llm_client,
            llm_client_resolver=llm_resolver,
            dikiwi_obsidian_writer=obsidian_writer,
        )
        self._bridge = ChaosDikiwiBridge(dikiwi_mind=dikiwi_mind, processed_folder=self.processed_folder)

    async def close(self) -> None:
        """Close the shared runtime cleanly."""
        if self._graph_db is not None:
            await self._graph_db.close()
            self._graph_db = None
        _MinerULocalAPIService.shared().stop_sync()
        self._bridge = None

    async def run(self, files: list[Path] | None = None, limit: int | None = None) -> MinerUBatchSummary:
        """Run the batch over discovered or explicit files."""
        if files is None:
            files = self.discover_files()
        if limit is not None:
            files = files[:limit]

        summary = MinerUBatchSummary(total=len(files))
        await self.initialize()

        semaphore = asyncio.Semaphore(max(1, SETTINGS.mineru_batch_extract_concurrency))

        async def _extract_one(index: int, path: Path) -> tuple[MinerUBatchItemResult, ExtractedContentMultimodal | None]:
            async with semaphore:
                logger.info("MinerU extract %s/%s: %s", index, len(files), path.name)
                return await self._extract_file(path)

        extracted_pairs = await asyncio.gather(
            *[_extract_one(index, path) for index, path in enumerate(files, start=1)]
        )

        batch_inputs: list[tuple[MinerUBatchItemResult, ExtractedContentMultimodal]] = []
        for result, extracted in extracted_pairs:
            summary.results.append(result)
            if result.status == "ok":
                summary.processed += 1
                if self.run_dikiwi and extracted is not None:
                    batch_inputs.append((result, extracted))
            elif result.status == "skipped":
                summary.skipped += 1
            else:
                summary.failed += 1

        if self.run_dikiwi and self._bridge is not None and batch_inputs:
            bridge_results = await self._bridge.process_extracted_content_batch(
                [extracted for _, extracted in batch_inputs]
            )
            for (batch_result, _), pipeline_result in zip(batch_inputs, bridge_results.get("results", [])):
                if pipeline_result.get("error"):
                    batch_result.status = "failed"
                    batch_result.error = str(pipeline_result["error"])
                    summary.processed -= 1
                    summary.failed += 1
                else:
                    batch_result.stage = pipeline_result.get("stage")
                    batch_result.zettels_created = int(pipeline_result.get("zettels_created", 0))
                    batch_result.insights = int(pipeline_result.get("insights", 0))

        if self.run_business:
            await self.run_business_pass(summary)

        return summary

    async def run_business_pass(self, summary: MinerUBatchSummary) -> None:
        """Run Reactor screening and Entrepreneur/Guru once after batch ingestion."""
        summary.business_enabled = True
        business_max = (
            self.business_max_per_session
            if self.business_max_per_session is not None
            else SETTINGS.minds.proposal_max_per_session
        )
        screening_limit = self.business_screening_limit
        if screening_limit is None:
            screening_limit = max(business_max * 3, business_max)
        summary.reactor_screened_limit = screening_limit

        if self._graph_db is None or self._llm_client is None or self._obsidian_writer is None:
            summary.business_error = "dikiwi_runtime_not_initialized"
            return

        try:
            reactor = ReactorScheduler(
                graph_db=self._graph_db,
                llm_client=self._llm_client,
                obsidian_writer=self._obsidian_writer,
            )
            approved = await reactor._evaluate_residual_proposals(max_nodes=screening_limit)
            summary.reactor_approved = len(approved)

            entrepreneur = EntrepreneurScheduler(
                graph_db=self._graph_db,
                llm_client=self._llm_client,
                obsidian_writer=self._obsidian_writer,
                proposal_max_per_session=business_max,
            )
            result = await entrepreneur._run_session()
            summary.entrepreneur_evaluated = int(result.get("evaluated", 0))
            summary.entrepreneur_approved = int(result.get("proposals_generated", 0))
        except Exception as exc:
            logger.exception("MinerU business pass failed")
            summary.business_error = str(exc)

    async def process_file(self, source_path: Path) -> MinerUBatchItemResult:
        """Parse one file, write 00-Chaos artifacts, then feed DIKIWI."""
        result, extracted = await self._extract_file(source_path)
        if result.status != "ok" or not self.run_dikiwi or self._bridge is None or extracted is None:
            return result

        pipeline_result = await self._bridge.process_extracted_content(extracted)
        if "error" in pipeline_result:
            result.status = "failed"
            result.error = str(pipeline_result["error"])
        else:
            result.stage = pipeline_result.get("stage")
            result.zettels_created = int(pipeline_result.get("zettels_created", 0))
            result.insights = int(pipeline_result.get("insights", 0))

        return result

    async def _extract_file(
        self,
        source_path: Path,
    ) -> tuple[MinerUBatchItemResult, ExtractedContentMultimodal | None]:
        """Parse one file and persist 00-Chaos artifacts without advancing DIKIWI."""
        source_path = source_path.resolve()

        extracted = await self.processor.process(source_path)
        if extracted is None:
            return (
                MinerUBatchItemResult(
                    source_path=str(source_path),
                    title=source_path.stem,
                    status="failed",
                    error="mineru_extraction_failed",
                ),
                None,
            )

        base_name = chaos_base_name(extracted, source_path)
        transcript_target = self.vault_path / "00-Chaos" / f"{base_name}.md"
        if self.skip_existing and transcript_target.exists():
            return (
                MinerUBatchItemResult(
                    source_path=str(source_path),
                    title=extracted.title or base_name,
                    status="skipped",
                    transcript_path=str(transcript_target),
                ),
                None,
            )

        saved = await self._persist_extraction(extracted, source_path)
        return (
            MinerUBatchItemResult(
                source_path=str(source_path),
                title=extracted.title,
                status="ok",
                transcript_path=str(saved["transcript_path"]),
                extraction_md_path=str(saved["markdown_path"]),
                extraction_json_path=str(saved["json_path"]),
            ),
            extracted,
        )

    async def _persist_extraction(
        self,
        extracted: ExtractedContentMultimodal,
        source_path: Path,
    ) -> dict[str, Path]:
        """Persist processed JSON/markdown and the 00-Chaos transcript."""
        date_folder = datetime.now().strftime("%Y-%m-%d")
        output_dir = self.processed_folder / date_folder
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = chaos_base_name(extracted, source_path)
        json_path = output_dir / f"{base_name}.json"
        markdown_path = output_dir / f"{base_name}.md"
        transcript_result = await self._write_chaos_transcript(extracted, source_path)
        transcript_path = transcript_result["path"]
        extracted.metadata["chaos_note_path"] = str(transcript_path)
        extracted.metadata["chaos_markdown"] = {
            "page_count": transcript_result["page_count"],
            "screenshot_count": transcript_result["screenshot_count"],
            "screenshot_renderer": transcript_result["screenshot_renderer"],
            "screenshot_error": transcript_result["screenshot_error"],
        }
        extracted.metadata["chaos_visual_assets"] = self._get_visual_asset_embeds(extracted, base_name)

        json_path.write_text(
            json.dumps(extracted.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown_path.write_text(transcript_path.read_text(encoding="utf-8"), encoding="utf-8")

        return {
            "json_path": json_path,
            "markdown_path": markdown_path,
            "transcript_path": transcript_path,
        }

    async def _write_chaos_transcript(
        self,
        extracted: ExtractedContentMultimodal,
        source_path: Path,
    ) -> dict[str, Any]:
        """Write a one-to-one transcript note into 00-Chaos."""
        transcript_dir = self.vault_path / "00-Chaos"
        transcript_dir.mkdir(parents=True, exist_ok=True)

        base_name = chaos_base_name(extracted, source_path)
        display_title = _semantic_title(extracted, source_path) or extracted.title or base_name
        transcript_path = transcript_dir / f"{base_name}.md"
        counter = 1
        while transcript_path.exists() and not self.skip_existing:
            transcript_path = transcript_dir / f"{base_name}_{counter}.md"
            counter += 1

        rendered = await render_chaos_markdown(
            extracted=extracted,
            source_path=source_path,
            base_name=base_name,
            vault_path=self.vault_path,
        )
        transcript_path.write_text(rendered.markdown, encoding="utf-8")
        return {
            "path": transcript_path,
            "page_count": rendered.page_count,
            "screenshot_count": rendered.screenshot_count,
            "screenshot_renderer": rendered.screenshot_renderer,
            "screenshot_error": rendered.screenshot_error,
            "display_title": display_title,
        }

    def _render_processed_markdown(
        self,
        extracted: ExtractedContentMultimodal,
        source_path: Path,
        base_name: str,
    ) -> str:
        """Render the audit markdown persisted under .processed."""
        display_title = _semantic_title(extracted, source_path) or extracted.title or base_name
        return "\n".join(
            [
                f"# {display_title}",
                "",
                f"**Source:** {source_path.name}",
                "",
                f"**Type:** {extracted.source_type}",
                "",
                f"**Tags:** {', '.join(extracted.tags)}",
                "",
                "---",
                "",
                extracted.get_full_text(),
                *(["", *asset_section] if (asset_section := self._render_visual_asset_section(extracted, base_name)) else []),
            ]
        )

    def _render_visual_asset_section(
        self,
        extracted: ExtractedContentMultimodal,
        base_name: str,
    ) -> list[str]:
        """Copy extracted visual assets into the vault and render embed markdown."""
        embeds = self._get_visual_asset_embeds(extracted, base_name)
        if not embeds:
            return []

        lines = ["## Visual Assets", ""]
        for embed in embeds:
            caption = embed["caption"]
            if caption:
                lines.extend([f"### {caption}", ""])
            lines.extend([embed["wikilink"], ""])
        return lines[:-1] if lines and lines[-1] == "" else lines

    def _get_visual_asset_embeds(
        self,
        extracted: ExtractedContentMultimodal,
        base_name: str,
    ) -> list[dict[str, str]]:
        cached = extracted.metadata.get("chaos_visual_assets")
        if isinstance(cached, list):
            return [entry for entry in cached if isinstance(entry, dict)]

        embeds = self._copy_visual_assets(extracted, base_name)
        extracted.metadata["chaos_visual_assets"] = embeds
        return embeds

    def _copy_visual_assets(
        self,
        extracted: ExtractedContentMultimodal,
        base_name: str,
    ) -> list[dict[str, str]]:
        """Copy MinerU-returned images/tables/charts into the vault for Obsidian embeds."""
        output_dir_value = extracted.metadata.get("mineru_output_dir")
        output_dir = Path(output_dir_value) if isinstance(output_dir_value, str) and output_dir_value else None
        if output_dir is None:
            return []

        asset_dir = self.vault_path / "00-Chaos" / "_assets" / base_name
        asset_dir.mkdir(parents=True, exist_ok=True)

        seen_sources: set[str] = set()
        copied: list[dict[str, str]] = []

        for index, element in enumerate(extracted.visual_elements, start=1):
            asset_path_value = getattr(element, "asset_path", None)
            if not asset_path_value:
                continue
            resolved = self._resolve_visual_asset_path(output_dir, asset_path_value)
            if resolved is None or not resolved.exists() or not resolved.is_file():
                continue
            resolved_key = str(resolved.resolve())
            if resolved_key in seen_sources:
                continue
            seen_sources.add(resolved_key)

            target_name = resolved.name or f"{element.element_type}_{index}{resolved.suffix}"
            target_path = asset_dir / target_name
            if not target_path.exists():
                shutil.copy2(resolved, target_path)

            vault_relative = target_path.relative_to(self.vault_path).as_posix()
            caption = (element.description or "").strip()
            copied.append(
                {
                    "caption": caption,
                    "wikilink": f"![[{vault_relative}]]",
                    "asset_path": asset_path_value,
                    "element_type": element.element_type,
                }
            )

        return copied

    @staticmethod
    def _resolve_visual_asset_path(output_dir: Path, asset_path_value: str) -> Path | None:
        """Resolve a visual asset path stored by MinerU."""
        asset_path = Path(asset_path_value)
        if asset_path.is_absolute():
            return asset_path
        candidate = (output_dir / asset_path).resolve()
        if output_dir.resolve() not in candidate.parents and candidate != output_dir.resolve():
            return None
        return candidate


def chaos_base_name(extracted: ExtractedContentMultimodal, source_path: Path) -> str:
    """Choose a stable semantic filename for persisted Chaos artifacts.

    Obsidian graph nodes should represent content identity, not the source
    storage name. Explicit external overrides are honored unless they are just
    the source filename stem, which older MinerU wiring used as a default.
    """
    base_name = extracted.metadata.get("chaos_base_name")
    if isinstance(base_name, str) and base_name.strip() and base_name.strip() != source_path.stem:
        return _slugify_semantic_name(base_name)

    semantic_title = _semantic_title(extracted, source_path)
    if semantic_title:
        return _slugify_semantic_name(semantic_title)

    meaningful_tags = [_normalize_title(tag) for tag in extracted.tags if _is_meaningful_title(tag, source_path)]
    if meaningful_tags:
        tag_name = " ".join(meaningful_tags[:4])
        return _slugify_semantic_name(tag_name)

    content_hash = hashlib.sha1(extracted.get_full_text()[:4000].encode("utf-8")).hexdigest()[:8]
    return f"untitled-content-{content_hash}"


def _semantic_title(extracted: ExtractedContentMultimodal, source_path: Path) -> str:
    """Return the best content-derived title available without using filename."""
    candidates: list[str] = []
    if extracted.title:
        candidates.append(extracted.title)

    text = extracted.get_full_text()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            candidates.append(stripped.lstrip("#").strip())
            continue
        if len(stripped) > 12:
            candidates.append(stripped)
        if len(candidates) >= 12:
            break

    for candidate in candidates:
        if _is_meaningful_title(candidate, source_path):
            return _normalize_title(candidate)
    return ""


def _normalize_title(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value)).strip(" -:_")
    return cleaned[:120].rsplit(" ", 1)[0] if len(cleaned) > 120 and " " in cleaned[:120] else cleaned[:120]


def _is_meaningful_title(value: str, source_path: Path) -> bool:
    cleaned = _normalize_title(value)
    if not cleaned:
        return False
    if cleaned == source_path.stem or cleaned == source_path.name:
        return False
    lowered = re.sub(r"[^a-z0-9 ]+", " ", cleaned.lower())
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if lowered in GENERIC_CHAOS_TITLES:
        return False
    if lowered.endswith((".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx")):
        return False
    words = [word for word in re.split(r"\s+", lowered) if word]
    if len(words) < 2:
        return False
    if len(cleaned) < 12:
        return False
    if sum(ch.isalpha() for ch in cleaned) < 8:
        return False
    return True


def _slugify_semantic_name(value: str, max_length: int = 120) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    if not cleaned:
        return "untitled-content"
    slug = cleaned.replace(" ", "_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:max_length].rstrip("_") or "untitled-content"
