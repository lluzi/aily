#!/usr/bin/env python3
"""AilyChaos Daemon - Background file processor for Chaos folder.

Watches ~/aily_chaos/ for new files, processes through DIKIWI,
and outputs to Obsidian vault.

Usage:
    # Start daemon
    python3 scripts/run_chaos_daemon.py start

    # Check status
    python3 scripts/run_chaos_daemon.py status

    # Stop daemon
    python3 scripts/run_chaos_daemon.py stop

    # Run once (foreground)
    python3 scripts/run_chaos_daemon.py once
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from aily.chaos.queue_processor import ChaosQueue
from aily.chaos.types import ExtractedContentMultimodal
from aily.config import SETTINGS
from aily.llm.provider_routes import PrimaryLLMRoute

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("aily.chaos.daemon")

# Configuration
CHAOS_FOLDER = Path.home() / "aily_chaos"
VAULT_PATH = Path(SETTINGS.obsidian_vault_path).expanduser() if SETTINGS.obsidian_vault_path else (Path.home() / "Documents/Obsidian Vault")
QUEUE_DB_PATH = CHAOS_FOLDER / ".aily_chaos.db"
PID_FILE = CHAOS_FOLDER / ".daemon.pid"
STATE_FILE = CHAOS_FOLDER / ".daemon.state"


@dataclass
class ImageContext:
    path: Path
    parent: Path
    stem: str
    suffix: str
    mtime: float
    exif_time: datetime | None
    camera_model: str | None
    filename_key: str


def _normalize_filename_key(stem: str) -> str:
    import re

    lowered = stem.lower()
    lowered = re.sub(r"\d+", "#", lowered)
    lowered = re.sub(r"[_\\-.\\s]+", "-", lowered)
    return lowered.strip("-")


class ChaosDaemon:
    """Background daemon for processing Chaos files."""

    def __init__(self) -> None:
        self.queue = ChaosQueue(QUEUE_DB_PATH)
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self._bridge = None
        self._graph_db = None

    async def start(self) -> None:
        """Start the daemon."""
        # Check if already running
        if PID_FILE.exists():
            try:
                with open(PID_FILE) as f:
                    old_pid = int(f.read().strip())
                # Check if process exists
                os.kill(old_pid, 0)
                logger.error(f"Daemon already running (PID {old_pid})")
                return
            except (OSError, ValueError):
                # Process not running, clean up stale pid file
                PID_FILE.unlink()

        # Write PID file
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        # Reset any stuck processing files
        reset_count = self.queue.reset_processing()
        if reset_count:
            logger.info(f"Reset {reset_count} stuck files to pending")

        # Scan existing files
        scanned = self.scan_existing()
        logger.info(f"Scanned {scanned} existing files")

        self.running = True
        logger.info("Chaos Daemon started")

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Main processing loop
        try:
            await self._processing_loop()
        except asyncio.CancelledError:
            logger.info("Processing loop cancelled")
        finally:
            await self._close_dikiwi_runtime()
            self._cleanup()

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _cleanup(self) -> None:
        """Clean up on shutdown."""
        if PID_FILE.exists():
            PID_FILE.unlink()
        logger.info("Chaos Daemon stopped")

    async def _ensure_dikiwi_bridge(self):
        """Initialize the shared DIKIWI runtime once for the daemon."""
        if self._bridge is not None:
            return self._bridge

        from aily.chaos.dikiwi_bridge import ChaosDikiwiBridge
        from aily.graph.db import GraphDB
        from aily.sessions.dikiwi_mind import DikiwiMind
        from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter

        llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
        llm_client = llm_resolver("dikiwi")

        graph_db = GraphDB(db_path=SETTINGS.graph_db_path)
        await graph_db.initialize()

        obsidian_writer = DikiwiObsidianWriter(vault_path=VAULT_PATH)
        dikiwi_mind = DikiwiMind(
            graph_db=graph_db,
            llm_client=llm_client,
            llm_client_resolver=llm_resolver,
            dikiwi_obsidian_writer=obsidian_writer,
        )

        self._graph_db = graph_db
        self._bridge = ChaosDikiwiBridge(dikiwi_mind=dikiwi_mind)
        logger.info("Initialized shared DIKIWI runtime for Chaos daemon")
        return self._bridge

    async def _close_dikiwi_runtime(self) -> None:
        """Close the shared DIKIWI runtime cleanly."""
        if self._graph_db is not None:
            await self._graph_db.close()
            self._graph_db = None
        self._bridge = None

    def stop(self) -> bool:
        """Stop the daemon."""
        if not PID_FILE.exists():
            logger.info("Daemon not running")
            return False

        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent stop signal to daemon (PID {pid})")
            return True
        except (OSError, ValueError) as e:
            logger.error(f"Failed to stop daemon: {e}")
            # Clean up stale pid file
            PID_FILE.unlink(missing_ok=True)
            return False

    def status(self) -> dict:
        """Get daemon status."""
        status = {
            "running": False,
            "pid": None,
            "stats": self.queue.get_stats(),
        }

        if PID_FILE.exists():
            try:
                with open(PID_FILE) as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # Check if process exists
                status["running"] = True
                status["pid"] = pid
            except (OSError, ValueError):
                pass

        return status

    def scan_existing(self) -> int:
        """Scan existing files in chaos folder."""
        if not CHAOS_FOLDER.exists():
            return 0

        count = 0
        extensions = {
            ".pdf": "pdf",
            ".doc": "office",
            ".docx": "office",
            ".ppt": "office",
            ".pptx": "office",
            ".xls": "office",
            ".xlsx": "office",
            ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
            ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image", ".webp": "image",
            ".txt": "text", ".md": "markdown", ".json": "json",
        }

        # Max file sizes (MB)
        max_sizes = {
            "pdf": 50,
            "office": 100,
            "video": 500,
            "image": 20,
            "text": 10,
            "markdown": 10,
            "json": 10,
        }

        processed_folder = CHAOS_FOLDER / ".processed"
        failed_folder = CHAOS_FOLDER / ".failed"

        # Skip these directories
        skip_dirs = {'.processed', '.failed', 'node_modules', '.git', '__pycache__', '.venv', 'venv', '.env'}

        # Skip these file patterns (build artifacts, package files, etc)
        skip_patterns = {'package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
                         '.DS_Store', 'Thumbs.db', '.env', '.env.local'}

        for root, dirs, files in os.walk(CHAOS_FOLDER):
            # Skip unwanted directories
            dirs[:] = [d for d in dirs if d not in skip_dirs]

            for filename in files:
                path = Path(root) / filename

                # Skip files in processed/failed folders
                if str(path).startswith(str(processed_folder)) or \
                   str(path).startswith(str(failed_folder)):
                    continue

                # Skip files matching skip patterns
                if filename in skip_patterns:
                    continue

                ext = path.suffix.lower()
                if ext in extensions:
                    if self._should_skip_auxiliary_artifact(path):
                        continue
                    file_type = extensions[ext]

                    # Check file size
                    try:
                        file_size_mb = path.stat().st_size / (1024 * 1024)
                        max_size = max_sizes.get(file_type, 50)
                        if file_size_mb > max_size:
                            logger.warning(f"Skipping {filename}: {file_size_mb:.1f}MB > {max_size}MB limit")
                            continue
                    except OSError:
                        continue

                    if self.queue.add_file(path, file_type):
                        count += 1

        return count

    def _should_skip_auxiliary_artifact(self, path: Path) -> bool:
        """Skip MinerU sidecars so only the primary markdown enters Chaos."""
        if path.suffix.lower() not in {".json", ".html", ".tex"}:
            return False

        sibling_full_md = path.parent / "full.md"
        if not sibling_full_md.exists():
            return False

        name = path.name.lower()
        if name in {"layout.json", "middle.json", "content_list.json", "model.json", "main.html"}:
            return True
        return name.endswith("_content_list.json") or name.endswith("_model.json")

    async def _processing_loop(self) -> None:
        """Main processing loop."""
        while self.running:
            # Try to claim a file from queue
            file_record = self.queue.claim_next()

            if file_record:
                try:
                    records = await self._expand_image_session([file_record])
                    await self._process_records(records)
                except Exception as e:
                    logger.exception(f"Failed to process {file_record.filename}")
                    for record in records if "records" in locals() else [file_record]:
                        self.queue.mark_failed(record.id, str(e))
                        self.failed_count += 1
            else:
                # No files to process, wait a bit
                await asyncio.sleep(5)

            # Log stats periodically
            if (self.processed_count + self.failed_count) % 10 == 0:
                stats = self.queue.get_stats()
                logger.info(f"Queue stats: {stats}")

    def _save_transcript_to_vault(self, content: ExtractedContentMultimodal, source_path: Path) -> None:
        """Save complete markdown transcript to vault 00-Chaos."""
        try:
            transcript_dir = VAULT_PATH / "00-Chaos"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            base_name = self._chaos_base_name(content, source_path)
            transcript_path = transcript_dir / f"{base_name}.md"

            counter = 1
            while transcript_path.exists():
                transcript_path = transcript_dir / f"{base_name}_{counter}.md"
                counter += 1

            lines = [
                f"# {content.title or base_name}\n",
                f"**Original File:** {source_path.name}\n",
                f"**Type:** {content.source_type}\n",
                f"**Processed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
                "---\n",
                content.get_full_text(),
            ]
            if content.segments:
                lines.append("\n## Segments\n")
                for seg in content.segments:
                    start_m = int(seg.start_time) // 60
                    start_s = int(seg.start_time) % 60
                    end_m = int(seg.end_time) // 60
                    end_s = int(seg.end_time) % 60
                    lines.append(f"**[{start_m}:{start_s:02d} - {end_m}:{end_s:02d}]** {seg.text}\n")

            transcript_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("Saved transcript to vault 00-Chaos: %s", transcript_path.name)
        except Exception as exc:
            logger.warning("Failed to save transcript to vault: %s", exc)

    def _chaos_base_name(self, content: ExtractedContentMultimodal, source_path: Path) -> str:
        """Choose a stable filename for the 00-Chaos transcript."""
        base_name = content.metadata.get("chaos_base_name")
        if isinstance(base_name, str) and base_name.strip():
            return base_name.strip()
        if content.source_type == "mineru_markdown" and source_path.name.lower() == "full.md":
            return source_path.parent.name
        return source_path.stem

    async def _process_records(self, file_records) -> None:
        """Process one file or a grouped image session through DIKIWI."""
        if not file_records:
            return

        if len(file_records) == 1:
            source_path = Path(file_records[0].source_path)
            logger.info(f"Processing: {source_path.name}")
            content = await self._extract_content(file_records[0])
        else:
            source_path = Path(file_records[0].source_path)
            logger.info(
                "Processing grouped image session: %s files (%s)",
                len(file_records),
                ", ".join(Path(record.source_path).name for record in file_records[:4]),
            )
            content = await self._extract_image_session(file_records)

        if not content:
            raise ValueError("Failed to extract content")

        # Save raw transcript to vault 00-Chaos
        self._save_transcript_to_vault(content, source_path)

        content_items = self._split_content_into_jobs(content)
        bridge = await self._ensure_dikiwi_bridge()
        for item in content_items:
            result = await bridge.process_extracted_content(item)
            if "error" in result:
                raise RuntimeError(result["error"])

            logger.info(
                "Completed: %s -> Stage %s, %s zettels",
                item.title or Path(file_records[0].source_path).name,
                result.get("stage", "UNKNOWN"),
                result.get("zettels_created", 0),
            )

        for file_record in file_records:
            self.queue.mark_completed(
                file_record.id,
                output_path=str(CHAOS_FOLDER / ".processed"),
                vault_path=str(VAULT_PATH / "3-Resources" / "Zettelkasten"),
            )
            self.processed_count += 1

    def _split_content_into_jobs(self, content: ExtractedContentMultimodal) -> list[ExtractedContentMultimodal]:
        """Expand one extracted content item into multiple DIKIWI jobs when appropriate."""
        if content.source_type != "url_markdown":
            return [content]

        from aily.chaos.config import ChaosConfig
        from aily.chaos.processors.document import TextProcessor

        processor = TextProcessor(ChaosConfig())
        return processor.split_url_import_items(content)

    async def _extract_content(self, file_record) -> Optional[ExtractedContentMultimodal]:
        """Extract content from file based on type."""
        from aily.chaos.processors.pdf import PDFProcessor
        from aily.chaos.processors.image import ImageProcessor
        from aily.chaos.processors.mineru_processor import MinerUProcessor
        from aily.chaos.processors.video import VideoProcessor
        from aily.chaos.processors.document import TextProcessor
        from aily.chaos.config import ChaosConfig

        source_path = Path(file_record.source_path)

        try:
            # Set timeouts based on file type
            timeouts = {
                "pdf": 60,      # 1 minute for PDFs
                "image": 30,    # 30 seconds for images
                "video": 300,   # 5 minutes for videos
                "text": 5,
                "markdown": 180,
                "json": 5,
            }
            timeout = timeouts.get(file_record.file_type, 60)

            if file_record.file_type == "pdf":
                config = ChaosConfig()
                processor = PDFProcessor(config=config)
                return await asyncio.wait_for(processor.process(source_path), timeout=timeout)

            elif file_record.file_type == "image":
                config = ChaosConfig()
                processor = ImageProcessor(config=config)
                return await asyncio.wait_for(processor.process(source_path), timeout=timeout)

            elif file_record.file_type == "video":
                config = ChaosConfig()
                processor = VideoProcessor(config=config)
                return await asyncio.wait_for(processor.process(source_path), timeout=timeout)

            elif file_record.file_type == "office":
                config = ChaosConfig()
                processor = MinerUProcessor(config=config)
                return await asyncio.wait_for(processor.process(source_path), timeout=timeout)

            elif file_record.file_type in ("text", "markdown"):
                config = ChaosConfig()
                processor = TextProcessor(config=config)
                return await asyncio.wait_for(processor.process(source_path), timeout=timeout)

            elif file_record.file_type == "json":
                with open(source_path, "r", encoding="utf-8") as f:
                    text = f.read()
                return ExtractedContentMultimodal(
                    text=text,
                    title=source_path.stem,
                    source_type="json",
                    source_path=source_path,
                    tags=[file_record.file_type],
                )

            else:
                logger.warning(f"Unknown file type: {file_record.file_type}")
                return None

        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            return None

    async def _expand_image_session(self, claimed_records):
        """Group adjacent image files into one session when evidence suggests they belong together."""
        if len(claimed_records) != 1:
            return claimed_records

        seed = claimed_records[0]
        if getattr(seed, "file_type", "") != "image":
            return claimed_records
        if not hasattr(self.queue, "get_pending_files") or not hasattr(self.queue, "claim_specific"):
            return claimed_records

        seed_path = Path(seed.source_path)
        seed_ctx = self._build_image_context(seed_path)
        if seed_ctx is None:
            return claimed_records

        pending = [record for record in self.queue.get_pending_files() if getattr(record, "file_type", "") == "image"]
        pending = pending[:24]

        grouped_ids = []
        for candidate in pending:
            candidate_path = Path(candidate.source_path)
            candidate_ctx = self._build_image_context(candidate_path)
            if candidate_ctx is None:
                continue
            if self._should_group_images(seed_ctx, candidate_ctx):
                grouped_ids.append(candidate.id)

        if not grouped_ids:
            return claimed_records

        claimed = self.queue.claim_specific(grouped_ids)
        return claimed_records + claimed

    def _build_image_context(self, path: Path) -> ImageContext | None:
        """Collect grouping signals from image metadata."""
        try:
            stat = path.stat()
        except OSError:
            return None

        exif_time = None
        camera_model = None
        try:
            from PIL import Image

            with Image.open(path) as image:
                exif = image.getexif()
                if exif:
                    timestamp = exif.get(36867) or exif.get(306)
                    if timestamp:
                        exif_time = datetime.strptime(str(timestamp), "%Y:%m:%d %H:%M:%S")
                    model = exif.get(272)
                    if model:
                        camera_model = str(model)
        except Exception:
            pass

        return ImageContext(
            path=path,
            parent=path.parent,
            stem=path.stem,
            suffix=path.suffix.lower(),
            mtime=stat.st_mtime,
            exif_time=exif_time,
            camera_model=camera_model,
            filename_key=_normalize_filename_key(path.stem),
        )

    def _should_group_images(self, seed: ImageContext, candidate: ImageContext) -> bool:
        """Heuristic for deciding whether two images belong to one technical session."""
        import re
        from difflib import SequenceMatcher

        if seed.path == candidate.path:
            return False
        if seed.parent != candidate.parent:
            return False

        score = 0

        if seed.exif_time and candidate.exif_time:
            exif_gap = abs((seed.exif_time - candidate.exif_time).total_seconds())
            if exif_gap <= 15 * 60:
                score += 2
            elif exif_gap <= 45 * 60:
                score += 1

        mtime_gap = abs(seed.mtime - candidate.mtime)
        if mtime_gap <= 5 * 60:
            score += 2
        elif mtime_gap <= 20 * 60:
            score += 1

        if seed.camera_model and candidate.camera_model and seed.camera_model == candidate.camera_model:
            score += 1

        if seed.filename_key == candidate.filename_key:
            score += 2
        elif SequenceMatcher(None, seed.filename_key, candidate.filename_key).ratio() >= 0.72:
            score += 1

        seed_nums = [int(num) for num in re.findall(r"\d+", seed.stem)]
        cand_nums = [int(num) for num in re.findall(r"\d+", candidate.stem)]
        if seed_nums and cand_nums and seed.stem.rstrip("0123456789_-") == candidate.stem.rstrip("0123456789_-"):
            if abs(seed_nums[-1] - cand_nums[-1]) <= 3:
                score += 1

        return score >= 3

    async def _extract_image_session(self, file_records) -> Optional[ExtractedContentMultimodal]:
        """Extract a related series of images into one session payload."""
        contents = []
        for record in file_records:
            extracted = await self._extract_content(record)
            if extracted:
                contents.append(extracted)

        if not contents:
            return None

        source_paths = [str(content.source_path) for content in contents if content.source_path]
        visual_elements = [elem for content in contents for elem in content.visual_elements]
        tags = []
        for content in contents:
            tags.extend(content.tags)

        session_title = self._session_title_from_paths([Path(record.source_path) for record in file_records])
        text_parts = [
            f"## Image Session Summary",
            "",
            f"This session contains {len(contents)} related images captured from the same likely technical session.",
        ]

        for index, content in enumerate(contents, start=1):
            source_name = Path(content.source_path).name if content.source_path else f"image-{index}"
            text_parts.extend([
                "",
                f"### Frame {index}: {source_name}",
                "",
                content.text.strip(),
            ])

        return ExtractedContentMultimodal(
            text="\n".join(part for part in text_parts if part is not None).strip(),
            title=session_title,
            source_type="image_session",
            source_path=contents[0].source_path,
            metadata={
                "source_paths": source_paths,
                "session_size": len(contents),
                "grouping_method": "timestamp_exif_filename",
            },
            visual_elements=visual_elements,
            tags=list(dict.fromkeys(["image-session", *tags])),
            processing_method="image_session_group",
        )

    def _session_title_from_paths(self, paths: list[Path]) -> str:
        """Build a readable title for a grouped image session."""
        import os

        stems = [path.stem for path in paths]
        if not stems:
            return "Technical Session Images"
        common = os.path.commonprefix(stems).strip("_- ")
        if common:
            base = common.replace("_", " ").replace("-", " ").strip()
            return f"{base.title()} Session"
        return f"{paths[0].parent.name.replace('_', ' ').replace('-', ' ').title()} Image Session"

    async def run_once(self) -> dict:
        """Run one processing cycle (for manual execution)."""
        self.scan_existing()
        stats_start = self.queue.get_stats()

        # Process up to 5 files
        for _ in range(5):
            file_record = self.queue.claim_next()
            if not file_record:
                break

            try:
                records = await self._expand_image_session([file_record])
                await self._process_records(records)
            except Exception as e:
                logger.exception(f"Failed to process {file_record.filename}")
                for record in records if "records" in locals() else [file_record]:
                    self.queue.mark_failed(record.id, str(e))

        await self._close_dikiwi_runtime()

        stats_end = self.queue.get_stats()
        return {
            "processed": stats_start["pending"] - stats_end["pending"],
            "remaining": stats_end["pending"],
        }


def main():
    parser = argparse.ArgumentParser(description="AilyChaos Daemon")
    parser.add_argument(
        "command",
        choices=["start", "stop", "status", "once"],
        help="Command to execute"
    )
    args = parser.parse_args()

    daemon = ChaosDaemon()

    if args.command == "start":
        logger.info("Starting Chaos Daemon...")
        try:
            asyncio.run(daemon.start())
        except KeyboardInterrupt:
            logger.info("Interrupted by user")

    elif args.command == "stop":
        success = daemon.stop()
        sys.exit(0 if success else 1)

    elif args.command == "status":
        status = daemon.status()
        print(f"Daemon running: {status['running']}")
        if status["pid"]:
            print(f"PID: {status['pid']}")
        print(f"\nQueue Statistics:")
        for key, value in status["stats"].items():
            print(f"  {key}: {value}")

    elif args.command == "once":
        logger.info("Running one cycle...")
        result = asyncio.run(daemon.run_once())
        print(f"Processed: {result['processed']}")
        print(f"Remaining: {result['remaining']}")


if __name__ == "__main__":
    main()
