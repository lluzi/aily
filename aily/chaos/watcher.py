"""File system watcher for Aily Chaos.

Monitors the chaos folder for new files and queues them for processing.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Coroutine

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from aily.chaos.config import ChaosConfig
from aily.chaos.types import ProcessingJob, ProcessingStatus

logger = logging.getLogger(__name__)


class ChaosEventHandler(FileSystemEventHandler):
    """Handles file system events for the chaos folder."""

    def __init__(
        self,
        job_queue: asyncio.Queue[ProcessingJob],
        config: ChaosConfig,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.job_queue = job_queue
        self.config = config
        self.loop = loop
        self._pending_files: dict[Path, float] = {}  # path -> detection time
        self._processed_files: set[Path] = set()
        self._debounce_task: asyncio.Task | None = None

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation event."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Skip hidden files and temp files
        if file_path.name.startswith(".") or file_path.suffix in {".tmp", ".crdownload", ".part"}:
            return

        # Skip files in subdirectories (process root only)
        if file_path.parent != self.config.watch_folder:
            return

        # Skip already processed
        if file_path in self._processed_files:
            return

        logger.info("Detected new file: %s", file_path)
        self._pending_files[file_path] = time.time()

        # Start or reset debounce timer
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        self._debounce_task = self.loop.create_task(self._process_pending_files())

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification (for detecting write completion)."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Only track if already pending
        if file_path in self._pending_files:
            logger.debug("File modified (still writing): %s", file_path)
            self._pending_files[file_path] = time.time()

    async def _process_pending_files(self) -> None:
        """Process pending files after debounce period."""
        await asyncio.sleep(self.config.debounce_seconds)

        now = time.time()
        ready_files: list[Path] = []

        for file_path, detection_time in list(self._pending_files.items()):
            # Check if file is stable (no recent modifications)
            if now - detection_time >= self.config.debounce_seconds:
                ready_files.append(file_path)

        for file_path in ready_files:
            del self._pending_files[file_path]
            self._processed_files.add(file_path)

            # Create processing job
            job = ProcessingJob(
                job_id=str(uuid.uuid4())[:8],
                file_path=file_path,
                status=ProcessingStatus.PENDING,
                file_size=file_path.stat().st_size if file_path.exists() else 0,
            )

            await self.job_queue.put(job)
            logger.info("Queued job %s for %s", job.job_id, file_path.name)


class FileWatcher:
    """Watches the chaos folder for new files.

    Usage:
        watcher = FileWatcher(config)
        await watcher.start()
        # Files are automatically queued for processing
        await watcher.stop()
    """

    def __init__(self, config: ChaosConfig | None = None) -> None:
        self.config = config or ChaosConfig()
        self.job_queue: asyncio.Queue[ProcessingJob] = asyncio.Queue()
        self._observer: Observer | None = None
        self._handler: ChaosEventHandler | None = None
        self._running = False

    async def start(self) -> None:
        """Start watching the chaos folder."""
        if self._running:
            return

        # Ensure folders exist
        self.config.watch_folder.mkdir(parents=True, exist_ok=True)
        self.config.processed_folder.mkdir(parents=True, exist_ok=True)
        self.config.failed_folder.mkdir(parents=True, exist_ok=True)

        # Set up event handler
        loop = asyncio.get_event_loop()
        self._handler = ChaosEventHandler(
            job_queue=self.job_queue,
            config=self.config,
            loop=loop,
        )

        # Start observer
        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(self.config.watch_folder),
            recursive=False,  # Only watch root level
        )
        self._observer.start()

        self._running = True
        logger.info(
            "FileWatcher started watching %s",
            self.config.watch_folder,
        )

    async def stop(self) -> None:
        """Stop watching."""
        if not self._running:
            return

        if self._observer:
            self._observer.stop()
            self._observer.join()

        self._running = False
        logger.info("FileWatcher stopped")

    async def get_job(self) -> ProcessingJob | None:
        """Get next job from queue (non-blocking)."""
        try:
            return self.job_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def __aiter__(self):
        """Allow async iteration over jobs."""
        return self

    async def __anext__(self) -> ProcessingJob:
        """Get next job (blocking)."""
        return await self.job_queue.get()
