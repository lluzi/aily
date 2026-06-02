"""Chaos-to-DIKIWI Bridge - Feed extracted content into Zettelkasten pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from aily.chaos.types import ExtractedContentMultimodal
from aily.config import SETTINGS
from aily.gating.drainage import RainDrop, RainType, StreamType
from aily.sessions.dikiwi_mind import DikiwiMind

logger = logging.getLogger(__name__)


def _stage_result_summary(result: Any) -> list[dict[str, Any]]:
    """Return JSON-safe per-stage traceability data for evidence manifests."""
    summaries: list[dict[str, Any]] = []
    for sr in getattr(result, "stage_results", []) or []:
        stage = getattr(sr, "stage", None)
        data = getattr(sr, "data", {}) or {}
        summaries.append(
            {
                "stage": getattr(stage, "name", str(stage)),
                "success": bool(getattr(sr, "success", False)),
                "items_processed": int(getattr(sr, "items_processed", 0) or 0),
                "items_output": int(getattr(sr, "items_output", 0) or 0),
                "processing_time_ms": round(float(getattr(sr, "processing_time_ms", 0.0) or 0.0), 2),
                "error_message": getattr(sr, "error_message", None),
                "persisted_note_counts": {
                    key: len(data.get(key, []) or [])
                    for key in [
                        "data_note_ids",
                        "info_note_ids",
                        "knowledge_note_ids",
                        "insight_note_ids",
                        "wisdom_note_ids",
                    ]
                    if key in data
                },
            }
        )
    return summaries


class ChaosDikiwiBridge:
    """Bridge between Chaos extracted content and DIKIWI Zettelkasten."""

    def __init__(
        self,
        dikiwi_mind: DikiwiMind,
        processed_folder: Path | None = None,
    ) -> None:
        self.dikiwi_mind = dikiwi_mind
        self.processed_folder = processed_folder or (Path.home() / "aily_chaos" / ".processed")

    async def _process_batch_dikiwi(self, drops: list[RainDrop]) -> Any:
        try:
            return await self.dikiwi_mind.process_inputs_batched(
                drops,
                foundation_only=SETTINGS.dikiwi_foundation_only_ingestion,
            )
        except TypeError as exc:
            if "foundation_only" not in str(exc):
                raise
            return await self.dikiwi_mind.process_inputs_batched(drops)

    async def process_extracted_content(
        self,
        content: ExtractedContentMultimodal,
    ) -> dict[str, Any]:
        """Process extracted content through DIKIWI pipeline.

        Args:
            content: Extracted multimodal content from Chaos

        Returns:
            DIKIWI processing results with Zettelkasten IDs
        """
        logger.info(f"Feeding to DIKIWI: {content.title or 'Untitled'}")

        # Create RainDrop from extracted content
        drop = self._create_raindrop(content)

        # Process through DIKIWI 6-stage pipeline
        try:
            result = await self.dikiwi_mind.process_input(drop)

            # Get final stage reached
            final_stage = result.final_stage_reached
            final_stage_name = final_stage.name if final_stage else "UNKNOWN"

            # Count zettels and insights from stage results
            zettels_created = 0
            insights_count = 0
            for sr in result.stage_results:
                if sr.success:
                    zettels_created += len(sr.data.get("zettels", []))
                    insights_count += len(sr.data.get("insights", []))

            logger.info(
                f"DIKIWI complete for {content.title}: "
                f"Stage {final_stage_name}, Zettels: {zettels_created}, Insights: {insights_count}"
            )

            return {
                "drop_id": drop.id,
                "stage": final_stage_name,
                "zettels_created": zettels_created,
                "insights": insights_count,
                "pipeline_id": result.pipeline_id,
                "stage_results": _stage_result_summary(result),
            }

        except Exception as e:
            logger.exception(f"DIKIWI processing failed: {e}")
            return {"error": str(e), "drop_id": drop.id}

    async def process_extracted_content_batch(
        self,
        contents: list[ExtractedContentMultimodal],
    ) -> dict[str, Any]:
        """Process extracted content as a stage-latched DIKIWI batch."""
        if not contents:
            return {
                "results": [],
                "processed": 0,
                "failed": 0,
                "zettels_created": 0,
                "insights": 0,
                "incremental_ratio": 0.0,
                "higher_order_triggered": False,
                "higher_order_candidates": 0,
                "higher_order_selected": 0,
            }

        drops = [self._create_raindrop(content) for content in contents]
        async with self._batch_lock():
            batch_run = await self._process_batch_dikiwi(drops)

        results: list[dict[str, Any]] = []
        processed = 0
        failed = 0
        total_zettels = 0
        total_insights = 0

        for content, drop, pipeline_result in zip(contents, drops, batch_run.results):
            final_stage = pipeline_result.final_stage_reached
            final_stage_name = final_stage.name if final_stage else "UNKNOWN"

            zettels_created = 0
            insights_count = 0
            stage_error = None
            for sr in pipeline_result.stage_results:
                if sr.success:
                    zettels_created += len(sr.data.get("zettels", []))
                    insights_count += len(sr.data.get("insights", []))
                elif stage_error is None:
                    stage_error = sr.error_message or f"{sr.stage.name} failed"

            item = {
                "drop_id": drop.id,
                "pipeline_id": pipeline_result.pipeline_id,
                "stage": final_stage_name,
                "zettels_created": zettels_created,
                "insights": insights_count,
                "source_path": str(content.source_path) if content.source_path else None,
                "stage_results": _stage_result_summary(pipeline_result),
            }
            if stage_error:
                item["error"] = stage_error
                failed += 1
            else:
                processed += 1
                total_zettels += zettels_created
                total_insights += insights_count
            results.append(item)

        return {
            "results": results,
            "processed": processed,
            "failed": failed,
            "zettels_created": total_zettels,
            "insights": total_insights,
            "incremental_ratio": batch_run.incremental_ratio,
            "incremental_threshold": batch_run.incremental_threshold,
            "higher_order_triggered": batch_run.higher_order_triggered,
            "higher_order_candidates": batch_run.higher_order_candidates,
            "higher_order_selected": batch_run.higher_order_selected,
            "pre_information_nodes": batch_run.pre_information_nodes,
            "post_information_nodes": batch_run.post_information_nodes,
        }

    async def process_chaos_markdown_batch(
        self,
        note_paths: list[Path],
    ) -> dict[str, Any]:
        """Process existing 00-Chaos markdown notes as one DIKIWI batch."""
        drops: list[RainDrop] = []
        effective_paths: list[Path] = []

        for note_path in note_paths:
            drop = self._create_raindrop_from_chaos_markdown(note_path)
            if drop is None:
                continue
            drops.append(drop)
            effective_paths.append(note_path)

        if not drops:
            return {
                "results": [],
                "processed": 0,
                "failed": 0,
                "zettels_created": 0,
                "insights": 0,
                "incremental_ratio": 0.0,
                "higher_order_triggered": False,
            }

        async with self._batch_lock():
            batch_run = await self._process_batch_dikiwi(drops)

        results: list[dict[str, Any]] = []
        processed = 0
        failed = 0
        total_zettels = 0
        total_insights = 0

        for note_path, drop, pipeline_result in zip(effective_paths, drops, batch_run.results):
            final_stage = pipeline_result.final_stage_reached
            final_stage_name = final_stage.name if final_stage else "UNKNOWN"

            zettels_created = 0
            insights_count = 0
            stage_error = None
            for sr in pipeline_result.stage_results:
                if sr.success:
                    zettels_created += len(sr.data.get("zettels", []))
                    insights_count += len(sr.data.get("insights", []))
                elif stage_error is None:
                    stage_error = sr.error_message or f"{sr.stage.name} failed"

            item = {
                "drop_id": drop.id,
                "pipeline_id": pipeline_result.pipeline_id,
                "stage": final_stage_name,
                "zettels_created": zettels_created,
                "insights": insights_count,
                "chaos_note_path": str(note_path),
                "stage_results": _stage_result_summary(pipeline_result),
            }
            if stage_error:
                item["error"] = stage_error
                failed += 1
            else:
                processed += 1
                total_zettels += zettels_created
                total_insights += insights_count
            results.append(item)

        return {
            "results": results,
            "processed": processed,
            "failed": failed,
            "zettels_created": total_zettels,
            "insights": total_insights,
            "incremental_ratio": batch_run.incremental_ratio,
            "incremental_threshold": batch_run.incremental_threshold,
            "higher_order_triggered": batch_run.higher_order_triggered,
            "higher_order_candidates": batch_run.higher_order_candidates,
            "higher_order_selected": batch_run.higher_order_selected,
            "pre_information_nodes": batch_run.pre_information_nodes,
            "post_information_nodes": batch_run.post_information_nodes,
        }

    def _create_raindrop(self, content: ExtractedContentMultimodal) -> RainDrop:
        """Convert Chaos content to RainDrop for DIKIWI processing."""
        # Build rich content from extracted data
        content_text = content.text or ""

        # Add title if available
        if content.title:
            content_text = f"# {content.title}\n\n{content_text}"

        # Add transcript if available
        if content.transcript:
            content_text += f"\n\n## Transcript\n\n{content.transcript}"

        # Add tags as context
        if content.tags:
            tags_str = ", ".join(content.tags)
            content_text += f"\n\n## Tags\n{tags_str}"

        # Add metadata
        metadata = {
            **content.metadata,
            "extracted_at": content.processing_timestamp.isoformat(),
            "processing_method": content.processing_method,
            "source_type": content.source_type,
            "has_transcript": content.transcript is not None,
            "visual_elements_count": len(content.visual_elements),
            "visual_elements": [elem.to_dict() for elem in content.visual_elements],
            "chaos_note_path": content.metadata.get("chaos_note_path"),
            "chaos_visual_assets": content.metadata.get("chaos_visual_assets", []),
            "original_tags": content.tags,
            "title": content.title,
        }
        if content.source_path:
            metadata.setdefault("source_paths", [str(content.source_path)])
        elif content.metadata.get("source_paths"):
            metadata["source_paths"] = content.metadata["source_paths"]

        # Add visual descriptions if available
        if content.visual_elements:
            visual_desc = "\n".join([
                f"- {getattr(elem, 'element_type', 'visual')}: {getattr(elem, 'description', '')}"
                for elem in content.visual_elements[:5]  # Limit to 5
            ])
            content_text += f"\n\n## Visual Elements\n{visual_desc}"

        return RainDrop(
            id="",
            rain_type=RainType.DOCUMENT,
            content=content_text,
            source="chaos_processor",
            source_id=str(content.source_path) if content.source_path else "unknown",
            stream_type=StreamType.EXTRACT_ANALYZE,
            metadata=metadata,
        )

    class _BatchLock:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.token = f"{os.getpid()}:{datetime.now().isoformat()}"

        async def __aenter__(self) -> Path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self.token, encoding="utf-8")
            return self.path

        async def __aexit__(self, exc_type, exc, tb) -> None:
            try:
                if self.path.exists() and self.path.read_text(encoding="utf-8") == self.token:
                    self.path.unlink()
            except Exception:
                logger.warning("Failed to clean up DIKIWI batch lock: %s", self.path)

    def _batch_lock(self) -> _BatchLock:
        return self._BatchLock(SETTINGS.dikiwi_batch_lock_path)

    def _create_raindrop_from_chaos_markdown(self, note_path: Path) -> RainDrop | None:
        """Convert an existing 00-Chaos markdown note into a RainDrop."""
        try:
            raw = note_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read chaos note %s: %s", note_path, exc)
            return None

        if note_path.name == "00 Zettelkasten Index.md":
            return None

        lines = raw.splitlines()
        title = note_path.stem.replace("_", " ")
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip() or title

        original_file = ""
        for line in lines[:12]:
            if line.startswith("**Original File:**"):
                original_file = line.split(":", 1)[1].strip()
                break

        if "\n---\n" in raw:
            body = raw.split("\n---\n", 1)[1].strip()
        else:
            body = raw.strip()

        content_text = f"# {title}\n\n{body}".strip()
        metadata = {
            "source_type": "chaos_markdown",
            "source_paths": [str(note_path)],
            "original_file": original_file,
            "chaos_note_path": str(note_path),
        }

        return RainDrop(
            id="",
            rain_type=RainType.DOCUMENT,
            content=content_text,
            source="chaos_processor",
            source_id=str(note_path),
            stream_type=StreamType.EXTRACT_ANALYZE,
            metadata=metadata,
        )

    async def process_incremental(
        self,
        new_markdown_paths: list[Path],
    ) -> dict[str, Any]:
        """Process newly arrived markdown files through the incremental DIKIWI pipeline.

        Only processes new content — existing notes are preserved. Higher stages
        (INSIGHT/WISDOM/IMPACT) only regenerate for affected graph neighborhoods.

        Args:
            new_markdown_paths: List of markdown file paths in 00-Chaos

        Returns:
            Incremental pipeline result summary
        """
        if not new_markdown_paths:
            return {"processed": 0, "new_info_nodes": 0, "affected_subgraphs": 0}

        from aily.dikiwi.incremental_orchestrator import IncrementalResult

        result: IncrementalResult | None = None
        try:
            result = await self.dikiwi_mind.process_input_incremental(new_markdown_paths)
        except Exception as exc:
            logger.exception("[Bridge] Incremental pipeline failed: %s", exc)
            return {"error": str(exc), "processed": 0}

        return {
            "new_files": result.new_files,
            "new_info_nodes": result.new_info_nodes,
            "affected_subgraphs": result.affected_subgraphs,
            "stale_insights": result.stale_insights,
            "stale_wisdom": result.stale_wisdom,
            "stale_impacts": result.stale_impacts,
            "regenerated_insights": result.regenerated_insights,
            "regenerated_wisdom": result.regenerated_wisdom,
            "regenerated_impacts": result.regenerated_impacts,
            "skipped_insights": result.skipped_insights,
            "skipped_wisdom": result.skipped_wisdom,
            "skipped_impacts": result.skipped_impacts,
            "elapsed_seconds": result.elapsed_seconds,
        }

    async def process_batch(
        self,
        date_folder: str | None = None,
        max_items: int | None = None,
    ) -> dict[str, Any]:
        """Process a batch of extracted JSON files through DIKIWI.

        Args:
            date_folder: Date folder to process (e.g., "2026-04-12"), defaults to today
            max_items: Maximum items to process, None for all

        Returns:
            Batch processing statistics
        """
        date_folder = date_folder or datetime.now().strftime("%Y-%m-%d")
        source_dir = self.processed_folder / date_folder

        if not source_dir.exists():
            logger.warning(f"Source directory not found: {source_dir}")
            return {"processed": 0, "failed": 0, "zettels_created": 0}

        # Find all JSON files
        json_files = list(source_dir.glob("*.json"))

        if max_items:
            json_files = json_files[:max_items]

        logger.info(f"Processing {len(json_files)} items through DIKIWI...")

        contents: list[ExtractedContentMultimodal] = []

        for json_file in json_files:
            try:
                # Load extracted content
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Convert to ExtractedContentMultimodal
                content = self._dict_to_content(data)

                # Split multi-URL imports into individual jobs
                jobs = self._split_content_into_jobs(content)
                logger.info(
                    "Processing %s through DIKIWI as %d job(s)",
                    json_file.name,
                    len(jobs),
                )

                contents.extend(jobs)

            except Exception as e:
                logger.warning(f"Failed to process {json_file.name}: {e}")
        stats = await self.process_extracted_content_batch(contents)
        stats["source_folder"] = str(source_dir)

        logger.info(f"Batch complete: {stats}")
        return stats

    def _dict_to_content(self, data: dict) -> ExtractedContentMultimodal:
        """Convert dict back to ExtractedContentMultimodal."""
        from datetime import datetime as dt

        return ExtractedContentMultimodal(
            text=data.get("text", ""),
            title=data.get("title"),
            source_type=data.get("source_type", "unknown"),
            source_path=Path(data["source_path"]) if data.get("source_path") else None,
            metadata=data.get("metadata", {}),
            tags=data.get("tags", []),
            processing_timestamp=dt.fromisoformat(data["processing_timestamp"])
            if data.get("processing_timestamp")
            else dt.now(),
            processing_method=data.get("processing_method", "unknown"),
        )

    @staticmethod
    def _split_content_into_jobs(content: ExtractedContentMultimodal) -> list[ExtractedContentMultimodal]:
        """Expand one extracted content item into multiple DIKIWI jobs when appropriate."""
        if content.source_type != "url_markdown":
            return [content]

        from aily.chaos.config import ChaosConfig
        from aily.chaos.processors.document import TextProcessor

        processor = TextProcessor(ChaosConfig())
        return processor.split_url_import_items(content)


async def run_chaos_to_zettelkasten(
    vault_path: Path,
    processed_folder: Path | None = None,
    date_folder: str | None = None,
) -> dict[str, Any]:
    """CLI entry point: Process Chaos extracted content to Zettelkasten.

    Args:
        vault_path: Path to Obsidian vault
        processed_folder: Path to processed Chaos files
        date_folder: Specific date folder to process

    Returns:
        Processing statistics
    """
    import os

    from aily.config import SETTINGS
    from aily.llm.provider_routes import PrimaryLLMRoute
    from aily.sessions.dikiwi_mind import DikiwiMind
    from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter

    # Setup
    llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
    llm_client = llm_resolver("dikiwi")

    # Initialize GraphDB
    from aily.graph.db import GraphDB
    graph_db = GraphDB(db_path=vault_path / ".aily" / "graph.db")
    await graph_db.initialize()

    # Initialize Obsidian writer
    obsidian_writer = DikiwiObsidianWriter(vault_path=vault_path)

    # Initialize browser manager for JS-rendered pages
    from aily.browser.manager import BrowserUseManager
    browser_manager = BrowserUseManager()
    await browser_manager.start()

    try:
        # Initialize DIKIWI mind
        dikiwi_mind = DikiwiMind(
            graph_db=graph_db,
            llm_client=llm_client,
            llm_client_resolver=llm_resolver,
            dikiwi_obsidian_writer=obsidian_writer,
            browser_manager=browser_manager,
        )

        # Create bridge
        bridge = ChaosDikiwiBridge(
            dikiwi_mind=dikiwi_mind,
            processed_folder=processed_folder,
        )

        # Process batch
        stats = await bridge.process_batch(date_folder=date_folder)
    finally:
        try:
            await browser_manager.stop()
        except Exception:
            pass

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Chaos to Zettelkasten bridge")
    parser.add_argument("--vault", "-v", type=str, required=True, help="Obsidian vault path")
    parser.add_argument("--folder", "-f", type=str, default="~/aily_chaos/.processed")
    parser.add_argument("--date", "-d", type=str, help="Date folder (default: today)")
    parser.add_argument("--max", "-m", type=int, help="Max items to process")

    args = parser.parse_args()

    result = asyncio.run(run_chaos_to_zettelkasten(
        vault_path=Path(args.vault),
        processed_folder=Path(args.folder).expanduser(),
        date_folder=args.date,
    ))

    print(f"\n{'='*50}")
    print("Chaos → Zettelkasten Complete")
    print(f"{'='*50}")
    print(f"Processed: {result['processed']}")
    print(f"Failed: {result['failed']}")
    print(f"Zettels Created: {result['zettels_created']}")
