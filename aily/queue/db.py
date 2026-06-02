import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite


class QueueDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                locked_by TEXT,
                locked_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._ensure_jobs_columns()
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_running_locked ON jobs(status, locked_at)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_ingestion_log (
                id TEXT PRIMARY KEY,
                url_hash TEXT UNIQUE NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_hash ON raw_ingestion_log(url_hash)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_log_created_at ON raw_ingestion_log(created_at)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS note_snapshots (
                id TEXT PRIMARY KEY,
                vault_path TEXT UNIQUE NOT NULL,
                original_markdown TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_path ON note_snapshots(vault_path)"
        )
        await self._db.commit()

    async def _ensure_jobs_columns(self) -> None:
        self._check_db()
        cursor = await self._db.execute("PRAGMA table_info(jobs)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
        if "locked_by" not in columns:
            await self._db.execute("ALTER TABLE jobs ADD COLUMN locked_by TEXT")
        if "locked_at" not in columns:
            await self._db.execute("ALTER TABLE jobs ADD COLUMN locked_at TIMESTAMP")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    def _check_db(self) -> None:
        if self._db is None:
            raise RuntimeError("QueueDB not initialized")

    @staticmethod
    def _hash_url(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    async def insert_raw_log(self, url: str, source: str = "manual") -> Optional[str]:
        self._check_db()
        url_hash = self._hash_url(url)
        log_id = str(uuid.uuid4())
        try:
            await self._db.execute(
                "INSERT INTO raw_ingestion_log (id, url_hash, url, source) VALUES (?, ?, ?, ?)",
                (log_id, url_hash, url, source),
            )
            await self._db.commit()
        except aiosqlite.IntegrityError:
            return None
        return log_id

    async def enqueue(self, job_type: str, payload: dict) -> str:
        self._check_db()
        job_id = str(uuid.uuid4())
        await self._db.execute(
            "INSERT INTO jobs (id, type, payload, status) VALUES (?, ?, ?, ?)",
            (job_id, job_type, json.dumps(payload), "pending"),
        )
        await self._db.commit()
        return job_id

    async def dequeue(self) -> Optional[dict]:
        self._check_db()
        await self._db.execute("BEGIN IMMEDIATE")
        cursor = await self._db.execute(
            "SELECT id, type, payload, retry_count FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
            ("pending",),
        )
        row = await cursor.fetchone()
        if row is None:
            await self._db.commit()
            return None
        job_id, job_type, payload, retry_count = row
        await self._db.execute(
            """
            UPDATE jobs
            SET status = ?, locked_by = ?, locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("running", "primary-worker", job_id),
        )
        await self._db.commit()
        return {
            "id": job_id,
            "type": job_type,
            "payload": json.loads(payload),
            "status": "running",
            "retry_count": retry_count,
        }

    async def complete_job(self, job_id: str, success: bool, error_message: Optional[str] = None) -> None:
        self._check_db()
        status = "completed" if success else "failed"
        await self._db.execute(
            """
            UPDATE jobs
            SET status = ?,
                error_message = CASE WHEN ? IS NULL THEN error_message ELSE ? END,
                locked_by = NULL,
                locked_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, error_message, error_message, job_id),
        )
        await self._db.commit()

    async def get_job(self, job_id: str) -> Optional[dict]:
        self._check_db()
        cursor = await self._db.execute(
            "SELECT id, type, payload, status, retry_count, error_message FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "type": row[1],
            "payload": json.loads(row[2]),
            "status": row[3],
            "retry_count": row[4],
            "error_message": row[5],
        }

    async def retry_job(
        self,
        job_id: str,
        max_retries: int = 3,
        error_message: Optional[str] = None,
    ) -> bool:
        self._check_db()
        cursor = await self._db.execute(
            "SELECT retry_count FROM jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        retry_count = row[0] + 1
        if retry_count >= max_retries:
            await self._db.execute(
                """
                UPDATE jobs
                SET retry_count = ?,
                    status = ?,
                    error_message = COALESCE(?, error_message),
                    locked_by = NULL,
                    locked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (retry_count, "failed", error_message, job_id),
            )
        else:
            await self._db.execute(
                """
                UPDATE jobs
                SET retry_count = ?,
                    status = ?,
                    error_message = COALESCE(?, error_message),
                    locked_by = NULL,
                    locked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (retry_count, "pending", error_message, job_id),
            )
        await self._db.commit()
        return retry_count < max_retries

    @staticmethod
    def _cutoff(hours: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    async def get_raw_logs_within_hours(self, hours: int) -> list[dict]:
        self._check_db()
        cutoff = self._cutoff(hours)
        query = "SELECT id, url_hash, url, source, created_at FROM raw_ingestion_log WHERE created_at >= ?"
        self._db.row_factory = aiosqlite.Row
        cursor = await self._db.execute(query, (cutoff,))
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "url_hash": row["url_hash"],
                "url": row["url"],
                "source": row["source"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def get_urls_for_raw_logs(self, raw_log_ids: list[str]) -> dict[str, str]:
        self._check_db()
        if not raw_log_ids:
            return {}
        placeholders = ",".join("?" for _ in raw_log_ids)
        query = f"SELECT id, url FROM raw_ingestion_log WHERE id IN ({placeholders})"
        cursor = await self._db.execute(query, raw_log_ids)
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def enqueue_url(self, url: str, open_id: str = "", source: str = "manual") -> bool:
        self._check_db()
        url_hash = self._hash_url(url)
        log_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        payload = json.dumps({"url": url, "open_id": open_id})
        try:
            await self._db.execute(
                "INSERT INTO raw_ingestion_log (id, url_hash, url, source) VALUES (?, ?, ?, ?)",
                (log_id, url_hash, url, source),
            )
        except aiosqlite.IntegrityError:
            return False
        await self._db.execute(
            "INSERT INTO jobs (id, type, payload, status) VALUES (?, ?, ?, ?)",
            (job_id, "url_fetch", payload, "pending"),
        )
        await self._db.commit()
        return True

    async def save_note_snapshot(self, vault_path: str, markdown: str) -> None:
        self._check_db()
        snapshot_id = str(uuid.uuid4())
        await self._db.execute(
            "INSERT OR REPLACE INTO note_snapshots (id, vault_path, original_markdown) VALUES (?, ?, ?)",
            (snapshot_id, vault_path, markdown),
        )
        await self._db.commit()

    async def get_note_snapshot(self, vault_path: str) -> Optional[dict]:
        self._check_db()
        self._db.row_factory = aiosqlite.Row
        cursor = await self._db.execute(
            "SELECT id, vault_path, original_markdown, created_at FROM note_snapshots WHERE vault_path = ?",
            (vault_path,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "vault_path": row["vault_path"],
            "original_markdown": row["original_markdown"],
            "created_at": row["created_at"],
        }

    async def requeue_stale_running_jobs(self, *, stale_after_seconds: float) -> int:
        self._check_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(0.0, stale_after_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """
            UPDATE jobs
            SET status = 'pending',
                locked_by = NULL,
                locked_at = NULL,
                error_message = COALESCE(error_message, 'stale worker lock recovered'),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running' AND (locked_at IS NULL OR locked_at <= ?)
            """,
            (cutoff,),
        )
        await self._db.commit()
        return int(cursor.rowcount or 0)

    async def get_job_counts(self) -> dict[str, int]:
        self._check_db()
        cursor = await self._db.execute(
            """
            SELECT status, COUNT(*)
            FROM jobs
            GROUP BY status
            """
        )
        rows = await cursor.fetchall()
        counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
        for status, count in rows:
            counts[str(status)] = int(count)
        counts["total"] = sum(counts.values())
        return counts
