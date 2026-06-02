from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds))).isoformat()


def _utc_before(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=max(0.0, seconds))).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SourceJobCapacityError(RuntimeError):
    """Raised when durable intake backlog reaches its configured admission cap."""


class SourceStore:
    """Persistent raw source store for uploads, links, and future media inputs."""

    def __init__(self, db_path: Path, object_dir: Path, markdown_dir: Path | None = None) -> None:
        self.db_path = db_path.expanduser()
        self.object_dir = object_dir.expanduser()
        self.markdown_dir = (markdown_dir or (self.object_dir.parent / "markdown_packages")).expanduser()
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.object_dir.mkdir(parents=True, exist_ok=True)
        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                sha256 TEXT UNIQUE,
                normalized_source TEXT NOT NULL,
                storage_path TEXT,
                filename TEXT,
                content_type TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS source_uploads (
                upload_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                filename TEXT,
                content_type TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources(source_id)
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS source_jobs (
                job_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'queued','running','completed','retry_pending','failed','cancelled'
                )),
                priority INTEGER NOT NULL DEFAULT 100,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                locked_by TEXT,
                locked_at TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources(source_id)
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS source_markdown_packages (
                package_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL UNIQUE,
                markdown_sha256 TEXT NOT NULL,
                package_path TEXT NOT NULL,
                title TEXT,
                source_type TEXT NOT NULL DEFAULT 'unknown',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources(source_id)
            )
            """
        )
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status)")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_sources_updated ON sources(updated_at)")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_uploads_source ON source_uploads(source_id)")
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_jobs_claim ON source_jobs(status, available_at, priority, created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_jobs_source ON source_jobs(source_id, status)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_markdown_packages_source ON source_markdown_packages(source_id)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _check_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SourceStore not initialized")
        return self._db

    async def store_upload(
        self,
        *,
        upload_id: str,
        filename: str,
        content_type: str,
        data: bytes,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        digest = _sha256_bytes(data)
        source_id = f"sha256:{digest}"
        object_path = self.object_dir / digest[:2] / digest
        object_path.parent.mkdir(parents=True, exist_ok=True)
        if not object_path.exists():
            object_path.write_bytes(data)

        existing = await self.get_source(source_id)
        now = _utc_now()
        if existing is None:
            await db.execute(
                """
                INSERT INTO sources (
                    source_id, kind, sha256, normalized_source, storage_path,
                    filename, content_type, size_bytes, status, metadata,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    "upload",
                    digest,
                    filename,
                    str(object_path),
                    filename,
                    content_type,
                    len(data),
                    "stored",
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            duplicate = False
            status = "stored"
        else:
            duplicate = True
            status = str(existing["status"])

        await db.execute(
            """
            INSERT OR REPLACE INTO source_uploads (
                upload_id, source_id, filename, content_type, size_bytes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (upload_id, source_id, filename, content_type, len(data), now),
        )
        await db.commit()

        return {
            "source_id": source_id,
            "upload_id": upload_id,
            "sha256": digest,
            "storage_path": str(object_path),
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(data),
            "status": status,
            "duplicate": duplicate,
        }

    async def store_url(
        self,
        *,
        url: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        normalized_url = url.strip()
        digest = _sha256_bytes(normalized_url.encode("utf-8"))
        source_id = f"url:{digest}"
        now = _utc_now()
        existing = await self.get_source(source_id)
        if existing is None:
            await db.execute(
                """
                INSERT INTO sources (
                    source_id, kind, sha256, normalized_source, storage_path,
                    filename, content_type, size_bytes, status, metadata,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    "url",
                    digest,
                    normalized_url,
                    None,
                    None,
                    "text/uri-list",
                    len(normalized_url),
                    "stored",
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            await db.commit()
            duplicate = False
            status = "stored"
        else:
            duplicate = True
            status = str(existing["status"])
        return {
            "source_id": source_id,
            "url": normalized_url,
            "sha256": digest,
            "status": status,
            "duplicate": duplicate,
        }

    async def store_text(
        self,
        *,
        text: str,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("Text source is empty")
        safe_title = title.strip() or "Text Source"
        data = normalized_text.encode("utf-8")
        digest = _sha256_bytes(b"text\x00" + data)
        source_id = f"text:{digest}"
        object_path = self.object_dir / digest[:2] / digest
        object_path.parent.mkdir(parents=True, exist_ok=True)
        if not object_path.exists():
            object_path.write_bytes(data)

        now = _utc_now()
        existing = await self.get_source(source_id)
        if existing is None:
            merged_metadata = {"title": safe_title, **(metadata or {})}
            await db.execute(
                """
                INSERT INTO sources (
                    source_id, kind, sha256, normalized_source, storage_path,
                    filename, content_type, size_bytes, status, metadata,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    "text",
                    digest,
                    normalized_text[:500],
                    str(object_path),
                    f"{safe_title}.txt",
                    "text/plain; charset=utf-8",
                    len(data),
                    "stored",
                    json.dumps(merged_metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            await db.commit()
            duplicate = False
            status = "stored"
        else:
            duplicate = True
            status = str(existing["status"])
        return {
            "source_id": source_id,
            "title": safe_title,
            "sha256": digest,
            "storage_path": str(object_path),
            "filename": f"{safe_title}.txt",
            "content_type": "text/plain; charset=utf-8",
            "size_bytes": len(data),
            "status": status,
            "duplicate": duplicate,
        }

    async def update_status(self, source_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        db = self._check_db()
        existing = await self.get_source(source_id)
        if existing is None:
            return
        merged = dict(existing.get("metadata") or {})
        if metadata:
            merged.update(metadata)
        await db.execute(
            "UPDATE sources SET status = ?, metadata = ?, updated_at = ? WHERE source_id = ?",
            (status, json.dumps(merged, ensure_ascii=False), _utc_now(), source_id),
        )
        await db.commit()

    async def mark_retry_pending(
        self,
        source_id: str,
        *,
        error: str,
        stage: str = "",
        provider: str = "",
        model: str = "",
        pipeline_id: str = "",
        retry_delay_seconds: float = 300.0,
    ) -> None:
        existing = await self.get_source(source_id)
        metadata = dict(existing.get("metadata") or {}) if existing else {}
        attempt_count = int(metadata.get("attempt_count") or 0) + 1
        await self.update_status(
            source_id,
            "retry_pending",
            {
                "attempt_count": attempt_count,
                "last_error": error,
                "last_failed_stage": stage,
                "provider": provider,
                "model": model,
                "pipeline_id": pipeline_id or metadata.get("pipeline_id", ""),
                "next_retry_at": _utc_after(retry_delay_seconds),
            },
        )

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        cursor = await db.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def get_source_for_upload(self, upload_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        cursor = await db.execute(
            """
            SELECT s.*
            FROM source_uploads u
            JOIN sources s ON s.source_id = u.source_id
            WHERE u.upload_id = ?
            """,
            (upload_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_failed_sources(self, *, limit: int = 100) -> list[dict[str, Any]]:
        db = self._check_db()
        safe_limit = min(max(1, limit), 500)
        cursor = await db.execute(
            "SELECT * FROM sources WHERE status IN ('failed', 'failed_retry_exhausted') ORDER BY updated_at DESC LIMIT ?",
            (safe_limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def enqueue_source_job(
        self,
        *,
        source_id: str,
        job_type: str,
        payload: dict[str, Any] | None = None,
        priority: int = 100,
        available_in_seconds: float = 0.0,
        max_pending: int | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        job_id = str(uuid.uuid4())
        await db.execute("BEGIN IMMEDIATE")
        if max_pending is not None and max_pending > 0:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM source_jobs WHERE status IN ('queued','retry_pending','running')"
            )
            pending = int((await cursor.fetchone())[0])
            if pending >= max_pending:
                await db.rollback()
                raise SourceJobCapacityError(
                    f"Source job queue is full: pending={pending}, max_pending={max_pending}"
                )
        await db.execute(
            """
            INSERT INTO source_jobs (
                job_id, source_id, job_type, status, priority, attempt_count,
                available_at, payload, created_at, updated_at
            )
            VALUES (?, ?, ?, 'queued', ?, 0, ?, ?, ?, ?)
            """,
            (
                job_id,
                source_id,
                job_type,
                priority,
                _utc_after(available_in_seconds),
                json.dumps(payload or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()
        return {
            "job_id": job_id,
            "source_id": source_id,
            "job_type": job_type,
            "status": "queued",
        }

    async def claim_next_source_job(self, *, worker_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        now = _utc_now()
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT *
            FROM source_jobs
            WHERE status IN ('queued', 'retry_pending') AND available_at <= ?
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
            """,
            (now,),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return None
        await db.execute(
            """
            UPDATE source_jobs
            SET status = 'running',
                attempt_count = attempt_count + 1,
                locked_by = ?,
                locked_at = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (worker_id, now, now, row["job_id"]),
        )
        await db.commit()
        payload = dict(row)
        payload["status"] = "running"
        payload["attempt_count"] = int(payload.get("attempt_count") or 0) + 1
        try:
            payload["payload"] = json.loads(payload.get("payload") or "{}")
        except json.JSONDecodeError:
            payload["payload"] = {}
        return payload

    async def complete_source_job(self, job_id: str) -> None:
        db = self._check_db()
        now = _utc_now()
        await db.execute(
            """
            UPDATE source_jobs
            SET status = 'completed',
                locked_by = NULL,
                locked_at = NULL,
                updated_at = ?
            WHERE job_id = ?
            """,
            (now, job_id),
        )
        await db.commit()

    async def fail_source_job(self, job_id: str, *, error: str) -> None:
        db = self._check_db()
        now = _utc_now()
        await db.execute(
            """
            UPDATE source_jobs
            SET status = 'failed',
                last_error = ?,
                locked_by = NULL,
                locked_at = NULL,
                updated_at = ?
            WHERE job_id = ?
            """,
            (error, now, job_id),
        )
        await db.commit()

    async def retry_source_job(self, job_id: str, *, error: str, delay_seconds: float) -> None:
        db = self._check_db()
        now = _utc_now()
        await db.execute(
            """
            UPDATE source_jobs
            SET status = 'retry_pending',
                last_error = ?,
                available_at = ?,
                locked_by = NULL,
                locked_at = NULL,
                updated_at = ?
            WHERE job_id = ?
            """,
            (error, _utc_after(delay_seconds), now, job_id),
        )
        await db.commit()

    async def requeue_stale_running_source_jobs(self, *, stale_after_seconds: float) -> int:
        db = self._check_db()
        cutoff = _utc_before(stale_after_seconds)
        now = _utc_now()
        cursor = await db.execute(
            "SELECT source_id FROM source_jobs WHERE status = 'running' AND locked_at <= ?",
            (cutoff,),
        )
        source_ids = sorted({str(row["source_id"]) for row in await cursor.fetchall()})
        cursor = await db.execute(
            """
            UPDATE source_jobs
            SET status = 'retry_pending',
                available_at = ?,
                locked_by = NULL,
                locked_at = NULL,
                last_error = COALESCE(last_error, 'stale worker lock recovered'),
                updated_at = ?
            WHERE status = 'running' AND locked_at <= ?
            """,
            (now, now, cutoff),
        )
        recovered = int(cursor.rowcount or 0)
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            source_cursor = await db.execute(
                f"SELECT source_id, metadata FROM sources WHERE source_id IN ({placeholders})",
                source_ids,
            )
            source_rows = await source_cursor.fetchall()
            for row in source_rows:
                try:
                    metadata = json.loads(row["metadata"] or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                metadata.setdefault("last_error", "stale worker lock recovered")
                await db.execute(
                    """
                    UPDATE sources
                    SET status = 'retry_pending',
                        metadata = ?,
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (json.dumps(metadata, ensure_ascii=False), now, row["source_id"]),
                )
        await db.commit()
        return recovered

    async def cancel_running_source_jobs(self) -> list[str]:
        db = self._check_db()
        cursor = await db.execute(
            "SELECT job_id, source_id FROM source_jobs WHERE status IN ('queued','retry_pending','running')"
        )
        rows = await cursor.fetchall()
        job_ids = [str(row["job_id"]) for row in rows]
        source_ids = sorted({str(row["source_id"]) for row in rows})
        now = _utc_now()
        await db.execute(
            """
            UPDATE source_jobs
            SET status = 'cancelled',
                locked_by = NULL,
                locked_at = NULL,
                updated_at = ?
            WHERE status IN ('queued','retry_pending','running')
            """,
            (now,),
        )
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            await db.execute(
                f"UPDATE sources SET status = 'cancelled', updated_at = ? WHERE source_id IN ({placeholders})",
                (now, *source_ids),
            )
        await db.commit()
        return job_ids

    async def get_source_job_counts(self) -> dict[str, int]:
        db = self._check_db()
        cursor = await db.execute("SELECT status, COUNT(*) FROM source_jobs GROUP BY status")
        rows = await cursor.fetchall()
        counts = {
            "queued": 0,
            "running": 0,
            "completed": 0,
            "retry_pending": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for status, count in rows:
            counts[str(status)] = int(count)
        counts["total"] = sum(counts.values())
        return counts

    async def list_source_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        db = self._check_db()
        safe_limit = min(max(1, limit), 500)
        safe_offset = max(0, offset)
        where = ""
        params: list[Any] = []
        if status:
            where = "WHERE j.status = ?"
            params.append(status)
        total_cursor = await db.execute(
            f"SELECT COUNT(*) FROM source_jobs j {where}",
            params,
        )
        total = int((await total_cursor.fetchone())[0])
        cursor = await db.execute(
            f"""
            SELECT
                j.*,
                s.filename,
                s.normalized_source,
                s.kind AS source_kind,
                s.content_type,
                s.size_bytes,
                s.status AS source_status,
                s.metadata AS source_metadata
            FROM source_jobs j
            LEFT JOIN sources s ON s.source_id = j.source_id
            {where}
            ORDER BY
                CASE j.status
                    WHEN 'running' THEN 0
                    WHEN 'queued' THEN 1
                    WHEN 'retry_pending' THEN 2
                    WHEN 'failed' THEN 3
                    WHEN 'cancelled' THEN 4
                    ELSE 5
                END,
                j.priority ASC,
                j.available_at ASC,
                j.created_at ASC
            LIMIT ? OFFSET ?
            """,
            (*params, safe_limit, safe_offset),
        )
        rows = await cursor.fetchall()
        return {
            "total": total,
            "jobs": [self._job_row_to_dict(row) for row in rows],
        }

    async def read_stored_object(self, source_id: str) -> bytes:
        source = await self.get_source(source_id)
        if source is None:
            raise FileNotFoundError(f"Source not found: {source_id}")
        storage_path = source.get("storage_path")
        if not storage_path:
            raise FileNotFoundError(f"Source has no stored object: {source_id}")
        path = Path(str(storage_path)).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Stored object missing for {source_id}: {path}")
        return path.read_bytes()

    async def store_markdown_package(
        self,
        *,
        source_id: str,
        markdown: str,
        title: str = "",
        source_type: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        source = await self.get_source(source_id)
        if source is None:
            raise FileNotFoundError(f"Source not found: {source_id}")
        normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("Canonical markdown package is empty")
        data = normalized.encode("utf-8")
        digest = _sha256_bytes(data)
        package_key = f"{source_id}\0{digest}".encode("utf-8")
        package_id = f"md:{_sha256_bytes(package_key)}"
        package_path = self.markdown_dir / digest[:2] / f"{digest}.md"
        package_path.parent.mkdir(parents=True, exist_ok=True)
        package_path.write_text(normalized + "\n", encoding="utf-8")
        now = _utc_now()
        await db.execute(
            """
            INSERT INTO source_markdown_packages (
                package_id, source_id, markdown_sha256, package_path, title,
                source_type, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                package_id = excluded.package_id,
                markdown_sha256 = excluded.markdown_sha256,
                package_path = excluded.package_path,
                title = excluded.title,
                source_type = excluded.source_type,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                package_id,
                source_id,
                digest,
                str(package_path),
                title,
                source_type,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.execute(
            """
            UPDATE sources
            SET metadata = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (
                json.dumps(
                    {
                        **dict(source.get("metadata") or {}),
                        "canonical_markdown_package_id": package_id,
                        "canonical_markdown_path": str(package_path),
                        "canonical_markdown_sha256": digest,
                    },
                    ensure_ascii=False,
                ),
                now,
                source_id,
            ),
        )
        await db.commit()
        return {
            "package_id": package_id,
            "source_id": source_id,
            "markdown_sha256": digest,
            "package_path": str(package_path),
            "title": title,
            "source_type": source_type,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

    async def get_markdown_package(self, source_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        cursor = await db.execute(
            "SELECT * FROM source_markdown_packages WHERE source_id = ?",
            (source_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._markdown_package_row_to_dict(row)

    async def read_markdown_package(self, source_id: str) -> str:
        package = await self.get_markdown_package(source_id)
        if package is None:
            raise FileNotFoundError(f"Canonical markdown package not found for {source_id}")
        path = Path(str(package["package_path"])).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Canonical markdown package missing for {source_id}: {path}")
        return path.read_text(encoding="utf-8")

    async def list_sources(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        db = self._check_db()
        safe_limit = min(max(1, limit), 500)
        safe_offset = max(0, offset)
        total_cursor = await db.execute("SELECT COUNT(*) AS count FROM sources")
        total = int((await total_cursor.fetchone())["count"])
        cursor = await db.execute(
            "SELECT * FROM sources ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (safe_limit, safe_offset),
        )
        rows = await cursor.fetchall()
        return {
            "total": total,
            "sources": [self._row_to_dict(row) for row in rows],
        }

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        payload = dict(row)
        try:
            payload["metadata"] = json.loads(payload.get("metadata") or "{}")
        except json.JSONDecodeError:
            payload["metadata"] = {}
        return payload

    @staticmethod
    def _job_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        payload = dict(row)
        try:
            payload["payload"] = json.loads(payload.get("payload") or "{}")
        except json.JSONDecodeError:
            payload["payload"] = {}
        try:
            payload["source_metadata"] = json.loads(payload.get("source_metadata") or "{}")
        except json.JSONDecodeError:
            payload["source_metadata"] = {}
        return payload

    @staticmethod
    def _markdown_package_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        payload = dict(row)
        try:
            payload["metadata"] = json.loads(payload.get("metadata") or "{}")
        except json.JSONDecodeError:
            payload["metadata"] = {}
        return payload
