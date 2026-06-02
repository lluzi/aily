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


class BusinessPlanStore:
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
            CREATE TABLE IF NOT EXISTS team_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                workflow_plan_id TEXT NOT NULL,
                team TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('completed','failed')),
                payload TEXT NOT NULL,
                obsidian_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS business_plans (
                business_plan_id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                workflow_plan_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('completed','failed')),
                title TEXT NOT NULL,
                payload TEXT NOT NULL,
                markdown TEXT NOT NULL,
                obsidian_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_team_evaluations_workflow ON team_evaluations(workflow_run_id, team)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_business_plans_workflow ON business_plans(workflow_run_id, created_at)"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _check_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("BusinessPlanStore not initialized")
        return self._db

    async def create_team_evaluation(
        self,
        *,
        workflow_run_id: str,
        workflow_plan_id: str,
        team: str,
        payload: dict[str, Any],
        obsidian_path: str = "",
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        evaluation_id = payload.get("evaluation_id") or _new_id("eval")
        payload = {**payload, "evaluation_id": evaluation_id}
        await db.execute(
            """
            INSERT INTO team_evaluations (
                evaluation_id, workflow_run_id, workflow_plan_id, team, status,
                payload, obsidian_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                workflow_run_id,
                workflow_plan_id,
                team,
                _json_dump(payload),
                obsidian_path,
                now,
                now,
            ),
        )
        await db.commit()
        record = await self.get_team_evaluation(evaluation_id)
        if record is None:
            raise RuntimeError("Team evaluation creation failed")
        return record

    async def update_team_evaluation_obsidian_path(self, evaluation_id: str, obsidian_path: str) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        await db.execute(
            "UPDATE team_evaluations SET obsidian_path = ?, updated_at = ? WHERE evaluation_id = ?",
            (obsidian_path, now, evaluation_id),
        )
        await db.commit()
        record = await self.get_team_evaluation(evaluation_id)
        if record is None:
            raise RuntimeError("Team evaluation update failed")
        return record

    async def get_team_evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT evaluation_id, workflow_run_id, workflow_plan_id, team, status,
                       payload, obsidian_path, created_at, updated_at
                FROM team_evaluations
                WHERE evaluation_id = ?
                """,
                (evaluation_id,),
            )
        ).fetchone()
        return self._evaluation_from_row(row) if row is not None else None

    async def list_team_evaluations(self, workflow_run_id: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        db = self._check_db()
        if workflow_run_id:
            rows = await (
                await db.execute(
                    """
                    SELECT evaluation_id, workflow_run_id, workflow_plan_id, team, status,
                           payload, obsidian_path, created_at, updated_at
                    FROM team_evaluations
                    WHERE workflow_run_id = ?
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    (workflow_run_id, max(1, min(500, limit))),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    """
                    SELECT evaluation_id, workflow_run_id, workflow_plan_id, team, status,
                           payload, obsidian_path, created_at, updated_at
                    FROM team_evaluations
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(500, limit)),),
                )
            ).fetchall()
        return [self._evaluation_from_row(row) for row in rows]

    async def create_business_plan(
        self,
        *,
        workflow_run_id: str,
        workflow_plan_id: str,
        title: str,
        payload: dict[str, Any],
        markdown: str,
        obsidian_path: str = "",
    ) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        business_plan_id = payload.get("business_plan_id") or _new_id("bp")
        payload = {**payload, "business_plan_id": business_plan_id}
        await db.execute(
            """
            INSERT INTO business_plans (
                business_plan_id, workflow_run_id, workflow_plan_id, status,
                title, payload, markdown, obsidian_path, created_at, updated_at
            )
            VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)
            """,
            (
                business_plan_id,
                workflow_run_id,
                workflow_plan_id,
                title,
                _json_dump(payload),
                markdown,
                obsidian_path,
                now,
                now,
            ),
        )
        await db.commit()
        record = await self.get_business_plan(business_plan_id)
        if record is None:
            raise RuntimeError("Business plan creation failed")
        return record

    async def update_business_plan_obsidian_path(self, business_plan_id: str, obsidian_path: str) -> dict[str, Any]:
        db = self._check_db()
        now = _utc_now()
        await db.execute(
            "UPDATE business_plans SET obsidian_path = ?, updated_at = ? WHERE business_plan_id = ?",
            (obsidian_path, now, business_plan_id),
        )
        await db.commit()
        record = await self.get_business_plan(business_plan_id)
        if record is None:
            raise RuntimeError("Business plan update failed")
        return record

    async def get_business_plan(self, business_plan_id: str) -> dict[str, Any] | None:
        db = self._check_db()
        row = await (
            await db.execute(
                """
                SELECT business_plan_id, workflow_run_id, workflow_plan_id, status,
                       title, payload, markdown, obsidian_path, created_at, updated_at
                FROM business_plans
                WHERE business_plan_id = ?
                """,
                (business_plan_id,),
            )
        ).fetchone()
        return self._business_plan_from_row(row) if row is not None else None

    async def list_business_plans(self, *, limit: int = 50) -> list[dict[str, Any]]:
        db = self._check_db()
        rows = await (
            await db.execute(
                """
                SELECT business_plan_id, workflow_run_id, workflow_plan_id, status,
                       title, payload, markdown, obsidian_path, created_at, updated_at
                FROM business_plans
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(500, limit)),),
            )
        ).fetchall()
        return [self._business_plan_from_row(row) for row in rows]

    @staticmethod
    def _evaluation_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "evaluation_id": row["evaluation_id"],
            "workflow_run_id": row["workflow_run_id"],
            "workflow_plan_id": row["workflow_plan_id"],
            "team": row["team"],
            "status": row["status"],
            "payload": _json_load(row["payload"], {}),
            "obsidian_path": row["obsidian_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _business_plan_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "business_plan_id": row["business_plan_id"],
            "workflow_run_id": row["workflow_run_id"],
            "workflow_plan_id": row["workflow_plan_id"],
            "status": row["status"],
            "title": row["title"],
            "payload": _json_load(row["payload"], {}),
            "markdown": row["markdown"],
            "obsidian_path": row["obsidian_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
