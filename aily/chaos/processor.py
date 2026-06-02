"""Main processor for Aily Chaos.

Orchestrates file watching, content extraction, tagging, and DIKIWI integration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from aily.chaos.config import ChaosConfig
from aily.chaos.chaos_markdown import render_chaos_markdown
from aily.chaos.tagger.engine import IntelligentTagger
from aily.chaos.types import (
    ExtractedContentMultimodal,
    ProcessingError,
    ProcessingJob,
    ProcessingStatus,
)
from aily.chaos.watcher import FileWatcher
from aily.config import SETTINGS
from aily.llm.provider_routes import PrimaryLLMRoute

logger = logging.getLogger(__name__)


class ChaosProcessor:
    """Main processor for multimodal content in the chaos folder.

    Usage:
        processor = ChaosProcessor()
        await processor.start()
        # Runs indefinitely, processing files as they appear
        await processor.stop()
    """

    def __init__(
        self,
        config: ChaosConfig | None = None,
        vault_path: str | Path | None = None,
    ) -> None:
        self.config = config or ChaosConfig()
        self.vault_path = Path(vault_path) if vault_path else None
        self.watcher = FileWatcher(self.config)
        self.tagger = IntelligentTagger(self.config)
        self.llm_client = PrimaryLLMRoute.from_settings(SETTINGS, workload="chaos")
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._active_jobs: dict[str, ProcessingJob] = {}
        self._job_callback: Callable[[ProcessingJob], None] | None = None

    def on_job_completed(
        self, callback: Callable[[ProcessingJob], None]
    ) -> None:
        """Register callback for job completion.

        Args:
            callback: Function called with completed job
        """
        self._job_callback = callback

    async def start(self) -> None:
        """Start the processor."""
        if self._running:
            return

        self._running = True
        await self.watcher.start()

        # Start worker task
        self._worker_task = asyncio.create_task(self._worker_loop())

        logger.info("ChaosProcessor started")

    async def stop(self) -> None:
        """Stop the processor."""
        if not self._running:
            return

        self._running = False

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        await self.watcher.stop()
        logger.info("ChaosProcessor stopped")

    async def _worker_loop(self) -> None:
        """Main worker loop."""
        while self._running:
            try:
                job = await self.watcher.job_queue.get()
                await self._process_job(job)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Worker loop error: %s", e)
                await asyncio.sleep(1)

    async def _process_job(self, job: ProcessingJob) -> None:
        """Process a single job."""
        job.started_at = datetime.utcnow()
        job.status = ProcessingStatus.PROCESSING
        self._active_jobs[job.job_id] = job

        logger.info("Processing job %s: %s", job.job_id, job.file_path.name)

        try:
            # 1. Validate file
            if not await self._validate_file(job):
                return

            # 2. Detect content type
            mime_type = await self._detect_mime_type(job.file_path)
            job.mime_type = mime_type

            # 3. Extract content
            extracted = await self._extract_content(job)
            if not extracted:
                raise Exception("Content extraction failed")

            # 4. Generate tags
            tags = await self.tagger.tag(extracted)
            extracted.tags = tags
            job.extracted = extracted

            # 5. Save extraction result
            await self._save_extraction(job, extracted)

            # 6. Move to processed
            await self._move_to_processed(job)

            job.status = ProcessingStatus.COMPLETED
            job.completed_at = datetime.utcnow()

            logger.info(
                "Job %s completed: %s (tags: %s)",
                job.job_id,
                job.file_path.name,
                len(tags),
            )

            # Notify callback
            if self._job_callback:
                self._job_callback(job)

        except Exception as e:
            logger.exception("Job %s failed: %s", job.job_id, e)
            job.status = ProcessingStatus.FAILED
            job.error = ProcessingError.UNKNOWN
            job.error_message = str(e)
            job.completed_at = datetime.utcnow()
            await self._move_to_failed(job)

        finally:
            if job.job_id in self._active_jobs:
                del self._active_jobs[job.job_id]

    async def _validate_file(self, job: ProcessingJob) -> bool:
        """Validate file before processing."""
        if not job.file_path.exists():
            job.status = ProcessingStatus.FAILED
            job.error = ProcessingError.CORRUPT_FILE
            job.error_message = "File does not exist"
            return False

        # Check file size
        size_mb = job.file_size / (1024 * 1024)
        if size_mb > self.config.max_file_size_mb:
            job.status = ProcessingStatus.FAILED
            job.error = ProcessingError.FILE_TOO_LARGE
            job.error_message = f"File too large: {size_mb:.1f}MB"
            return False

        return True

    async def _detect_mime_type(self, file_path: Path) -> str:
        """Detect MIME type using magic numbers."""
        try:
            import magic

            mime = magic.from_file(str(file_path), mime=True)
            return mime or "application/octet-stream"
        except ImportError:
            # Fallback to extension
            ext = file_path.suffix.lower()
            mime_map = {
                ".pdf": "application/pdf",
                ".mp4": "video/mp4",
                ".mov": "video/quicktime",
                ".avi": "video/x-msvideo",
                ".mkv": "video/x-matroska",
                ".doc": "application/msword",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".ppt": "application/vnd.ms-powerpoint",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xls": "application/vnd.ms-excel",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".md": "text/markdown",
                ".txt": "text/plain",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }
            return mime_map.get(ext, "application/octet-stream")

    async def _extract_content(
        self, job: ProcessingJob
    ) -> ExtractedContentMultimodal | None:
        """Extract content based on file type."""
        mime_type = job.mime_type or ""
        file_path = job.file_path

        # Route to appropriate processor
        if mime_type == "application/pdf":
            from aily.chaos.processors.pdf import PDFProcessor

            processor = PDFProcessor(self.config.pdf, self.llm_client)
            return await processor.process(file_path)

        elif mime_type.startswith("video/"):
            from aily.chaos.processors.video import VideoProcessor

            processor = VideoProcessor(self.config.video, self.llm_client)
            return await processor.process(file_path)

        elif mime_type.startswith("image/"):
            from aily.chaos.processors.image import ImageProcessor

            processor = ImageProcessor(self.config.image, self.llm_client)
            return await processor.process(file_path)

        elif mime_type in {
            "application/msword",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            from aily.chaos.processors.mineru_processor import MinerUProcessor

            mineru_processor = MinerUProcessor(self.config, self.llm_client)
            mineru_result = await mineru_processor.process(file_path)
            if mineru_result:
                return mineru_result

            # Try Docling when the optional dependency is installed.
            try:
                from aily.chaos.processors.docling_processor import DoclingProcessor

                processor = DoclingProcessor(self.config, self.llm_client)
                result = await processor.process(file_path)
                if result:
                    return result
            except Exception as exc:
                logger.warning("Docling extraction unavailable for %s: %s", file_path.name, exc)

            # Fallback to PPTX processor for presentations
            if mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
                from aily.chaos.processors.pptx import PPTXProcessor

                processor = PPTXProcessor(self.config.pptx, self.llm_client)
                return await processor.process(file_path)

            from aily.chaos.processors.document import GenericDocumentProcessor

            processor = GenericDocumentProcessor(self.config)
            return await processor.process(file_path)

        elif mime_type in {"text/markdown", "text/plain"}:
            from aily.chaos.processors.document import TextProcessor

            processor = TextProcessor(self.config)
            return await processor.process(file_path)

        else:
            # Try generic document processor
            from aily.chaos.processors.document import GenericDocumentProcessor

            processor = GenericDocumentProcessor(self.config)
            return await processor.process(file_path)

    async def _save_extraction(
        self, job: ProcessingJob, extracted: ExtractedContentMultimodal
    ) -> None:
        """Save extraction result to processed folder and 00-chaos transcript folder."""
        date_folder = datetime.now().strftime("%Y-%m-%d")
        output_dir = self.config.processed_folder / date_folder
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = self._chaos_base_name(extracted, job.file_path)

        # Save JSON
        json_path = output_dir / f"{base_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(extracted.to_dict(), f, ensure_ascii=False, indent=2)

        # Save Markdown
        md_path = output_dir / f"{base_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {extracted.title or base_name}\n\n")
            f.write(f"**Source:** {job.file_path.name}\n\n")
            f.write(f"**Type:** {extracted.source_type}\n\n")
            f.write(f"**Tags:** {', '.join(extracted.tags)}\n\n")
            f.write("---\n\n")
            f.write(extracted.get_full_text())

        # Save complete source-equivalent Markdown transcript to vault 00-Chaos.
        if self.vault_path:
            transcript_dir = self.vault_path / "00-Chaos"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = transcript_dir / f"{base_name}.md"

            # Handle duplicates
            counter = 1
            while transcript_path.exists():
                transcript_path = transcript_dir / f"{base_name}_{counter}.md"
                counter += 1

            rendered = await render_chaos_markdown(
                extracted=extracted,
                source_path=job.file_path,
                base_name=base_name,
                vault_path=self.vault_path,
            )
            extracted.metadata["chaos_markdown"] = {
                "page_count": rendered.page_count,
                "screenshot_count": rendered.screenshot_count,
                "screenshot_renderer": rendered.screenshot_renderer,
                "screenshot_error": rendered.screenshot_error,
            }
            transcript_path.write_text(rendered.markdown, encoding="utf-8")

            logger.info(
                "Saved complete transcript to vault 00-Chaos: %s",
                transcript_path.name,
            )

    @staticmethod
    def _chaos_base_name(extracted: ExtractedContentMultimodal, source_path: Path) -> str:
        """Choose a stable filename for persisted Chaos artifacts."""
        base_name = extracted.metadata.get("chaos_base_name")
        if isinstance(base_name, str) and base_name.strip():
            return base_name.strip()
        if extracted.source_type == "mineru_markdown" and source_path.name.lower() == "full.md":
            return source_path.parent.name
        return source_path.stem

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format seconds as HH:MM:SS or MM:SS."""
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    async def _move_to_processed(self, job: ProcessingJob) -> None:
        """Move original file to processed folder."""
        date_folder = datetime.now().strftime("%Y-%m-%d")
        target_dir = self.config.processed_folder / date_folder
        target_dir.mkdir(parents=True, exist_ok=True)

        target_path = target_dir / job.file_path.name

        # Handle duplicates
        counter = 1
        while target_path.exists():
            target_path = target_dir / f"{job.file_path.stem}_{counter}{job.file_path.suffix}"
            counter += 1

        await asyncio.to_thread(shutil.move, str(job.file_path), str(target_path))

    async def _move_to_failed(self, job: ProcessingJob) -> None:
        """Move file to failed folder with error info."""
        self.config.failed_folder.mkdir(parents=True, exist_ok=True)
        target_path = self.config.failed_folder / job.file_path.name

        # Save error info
        error_path = target_path.with_suffix(".error.json")
        with open(error_path, "w", encoding="utf-8") as f:
            json.dump(job.to_dict(), f, ensure_ascii=False, indent=2)

        # Move file if it exists
        if job.file_path.exists():
            await asyncio.to_thread(shutil.move, str(job.file_path), str(target_path))

    async def process_file(self, file_path: Path) -> ProcessingJob:
        """Process a single file manually.

        Args:
            file_path: Path to file to process

        Returns:
            ProcessingJob with results
        """
        job = ProcessingJob(
            job_id=f"manual_{datetime.now().strftime('%H%M%S')}",
            file_path=file_path,
            file_size=file_path.stat().st_size if file_path.exists() else 0,
        )
        await self._process_job(job)
        return job
