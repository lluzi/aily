"""Durable queue of synthesis candidates (ripe topics recommended for I/W/I).

A candidate is a scoped subgraph (or topic) that detection found ready for
higher-order synthesis. Candidates are deduped by a stable id, carry a readiness
score and a "why now" reason, and move through:

    pending -> approved -> generated      (user approves; generation runs)
    pending -> dismissed                  (user declines; cooldown before re-raise)
    pending -> stale                      (underlying subgraph changed materially)

Generation itself is never automatic — it is triggered explicitly from an
approved candidate (or a manual Copilot topic request).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

VALID_STATUSES = {"pending", "approved", "dismissed", "stale", "generated"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def candidate_id_for_nodes(node_ids: list[str]) -> str:
    """Stable candidate id from the set of information-node ids in scope."""
    digest = hashlib.sha1("|".join(sorted(node_ids)).encode("utf-8")).hexdigest()[:16]
    return f"cand_{digest}"


def candidate_id_for_topic(topic: str) -> str:
    digest = hashlib.sha1(topic.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"cand_topic_{digest}"


@dataclass
class SynthesisCandidate:
    candidate_id: str
    scope_kind: str  # "subgraph" | "topic"
    scope_label: str
    node_ids: list[str] = field(default_factory=list)
    readiness_score: float = 0.0
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    detected_via: str = "daily"  # "daily" | "threshold" | "manual"

    def to_row(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "scope_kind": self.scope_kind,
            "scope_label": self.scope_label,
            "node_ids": json.dumps(self.node_ids),
            "readiness_score": float(self.readiness_score),
            "reason": self.reason,
            "metrics": json.dumps(self.metrics),
            "detected_via": self.detected_via,
        }


class SynthesisCandidateStore:
    """SQLite-backed store for the one deduped synthesis candidate queue."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS synthesis_candidates (
                candidate_id TEXT PRIMARY KEY,
                scope_kind TEXT NOT NULL,
                scope_label TEXT NOT NULL DEFAULT '',
                node_ids TEXT NOT NULL DEFAULT '[]',
                readiness_score REAL NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                metrics TEXT NOT NULL DEFAULT '{}',
                detected_via TEXT NOT NULL DEFAULT 'daily',
                status TEXT NOT NULL DEFAULT 'pending',
                workflow_run_id TEXT,
                cooldown_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS synthesis_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await self._db.commit()

    async def get_meta(self, key: str, default: str = "") -> str:
        db = self._check()
        cursor = await db.execute("SELECT value FROM synthesis_meta WHERE key=?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else default

    async def set_meta(self, key: str, value: str) -> None:
        db = self._check()
        await db.execute(
            "INSERT INTO synthesis_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        await db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _check(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SynthesisCandidateStore not initialized")
        return self._db

    async def upsert(self, candidate: SynthesisCandidate) -> dict[str, Any]:
        """Insert a new candidate or refresh an existing pending/stale one.

        Returns {"candidate": <row>, "action": "created"|"refreshed"|"skipped"}.
        A candidate in cooldown (recently dismissed) or already approved/generated
        is left untouched and reported as skipped.
        """
        db = self._check()
        now = _utc_now()
        existing = await self.get(candidate.candidate_id)
        if existing is not None:
            status = existing["status"]
            if status in {"approved", "generated"}:
                return {"candidate": existing, "action": "skipped"}
            if status == "dismissed":
                cooldown_until = existing.get("cooldown_until")
                if cooldown_until and now < datetime.fromisoformat(cooldown_until):
                    return {"candidate": existing, "action": "skipped"}
            # pending / stale / cooled-down-dismissed -> refresh as pending
            row = candidate.to_row()
            await db.execute(
                """
                UPDATE synthesis_candidates
                SET scope_kind=?, scope_label=?, node_ids=?, readiness_score=?,
                    reason=?, metrics=?, detected_via=?, status='pending',
                    cooldown_until=NULL, updated_at=?
                WHERE candidate_id=?
                """,
                (
                    row["scope_kind"], row["scope_label"], row["node_ids"],
                    row["readiness_score"], row["reason"], row["metrics"],
                    row["detected_via"], now.isoformat(), candidate.candidate_id,
                ),
            )
            await db.commit()
            return {"candidate": await self.get(candidate.candidate_id), "action": "refreshed"}

        row = candidate.to_row()
        await db.execute(
            """
            INSERT INTO synthesis_candidates
                (candidate_id, scope_kind, scope_label, node_ids, readiness_score,
                 reason, metrics, detected_via, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                row["candidate_id"], row["scope_kind"], row["scope_label"],
                row["node_ids"], row["readiness_score"], row["reason"],
                row["metrics"], row["detected_via"], now.isoformat(), now.isoformat(),
            ),
        )
        await db.commit()
        return {"candidate": await self.get(candidate.candidate_id), "action": "created"}

    async def get(self, candidate_id: str) -> dict[str, Any] | None:
        db = self._check()
        cursor = await db.execute(
            "SELECT * FROM synthesis_candidates WHERE candidate_id=?", (candidate_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    async def list(self, *, status: str | None = "pending", limit: int = 50) -> list[dict[str, Any]]:
        db = self._check()
        safe_limit = max(1, min(int(limit), 200))
        if status:
            cursor = await db.execute(
                "SELECT * FROM synthesis_candidates WHERE status=? ORDER BY readiness_score DESC, updated_at DESC LIMIT ?",
                (status, safe_limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM synthesis_candidates ORDER BY readiness_score DESC, updated_at DESC LIMIT ?",
                (safe_limit,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def set_status(
        self,
        candidate_id: str,
        status: str,
        *,
        workflow_run_id: str | None = None,
        cooldown_hours: int | None = None,
    ) -> dict[str, Any] | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        db = self._check()
        now = _utc_now()
        cooldown_until = (
            (now + timedelta(hours=cooldown_hours)).isoformat()
            if cooldown_hours and status == "dismissed"
            else None
        )
        await db.execute(
            """
            UPDATE synthesis_candidates
            SET status=?,
                workflow_run_id=COALESCE(?, workflow_run_id),
                cooldown_until=?,
                updated_at=?
            WHERE candidate_id=?
            """,
            (status, workflow_run_id, cooldown_until, now.isoformat(), candidate_id),
        )
        await db.commit()
        return await self.get(candidate_id)

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        payload = dict(row)
        for key in ("node_ids", "metrics"):
            try:
                payload[key] = json.loads(payload.get(key) or ("[]" if key == "node_ids" else "{}"))
            except json.JSONDecodeError:
                payload[key] = [] if key == "node_ids" else {}
        return payload
