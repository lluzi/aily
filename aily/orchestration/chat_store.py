from __future__ import annotations

import json
import re
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


class ChatStore:
    """Durable chat, topic, and workflow-plan records for V1 orchestration."""

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
            CREATE TABLE IF NOT EXISTS chat_threads (
                chat_thread_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                message_id TEXT PRIMARY KEY,
                chat_thread_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(chat_thread_id) REFERENCES chat_threads(chat_thread_id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_extractions (
                topic_extraction_id TEXT PRIMARY KEY,
                chat_thread_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                motive TEXT NOT NULL,
                topics TEXT NOT NULL DEFAULT '[]',
                knowledge_context TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(chat_thread_id) REFERENCES chat_threads(chat_thread_id) ON DELETE CASCADE,
                FOREIGN KEY(message_id) REFERENCES chat_messages(message_id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_plans (
                workflow_plan_id TEXT PRIMARY KEY,
                chat_thread_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                topic_extraction_id TEXT NOT NULL,
                plan_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'draft','awaiting_confirmation','approved','rejected','superseded','dispatched'
                )),
                motive TEXT NOT NULL,
                topics TEXT NOT NULL DEFAULT '[]',
                knowledge_context TEXT NOT NULL DEFAULT '[]',
                proposed_steps TEXT NOT NULL DEFAULT '[]',
                approvals TEXT NOT NULL DEFAULT '{}',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confirmed_at TEXT,
                FOREIGN KEY(chat_thread_id) REFERENCES chat_threads(chat_thread_id) ON DELETE CASCADE,
                FOREIGN KEY(message_id) REFERENCES chat_messages(message_id) ON DELETE CASCADE,
                FOREIGN KEY(topic_extraction_id) REFERENCES topic_extractions(topic_extraction_id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_threads_updated ON chat_threads(updated_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(chat_thread_id, created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_topic_extractions_thread ON topic_extractions(chat_thread_id, created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_plans_thread ON workflow_plans(chat_thread_id, updated_at)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _check_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ChatStore not initialized")
        return self._db

    async def create_thread(self, *, title: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        thread_id = _new_id("chat")
        await db.execute(
            """
            INSERT INTO chat_threads (chat_thread_id, title, status, metadata, created_at, updated_at)
            VALUES (?, ?, 'open', ?, ?, ?)
            """,
            (thread_id, title.strip()[:160], _json_dump(metadata or {}), now, now),
        )
        await db.commit()
        thread = await self.get_thread(thread_id)
        if thread is None:
            raise RuntimeError("Chat thread creation failed")
        return thread

    async def list_threads(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        db = self._check_db()
        safe_limit = min(max(1, limit), 200)
        safe_offset = max(0, offset)
        count_cursor = await db.execute("SELECT COUNT(*) FROM chat_threads")
        total_row = await count_cursor.fetchone()
        rows = await (
            await db.execute(
                """
                SELECT chat_thread_id, title, status, metadata, created_at, updated_at
                FROM chat_threads
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (safe_limit, safe_offset),
            )
        ).fetchall()
        return {
            "total": int(total_row[0]) if total_row else 0,
            "threads": [self._thread_from_row(row) for row in rows],
        }

    async def get_thread(self, chat_thread_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT chat_thread_id, title, status, metadata, created_at, updated_at
                FROM chat_threads
                WHERE chat_thread_id = ?
                """,
                (chat_thread_id,),
            )
        ).fetchone()
        if row is None:
            return None
        thread = self._thread_from_row(row)
        thread["messages"] = await self.list_messages(chat_thread_id)
        thread["workflow_plans"] = await self.list_workflow_plans(chat_thread_id)
        return thread

    async def add_message(
        self,
        chat_thread_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        thread = await self.get_thread(chat_thread_id)
        if thread is None:
            raise KeyError(f"Chat thread not found: {chat_thread_id}")
        now = _utc_now()
        message_id = _new_id("msg")
        await db.execute(
            """
            INSERT INTO chat_messages (message_id, chat_thread_id, role, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, chat_thread_id, role, content, _json_dump(metadata or {}), now),
        )
        if not thread.get("title") and role == "user":
            title = _title_from_message(content)
            await db.execute(
                "UPDATE chat_threads SET title = ?, updated_at = ? WHERE chat_thread_id = ?",
                (title, now, chat_thread_id),
            )
        else:
            await db.execute(
                "UPDATE chat_threads SET updated_at = ? WHERE chat_thread_id = ?",
                (now, chat_thread_id),
            )
        await db.commit()
        message = await self.get_message(message_id)
        if message is None:
            raise RuntimeError("Chat message creation failed")
        return message

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT message_id, chat_thread_id, role, content, metadata, created_at
                FROM chat_messages
                WHERE message_id = ?
                """,
                (message_id,),
            )
        ).fetchone()
        return self._message_from_row(row) if row is not None else None

    async def list_messages(self, chat_thread_id: str) -> list[dict[str, Any]]:
        db = self._check_db()
        rows = await (
            await db.execute(
                """
                SELECT message_id, chat_thread_id, role, content, metadata, created_at
                FROM chat_messages
                WHERE chat_thread_id = ?
                ORDER BY created_at ASC
                """,
                (chat_thread_id,),
            )
        ).fetchall()
        return [self._message_from_row(row) for row in rows]

    async def create_topic_extraction(
        self,
        *,
        chat_thread_id: str,
        message_id: str,
        motive: str,
        topics: list[dict[str, Any]],
        knowledge_context: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        topic_extraction_id = _new_id("topic")
        await db.execute(
            """
            INSERT INTO topic_extractions (
                topic_extraction_id, chat_thread_id, message_id, motive, topics,
                knowledge_context, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_extraction_id,
                chat_thread_id,
                message_id,
                motive,
                _json_dump(topics),
                _json_dump(knowledge_context),
                _json_dump(metadata or {}),
                now,
            ),
        )
        await db.commit()
        return {
            "topic_extraction_id": topic_extraction_id,
            "chat_thread_id": chat_thread_id,
            "message_id": message_id,
            "motive": motive,
            "topics": topics,
            "knowledge_context": knowledge_context,
            "metadata": metadata or {},
            "created_at": now,
        }

    async def get_topic_extraction(self, topic_extraction_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT topic_extraction_id, chat_thread_id, message_id, motive,
                       topics, knowledge_context, metadata, created_at
                FROM topic_extractions
                WHERE topic_extraction_id = ?
                """,
                (topic_extraction_id,),
            )
        ).fetchone()
        if row is None:
            return None
        return {
            "topic_extraction_id": row["topic_extraction_id"],
            "chat_thread_id": row["chat_thread_id"],
            "message_id": row["message_id"],
            "motive": row["motive"],
            "topics": _json_load(row["topics"], []),
            "knowledge_context": _json_load(row["knowledge_context"], []),
            "metadata": _json_load(row["metadata"], {}),
            "created_at": row["created_at"],
        }

    async def create_workflow_plan(
        self,
        *,
        chat_thread_id: str,
        message_id: str,
        topic_extraction_id: str,
        plan_type: str,
        motive: str,
        topics: list[dict[str, Any]],
        knowledge_context: list[dict[str, Any]],
        proposed_steps: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        workflow_plan_id = _new_id("plan")
        approvals = {
            "workflow_plan": "pending",
            "expensive_or_external_actions": "requires_explicit_confirmation",
        }
        await db.execute(
            """
            INSERT INTO workflow_plans (
                workflow_plan_id, chat_thread_id, message_id, topic_extraction_id,
                plan_type, status, motive, topics, knowledge_context, proposed_steps,
                approvals, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'awaiting_confirmation', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_plan_id,
                chat_thread_id,
                message_id,
                topic_extraction_id,
                plan_type,
                motive,
                _json_dump(topics),
                _json_dump(knowledge_context),
                _json_dump(proposed_steps),
                _json_dump(approvals),
                _json_dump(metadata or {}),
                now,
                now,
            ),
        )
        await db.execute(
            "UPDATE chat_threads SET updated_at = ? WHERE chat_thread_id = ?",
            (now, chat_thread_id),
        )
        await db.commit()
        plan = await self.get_workflow_plan(workflow_plan_id)
        if plan is None:
            raise RuntimeError("Workflow plan creation failed")
        return plan

    async def get_workflow_plan(self, workflow_plan_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT workflow_plan_id, chat_thread_id, message_id, topic_extraction_id,
                       plan_type, status, motive, topics, knowledge_context, proposed_steps,
                       approvals, metadata, created_at, updated_at, confirmed_at
                FROM workflow_plans
                WHERE workflow_plan_id = ?
                """,
                (workflow_plan_id,),
            )
        ).fetchone()
        return self._workflow_plan_from_row(row) if row is not None else None

    async def list_workflow_plans(self, chat_thread_id: str) -> list[dict[str, Any]]:
        db = self._check_db()
        rows = await (
            await db.execute(
                """
                SELECT workflow_plan_id, chat_thread_id, message_id, topic_extraction_id,
                       plan_type, status, motive, topics, knowledge_context, proposed_steps,
                       approvals, metadata, created_at, updated_at, confirmed_at
                FROM workflow_plans
                WHERE chat_thread_id = ?
                ORDER BY updated_at DESC
                """,
                (chat_thread_id,),
            )
        ).fetchall()
        return [self._workflow_plan_from_row(row) for row in rows]

    async def set_workflow_plan_decision(
        self,
        workflow_plan_id: str,
        *,
        approved: bool,
        decided_by: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = self._check_db()
        existing = await self.get_workflow_plan(workflow_plan_id)
        if existing is None:
            raise KeyError(f"Workflow plan not found: {workflow_plan_id}")
        if existing["status"] not in {"awaiting_confirmation", "approved", "rejected"}:
            raise ValueError(f"Workflow plan is not confirmable from status {existing['status']}")
        now = _utc_now()
        status = "approved" if approved else "rejected"
        approvals = {
            **existing.get("approvals", {}),
            "workflow_plan": status,
            "decided_by": decided_by,
            "decided_at": now,
        }
        next_metadata = {**existing.get("metadata", {}), **(metadata or {})}
        await db.execute(
            """
            UPDATE workflow_plans
            SET status = ?, approvals = ?, metadata = ?, updated_at = ?, confirmed_at = ?
            WHERE workflow_plan_id = ?
            """,
            (status, _json_dump(approvals), _json_dump(next_metadata), now, now, workflow_plan_id),
        )
        await db.execute(
            "UPDATE chat_threads SET updated_at = ? WHERE chat_thread_id = ?",
            (now, existing["chat_thread_id"]),
        )
        await db.commit()
        updated = await self.get_workflow_plan(workflow_plan_id)
        if updated is None:
            raise RuntimeError("Workflow plan decision failed")
        return updated

    async def mark_workflow_plan_dispatched(
        self,
        workflow_plan_id: str,
        *,
        workflow_run_id: str,
    ) -> dict[str, Any]:
        db = self._check_db()
        existing = await self.get_workflow_plan(workflow_plan_id)
        if existing is None:
            raise KeyError(f"Workflow plan not found: {workflow_plan_id}")
        now = _utc_now()
        metadata = {**existing.get("metadata", {}), "workflow_run_id": workflow_run_id}
        await db.execute(
            """
            UPDATE workflow_plans
            SET status = 'dispatched', metadata = ?, updated_at = ?
            WHERE workflow_plan_id = ?
            """,
            (_json_dump(metadata), now, workflow_plan_id),
        )
        await db.commit()
        updated = await self.get_workflow_plan(workflow_plan_id)
        if updated is None:
            raise RuntimeError("Workflow plan dispatch update failed")
        return updated

    @staticmethod
    def _thread_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "chat_thread_id": row["chat_thread_id"],
            "title": row["title"],
            "status": row["status"],
            "metadata": _json_load(row["metadata"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _message_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "message_id": row["message_id"],
            "chat_thread_id": row["chat_thread_id"],
            "role": row["role"],
            "content": row["content"],
            "metadata": _json_load(row["metadata"], {}),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _workflow_plan_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "workflow_plan_id": row["workflow_plan_id"],
            "chat_thread_id": row["chat_thread_id"],
            "message_id": row["message_id"],
            "topic_extraction_id": row["topic_extraction_id"],
            "plan_type": row["plan_type"],
            "status": row["status"],
            "motive": row["motive"],
            "topics": _json_load(row["topics"], []),
            "knowledge_context": _json_load(row["knowledge_context"], []),
            "proposed_steps": _json_load(row["proposed_steps"], []),
            "approvals": _json_load(row["approvals"], {}),
            "metadata": _json_load(row["metadata"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "confirmed_at": row["confirmed_at"],
        }


def extract_candidate_topics(motive: str, *, limit: int = 5) -> list[dict[str, Any]]:
    tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", motive)
        if token.lower() not in _STOP_WORDS
    ]
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        topics.append(
            {
                "topic_id": f"topic:{token}",
                "label": token,
                "extraction_method": "deterministic_motive_keyword",
            }
        )
        if len(topics) >= limit:
            break
    if not topics and motive.strip():
        label = motive.strip()[:80]
        topics.append(
            {
                "topic_id": f"topic:{uuid.uuid5(uuid.NAMESPACE_URL, label).hex[:12]}",
                "label": label,
                "extraction_method": "deterministic_motive_fallback",
            }
        )
    return topics


def build_iwi_workflow_steps(*, research_required: bool = False) -> list[dict[str, Any]]:
    steps = [
        {
            "step_id": "search_knowledge_context",
            "label": "Search Obsidian and Knowledge graph context",
            "requires_confirmation": False,
        },
        {
            "step_id": "run_iwi",
            "label": "Run Insight, Wisdom, and Impact synthesis",
            "requires_confirmation": True,
        },
    ]
    if research_required:
        steps.append(
            {
                "step_id": "run_deep_research",
                "label": "Run external Deep Research",
                "requires_confirmation": True,
                "external_service": "tavily",
            }
        )
    return steps


def _title_from_message(content: str) -> str:
    title = " ".join(content.strip().split())
    return title[:80] or "Untitled chat"


_STOP_WORDS = {
    "about",
    "after",
    "aily",
    "also",
    "and",
    "are",
    "business",
    "can",
    "could",
    "from",
    "generate",
    "help",
    "into",
    "make",
    "need",
    "plan",
    "please",
    "should",
    "that",
    "the",
    "this",
    "want",
    "with",
    "would",
}
