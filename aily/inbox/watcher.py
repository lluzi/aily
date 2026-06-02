from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any

from aily.source_store import SourceJobCapacityError, SourceStore

logger = logging.getLogger(__name__)

EventEmitter = Callable[..., Awaitable[None]]

_SKIP_SUFFIXES = {".tmp", ".crdownload", ".part", ".download"}
_URL_POINTER_SUFFIXES = {".url", ".uri", ".link", ".webloc"}
_REENQUEUE_STATUSES = {
    "stored",
    "deferred",
    "failed",
    "failed_retry_exhausted",
    "cancelled",
    "retry_pending",
}
_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


@dataclass(frozen=True)
class InboxIntakeResult:
    source_id: str
    origin_path: Path
    source_type: str
    duplicate: bool
    queued: bool
    job_id: str | None = None
    error: str | None = None


class WatchedInboxService:
    """Poll a local inbox directory and register new files in SourceStore."""

    def __init__(
        self,
        *,
        source_store: SourceStore,
        inbox_path: Path,
        poll_interval_seconds: float = 5.0,
        file_stable_seconds: float = 2.0,
        max_pending_jobs: int = 500,
        emit_event: EventEmitter | None = None,
    ) -> None:
        self.source_store = source_store
        self.inbox_path = inbox_path.expanduser()
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.file_stable_seconds = max(0.0, float(file_stable_seconds))
        self.max_pending_jobs = max_pending_jobs
        self.emit_event = emit_event
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._seen_signatures: dict[Path, tuple[int, int]] = {}
        self.last_scan_at: float | None = None
        self.last_error: str = ""
        self.total_registered = 0
        self.total_queued = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "inbox_path": str(self.inbox_path),
            "poll_interval_seconds": self.poll_interval_seconds,
            "file_stable_seconds": self.file_stable_seconds,
            "last_scan_at": self.last_scan_at,
            "last_error": self.last_error,
            "total_registered": self.total_registered,
            "total_queued": self.total_queued,
        }

    async def start(self) -> None:
        if self.running:
            return
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="aily-inbox-watcher")
        await self._emit(
            "inbox_watcher_started",
            inbox_path=str(self.inbox_path),
            poll_interval_seconds=self.poll_interval_seconds,
        )
        logger.info("Watched inbox started: %s", self.inbox_path)

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop_event = None
        await self._emit("inbox_watcher_stopped", inbox_path=str(self.inbox_path))
        logger.info("Watched inbox stopped")

    async def scan_once(self) -> list[InboxIntakeResult]:
        self.inbox_path.mkdir(parents=True, exist_ok=True)
        self.last_scan_at = time()
        results: list[InboxIntakeResult] = []
        for path in sorted(self.inbox_path.iterdir()):
            if not self._is_candidate(path):
                continue
            signature = self._signature(path)
            if signature is None or self._seen_signatures.get(path) == signature:
                continue
            if not self._is_stable(path):
                continue
            result = await self._register_path(path)
            results.append(result)
            if result.error is None:
                self._seen_signatures[path] = signature
                self.total_registered += 1
                if result.queued:
                    self.total_queued += 1
        return results

    async def _run(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.scan_once()
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Watched inbox scan failed")
                await self._emit("inbox_scan_failed", inbox_path=str(self.inbox_path), error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue

    def _is_candidate(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.name.startswith("."):
            return False
        return path.suffix.lower() not in _SKIP_SUFFIXES

    def _signature(self, path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return int(stat.st_size), int(stat.st_mtime_ns)

    def _is_stable(self, path: Path) -> bool:
        try:
            return time() - path.stat().st_mtime >= self.file_stable_seconds
        except FileNotFoundError:
            return False

    async def _register_path(self, path: Path) -> InboxIntakeResult:
        try:
            url = self._extract_url_pointer(path)
            if url:
                return await self._register_url_pointer(path, url)
            return await self._register_file(path)
        except SourceJobCapacityError as exc:
            await self._emit("inbox_source_deferred", origin_path=str(path), error=str(exc))
            return InboxIntakeResult(
                source_id="",
                origin_path=path,
                source_type="unknown",
                duplicate=False,
                queued=False,
                error=str(exc),
            )
        except Exception as exc:
            logger.exception("Failed to register inbox path: %s", path)
            await self._emit("inbox_source_failed", origin_path=str(path), error=str(exc))
            return InboxIntakeResult(
                source_id="",
                origin_path=path,
                source_type="unknown",
                duplicate=False,
                queued=False,
                error=str(exc),
            )

    async def _register_file(self, path: Path) -> InboxIntakeResult:
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        upload_id = f"inbox-{uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve()))}"
        source = await self.source_store.store_upload(
            upload_id=upload_id,
            filename=path.name,
            content_type=content_type,
            data=data,
            metadata={
                "intake": "watched_inbox",
                "origin_path": str(path),
                "origin_name": path.name,
            },
        )
        return await self._queue_if_needed(
            source=source,
            origin_path=path,
            source_type="file",
            job_type="process_upload_source",
            payload={
                "upload_id": upload_id,
                "filename": path.name,
                "content_type": content_type,
                "origin_path": str(path),
                "source_kind": "inbox_file",
            },
        )

    async def _register_url_pointer(self, path: Path, url: str) -> InboxIntakeResult:
        source = await self.source_store.store_url(
            url=url,
            metadata={
                "intake": "watched_inbox",
                "origin_path": str(path),
                "origin_name": path.name,
                "source_kind": "url_pointer",
            },
        )
        return await self._queue_if_needed(
            source=source,
            origin_path=path,
            source_type="url",
            job_type="process_url_source",
            payload={
                "upload_id": f"inbox-url-{source['source_id']}",
                "url": url,
                "origin_path": str(path),
                "source_kind": "url_pointer",
            },
        )

    async def _queue_if_needed(
        self,
        *,
        source: dict[str, Any],
        origin_path: Path,
        source_type: str,
        job_type: str,
        payload: dict[str, Any],
    ) -> InboxIntakeResult:
        source_id = str(source["source_id"])
        status = str(source.get("status") or "")
        should_enqueue = (not source.get("duplicate")) or status in _REENQUEUE_STATUSES
        job: dict[str, Any] | None = None
        if should_enqueue:
            await self.source_store.update_status(
                source_id,
                "queued",
                {
                    "intake": "watched_inbox",
                    "origin_path": str(origin_path),
                    "job_type": job_type,
                },
            )
            job = await self.source_store.enqueue_source_job(
                source_id=source_id,
                job_type=job_type,
                payload=payload,
                max_pending=self.max_pending_jobs,
            )
            await self._emit(
                "inbox_source_queued",
                source_id=source_id,
                source_type=source_type,
                origin_path=str(origin_path),
                job_id=job["job_id"],
                job_type=job["job_type"],
                duplicate=bool(source.get("duplicate")),
            )
        else:
            await self._emit(
                "inbox_source_seen",
                source_id=source_id,
                source_type=source_type,
                origin_path=str(origin_path),
                duplicate=True,
                status=status,
            )
        return InboxIntakeResult(
            source_id=source_id,
            origin_path=origin_path,
            source_type=source_type,
            duplicate=bool(source.get("duplicate")),
            queued=should_enqueue,
            job_id=job["job_id"] if job else None,
        )

    def _extract_url_pointer(self, path: Path) -> str | None:
        if path.suffix.lower() not in _URL_POINTER_SUFFIXES:
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";", "[")):
                continue
            if stripped.lower().startswith("url="):
                stripped = stripped.split("=", 1)[1].strip()
            match = _URL_PATTERN.search(stripped)
            if match:
                return match.group(0).rstrip(".,)")
        return None

    async def _emit(self, event_type: str, **payload: Any) -> None:
        if self.emit_event is None:
            return
        try:
            await self.emit_event(event_type, **payload)
        except Exception:
            logger.debug("Inbox event emission failed: %s", event_type, exc_info=True)
