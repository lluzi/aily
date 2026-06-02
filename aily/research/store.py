from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_load(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ResearchStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser()
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS research_jobs (
                research_id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                topic_extraction_id TEXT NOT NULL DEFAULT '',
                topic TEXT NOT NULL,
                trigger TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
                query TEXT NOT NULL,
                quota_checked_at TEXT,
                quota_allowed INTEGER NOT NULL DEFAULT 0,
                packet TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS second_opinion_references (
                second_opinion_id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                document_type TEXT NOT NULL DEFAULT 'other',
                authority TEXT NOT NULL DEFAULT 'external_user_provided_non_authoritative',
                note TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS second_opinion_packets (
                second_opinion_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                attached_to TEXT NOT NULL,
                document_type TEXT NOT NULL,
                stance TEXT NOT NULL,
                packet TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(second_opinion_id) REFERENCES second_opinion_references(second_opinion_id)
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_jobs_workflow ON research_jobs(workflow_run_id, created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_jobs_created ON research_jobs(created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_second_opinion_workflow ON second_opinion_references(workflow_run_id, created_at)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _check_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ResearchStore not initialized")
        return self._db

    async def count_research_jobs_since(self, since_iso: str) -> int:
        db = self._check_db()
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM research_jobs WHERE created_at >= ?",
                (since_iso,),
            )
        ).fetchone()
        return int(row[0]) if row else 0

    async def create_research_job(
        self,
        *,
        workflow_run_id: str,
        topic: str,
        trigger: str,
        model: str,
        query: str,
        topic_extraction_id: str = "",
        quota_allowed: bool = False,
        quota_checked_at: str | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        research_id = _new_id("research")
        await db.execute(
            """
            INSERT INTO research_jobs (
                research_id, workflow_run_id, topic_extraction_id, topic, trigger,
                model, status, query, quota_checked_at, quota_allowed,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                research_id,
                workflow_run_id,
                topic_extraction_id,
                topic,
                trigger,
                model,
                query,
                quota_checked_at,
                1 if quota_allowed else 0,
                now,
                now,
            ),
        )
        await db.commit()
        job = await self.get_research_job(research_id)
        if job is None:
            raise RuntimeError("Research job creation failed")
        return job

    async def mark_research_running(self, research_id: str) -> dict[str, Any]:
        return await self._update_research(research_id, status="running")

    async def complete_research_job(self, research_id: str, packet: dict[str, Any]) -> dict[str, Any]:
        return await self._update_research(
            research_id,
            status="completed",
            packet=packet,
            completed_at=_utc_now(),
        )

    async def fail_research_job(self, research_id: str, error: str) -> dict[str, Any]:
        return await self._update_research(research_id, status="failed", error=error)

    async def _update_research(
        self,
        research_id: str,
        *,
        status: str,
        packet: dict[str, Any] | None = None,
        error: str | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        existing = await self.get_research_job(research_id)
        if existing is None:
            raise KeyError(f"Research job not found: {research_id}")
        now = _utc_now()
        await db.execute(
            """
            UPDATE research_jobs
            SET status = ?, packet = ?, error = ?, updated_at = ?, completed_at = ?
            WHERE research_id = ?
            """,
            (
                status,
                _json_dump(packet if packet is not None else existing.get("packet", {})),
                error,
                now,
                completed_at if completed_at is not None else existing.get("completed_at"),
                research_id,
            ),
        )
        await db.commit()
        updated = await self.get_research_job(research_id)
        if updated is None:
            raise RuntimeError("Research job update failed")
        return updated

    async def get_research_job(self, research_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT research_id, workflow_run_id, topic_extraction_id, topic,
                       trigger, model, status, query, quota_checked_at,
                       quota_allowed, packet, error, created_at, updated_at,
                       completed_at
                FROM research_jobs
                WHERE research_id = ?
                """,
                (research_id,),
            )
        ).fetchone()
        return self._research_from_row(row) if row is not None else None

    async def list_research_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        db = self._check_db()
        rows = await (
            await db.execute(
                """
                SELECT research_id, workflow_run_id, topic_extraction_id, topic,
                       trigger, model, status, query, quota_checked_at,
                       quota_allowed, packet, error, created_at, updated_at,
                       completed_at
                FROM research_jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(500, limit)),),
            )
        ).fetchall()
        return [self._research_from_row(row) for row in rows]

    async def create_second_opinion_reference(
        self,
        *,
        workflow_run_id: str,
        source_id: str,
        document_type: str = "other",
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        second_opinion_id = _new_id("secondop")
        await db.execute(
            """
            INSERT INTO second_opinion_references (
                second_opinion_id, workflow_run_id, source_id, document_type,
                authority, note, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'external_user_provided_non_authoritative', ?, ?, ?, ?)
            """,
            (
                second_opinion_id,
                workflow_run_id,
                source_id,
                document_type,
                note,
                _json_dump(metadata or {}),
                now,
                now,
            ),
        )
        await db.commit()
        reference = await self.get_second_opinion_reference(second_opinion_id)
        if reference is None:
            raise RuntimeError("Second-opinion reference creation failed")
        return reference

    async def create_second_opinion_packet(
        self,
        *,
        second_opinion_id: str,
        source_id: str,
        attached_to: str,
        document_type: str,
        packet: dict[str, Any],
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        await db.execute(
            """
            INSERT OR REPLACE INTO second_opinion_packets (
                second_opinion_id, source_id, attached_to, document_type,
                stance, packet, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                second_opinion_id,
                source_id,
                attached_to,
                document_type,
                str(packet.get("stance") or "unknown"),
                _json_dump(packet),
                now,
                now,
            ),
        )
        await db.commit()
        stored = await self.get_second_opinion_packet(second_opinion_id)
        if stored is None:
            raise RuntimeError("Second-opinion packet creation failed")
        return stored

    async def get_second_opinion_reference(self, second_opinion_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT second_opinion_id, workflow_run_id, source_id, document_type,
                       authority, note, metadata, created_at, updated_at
                FROM second_opinion_references
                WHERE second_opinion_id = ?
                """,
                (second_opinion_id,),
            )
        ).fetchone()
        if row is None:
            return None
        return {
            "second_opinion_id": row["second_opinion_id"],
            "workflow_run_id": row["workflow_run_id"],
            "source_id": row["source_id"],
            "document_type": row["document_type"],
            "authority": row["authority"],
            "note": row["note"],
            "metadata": _json_load(row["metadata"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def get_second_opinion_packet(self, second_opinion_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT second_opinion_id, source_id, attached_to, document_type,
                       stance, packet, created_at, updated_at
                FROM second_opinion_packets
                WHERE second_opinion_id = ?
                """,
                (second_opinion_id,),
            )
        ).fetchone()
        if row is None:
            return None
        payload = _json_load(row["packet"], {})
        return {
            "second_opinion_id": row["second_opinion_id"],
            "source_id": row["source_id"],
            "attached_to": row["attached_to"],
            "document_type": row["document_type"],
            "stance": row["stance"],
            "packet": payload,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def list_second_opinions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        db = self._check_db()
        rows = await (
            await db.execute(
                """
                SELECT second_opinion_id, workflow_run_id, source_id, document_type,
                       authority, note, metadata, created_at, updated_at
                FROM second_opinion_references
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(500, limit)),),
            )
        ).fetchall()
        return [
            {
                "second_opinion_id": row["second_opinion_id"],
                "workflow_run_id": row["workflow_run_id"],
                "source_id": row["source_id"],
                "document_type": row["document_type"],
                "authority": row["authority"],
                "note": row["note"],
                "metadata": _json_load(row["metadata"], {}),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _research_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "research_id": row["research_id"],
            "workflow_run_id": row["workflow_run_id"],
            "topic_extraction_id": row["topic_extraction_id"],
            "topic": row["topic"],
            "trigger": row["trigger"],
            "model": row["model"],
            "status": row["status"],
            "query": row["query"],
            "quota_checked_at": row["quota_checked_at"],
            "quota_allowed": bool(row["quota_allowed"]),
            "packet": _json_load(row["packet"], {}),
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }
