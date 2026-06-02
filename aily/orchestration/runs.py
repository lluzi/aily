from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from aily.orchestration.state import WorkflowKind, WorkflowRunSnapshot, WorkflowStatus


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowRunStore:
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
            CREATE TABLE IF NOT EXISTS workflow_runs (
                workflow_run_id TEXT PRIMARY KEY,
                langgraph_thread_id TEXT NOT NULL UNIQUE,
                workflow_kind TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'queued','running','interrupted','completed','failed','cancelled'
                )),
                current_node TEXT NOT NULL DEFAULT '',
                input_summary TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                last_error TEXT
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status, updated_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_kind ON workflow_runs(workflow_kind, updated_at)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_run_history (
                history_id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                current_node TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}',
                last_error TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_run_history_run ON workflow_run_history(workflow_run_id, created_at)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _check_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("WorkflowRunStore not initialized")
        return self._db

    async def create_run(
        self,
        *,
        workflow_kind: WorkflowKind,
        workflow_run_id: str | None = None,
        langgraph_thread_id: str | None = None,
        input_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowRunSnapshot:
        db = self._check_db()
        run_id = workflow_run_id or f"wf_{uuid.uuid4().hex}"
        thread_id = langgraph_thread_id or run_id
        now = _utc_now()
        await db.execute(
            """
            INSERT INTO workflow_runs (
                workflow_run_id, langgraph_thread_id, workflow_kind, status,
                current_node, input_summary, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, 'queued', '', ?, ?, ?, ?)
            """,
            (
                run_id,
                thread_id,
                workflow_kind,
                input_summary,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        await self._insert_history(
            run_id,
            status="queued",
            current_node="",
            metadata=metadata or {},
            last_error=None,
            created_at=now,
        )
        await db.commit()
        snapshot = await self.get_run(run_id)
        if snapshot is None:
            raise RuntimeError("Workflow run creation failed")
        return snapshot

    async def update_status(
        self,
        workflow_run_id: str,
        *,
        status: WorkflowStatus,
        current_node: str | None = None,
        metadata: dict[str, Any] | None = None,
        last_error: str | None = None,
    ) -> WorkflowRunSnapshot:
        db = self._check_db()
        existing = await self.get_run(workflow_run_id)
        if existing is None:
            raise KeyError(f"Workflow run not found: {workflow_run_id}")
        next_metadata = existing.metadata
        if metadata:
            next_metadata = {**next_metadata, **metadata}
        now = _utc_now()
        completed_at = now if status in {"completed", "failed", "cancelled"} else None
        next_last_error = last_error if last_error is not None else existing.last_error
        if status in {"queued", "running", "interrupted", "completed"} and last_error is None:
            next_last_error = None
        await db.execute(
            """
            UPDATE workflow_runs
            SET status = ?,
                current_node = ?,
                metadata = ?,
                updated_at = ?,
                completed_at = ?,
                last_error = ?
            WHERE workflow_run_id = ?
            """,
            (
                status,
                current_node if current_node is not None else existing.current_node,
                json.dumps(next_metadata, ensure_ascii=False, sort_keys=True),
                now,
                completed_at,
                next_last_error,
                workflow_run_id,
            ),
        )
        await self._insert_history(
            workflow_run_id,
            status=status,
            current_node=current_node if current_node is not None else existing.current_node,
            metadata=next_metadata,
            last_error=next_last_error,
            created_at=now,
        )
        await db.commit()
        updated = await self.get_run(workflow_run_id)
        if updated is None:
            raise RuntimeError("Workflow run update failed")
        return updated

    async def get_run(self, workflow_run_id: str) -> WorkflowRunSnapshot | None:
        db = self._check_db()
        cursor = await db.execute(
            """
            SELECT workflow_run_id, langgraph_thread_id, workflow_kind, status,
                   current_node, input_summary, metadata, created_at, updated_at,
                   completed_at, last_error
            FROM workflow_runs
            WHERE workflow_run_id = ?
            """,
            (workflow_run_id,),
        )
        row = await cursor.fetchone()
        return self._snapshot_from_row(row) if row is not None else None

    async def get_run_by_thread(self, langgraph_thread_id: str) -> WorkflowRunSnapshot | None:
        db = self._check_db()
        cursor = await db.execute(
            """
            SELECT workflow_run_id, langgraph_thread_id, workflow_kind, status,
                   current_node, input_summary, metadata, created_at, updated_at,
                   completed_at, last_error
            FROM workflow_runs
            WHERE langgraph_thread_id = ?
            """,
            (langgraph_thread_id,),
        )
        row = await cursor.fetchone()
        return self._snapshot_from_row(row) if row is not None else None

    async def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: WorkflowStatus | None = None,
    ) -> list[WorkflowRunSnapshot]:
        db = self._check_db()
        safe_limit = max(1, min(500, limit))
        safe_offset = max(0, offset)
        if status is None:
            cursor = await db.execute(
                """
                SELECT workflow_run_id, langgraph_thread_id, workflow_kind, status,
                       current_node, input_summary, metadata, created_at, updated_at,
                       completed_at, last_error
                FROM workflow_runs
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (safe_limit, safe_offset),
            )
        else:
            cursor = await db.execute(
                """
                SELECT workflow_run_id, langgraph_thread_id, workflow_kind, status,
                       current_node, input_summary, metadata, created_at, updated_at,
                       completed_at, last_error
                FROM workflow_runs
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (status, safe_limit, safe_offset),
            )
        return [self._snapshot_from_row(row) for row in await cursor.fetchall()]

    async def list_run_history(self, workflow_run_id: str) -> list[dict[str, Any]]:
        db = self._check_db()
        cursor = await db.execute(
            """
            SELECT history_id, workflow_run_id, status, current_node, metadata,
                   last_error, created_at
            FROM workflow_run_history
            WHERE workflow_run_id = ?
            ORDER BY created_at ASC
            """,
            (workflow_run_id,),
        )
        rows = await cursor.fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            try:
                payload["metadata"] = json.loads(payload.get("metadata") or "{}")
            except json.JSONDecodeError:
                payload["metadata"] = {}
            history.append(payload)
        return history

    async def _insert_history(
        self,
        workflow_run_id: str,
        *,
        status: WorkflowStatus,
        current_node: str,
        metadata: dict[str, Any],
        last_error: str | None,
        created_at: str,
    ) -> None:
        db = self._check_db()
        await db.execute(
            """
            INSERT INTO workflow_run_history (
                history_id, workflow_run_id, status, current_node, metadata,
                last_error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"hist_{uuid.uuid4().hex}",
                workflow_run_id,
                status,
                current_node,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                last_error,
                created_at,
            ),
        )

    @staticmethod
    def _snapshot_from_row(row: aiosqlite.Row) -> WorkflowRunSnapshot:
        return WorkflowRunSnapshot(
            workflow_run_id=str(row["workflow_run_id"]),
            langgraph_thread_id=str(row["langgraph_thread_id"]),
            workflow_kind=str(row["workflow_kind"]),
            status=str(row["status"]),
            current_node=str(row["current_node"] or ""),
            input_summary=str(row["input_summary"] or ""),
            metadata=json.loads(str(row["metadata"] or "{}")),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=row["completed_at"],
            last_error=row["last_error"],
        )
