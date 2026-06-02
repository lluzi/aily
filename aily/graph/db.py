"""GraphDB - SQLite-backed knowledge graph with node properties."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite


class GraphDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path, timeout=30.0)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.execute("PRAGMA busy_timeout=30000")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                label TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_node_id) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (target_node_id) REFERENCES nodes(id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_source_target ON edges(source_node_id, target_node_id)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS occurrences (
                id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                raw_log_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_occurrences_node ON occurrences(node_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_occurrences_log ON occurrences(raw_log_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_created_at ON nodes(created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_created_at ON edges(created_at)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_occurrences_created_at ON occurrences(created_at)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                id TEXT PRIMARY KEY,
                preference TEXT NOT NULL,
                source_note_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS node_properties (
                node_id TEXT PRIMARY KEY,
                properties TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_node_properties_node_id ON node_properties(node_id)"
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS residual_feedback (
                id TEXT PRIMARY KEY,
                proposal_label TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_residual_feedback_created_at ON residual_feedback(created_at)"
        )
        await self._db.commit()

    async def _retry_db_op(self, op, attempts: int = 5, base_delay: float = 0.2):
        """Retry transient SQLite lock errors."""
        last_exc = None
        for attempt in range(attempts):
            try:
                return await op()
            except aiosqlite.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower() or attempt == attempts - 1:
                    raise
                await asyncio.sleep(base_delay * (attempt + 1))
        if last_exc is not None:
            raise last_exc

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _execute(self, sql: str, params: tuple | None = None) -> None:
        if self._db is None:
            raise RuntimeError("GraphDB not initialized")
        async def op():
            await self._db.execute(sql, params or ())
            await self._db.commit()
        await self._retry_db_op(op)

    async def _fetchall(self, sql: str, params: tuple | None = None) -> list[tuple]:
        if self._db is None:
            raise RuntimeError("GraphDB not initialized")
        async def op():
            cursor = await self._db.execute(sql, params or ())
            return await cursor.fetchall()
        return await self._retry_db_op(op)

    async def insert_node(self, node_id: str, node_type: str, label: str, source: str) -> None:
        await self._execute(
            "INSERT OR IGNORE INTO nodes (id, type, label, source) VALUES (?, ?, ?, ?)",
            (node_id, node_type, label, source),
        )

    async def insert_edge(
        self,
        edge_id: str,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        weight: float,
        source: str,
    ) -> None:
        await self._execute(
            """
            INSERT OR IGNORE INTO edges
            (id, source_node_id, target_node_id, relation_type, weight, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (edge_id, source_node_id, target_node_id, relation_type, weight, source),
        )

    async def insert_occurrence(self, occurrence_id: str, node_id: str, raw_log_id: str) -> None:
        await self._execute(
            "INSERT INTO occurrences (id, node_id, raw_log_id) VALUES (?, ?, ?)",
            (occurrence_id, node_id, raw_log_id),
        )

    async def insert_preference(self, preference_id: str, preference: str, source_note_path: str) -> None:
        await self._execute(
            "INSERT INTO user_preferences (id, preference, source_note_path) VALUES (?, ?, ?)",
            (preference_id, preference, source_note_path),
        )

    async def get_nodes_by_type(self, node_type: str) -> list[dict]:
        rows = await self._fetchall(
            "SELECT id, type, label, source, created_at FROM nodes WHERE type = ?",
            (node_type,),
        )
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    async def count_nodes_by_type(self, node_type: str) -> int:
        rows = await self._fetchall(
            "SELECT COUNT(*) FROM nodes WHERE type = ?",
            (node_type,),
        )
        return int(rows[0][0]) if rows else 0

    async def get_cooccurring_nodes(self, raw_log_id: str) -> list[dict]:
        rows = await self._fetchall(
            """
            SELECT n.id, n.type, n.label, n.source, n.created_at
            FROM nodes n
            JOIN occurrences o ON n.id = o.node_id
            WHERE o.raw_log_id = ?
            """,
            (raw_log_id,),
        )
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    @staticmethod
    def _cutoff(hours: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    async def get_nodes_within_hours(self, hours: int) -> list[dict]:
        cutoff = self._cutoff(hours)
        rows = await self._fetchall(
            """
            SELECT id, type, label, source, created_at
            FROM nodes
            WHERE created_at >= ?
            """,
            (cutoff,),
        )
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    async def get_graph_snapshot(
        self,
        *,
        limit_nodes: int = 2000,
        limit_edges: int = 5000,
    ) -> dict[str, list[dict]]:
        node_rows = await self._fetchall(
            """
            SELECT id, type, label, source, created_at
            FROM nodes
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit_nodes,),
        )
        edge_rows = await self._fetchall(
            """
            SELECT id, source_node_id, target_node_id, relation_type, weight, source, created_at
            FROM edges
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit_edges,),
        )
        return {
            "nodes": [
                {
                    "id": row[0],
                    "type": row[1],
                    "label": row[2],
                    "source": row[3],
                    "created_at": row[4],
                }
                for row in node_rows
            ],
            "edges": [
                {
                    "id": row[0],
                    "source_node_id": row[1],
                    "target_node_id": row[2],
                    "relation_type": row[3],
                    "weight": row[4],
                    "source": row[5],
                    "created_at": row[6],
                }
                for row in edge_rows
            ],
        }

    async def get_recent_nodes(self, limit: int = 20) -> list[dict]:
        rows = await self._fetchall(
            """
            SELECT id, type, label, source, created_at
            FROM nodes
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    async def get_edges_within_hours(self, hours: int) -> list[dict]:
        cutoff = self._cutoff(hours)
        rows = await self._fetchall(
            """
            SELECT id, source_node_id, target_node_id, relation_type, weight, source, created_at
            FROM edges
            WHERE created_at >= ?
            """,
            (cutoff,),
        )
        return [
            {
                "id": row[0],
                "source_node_id": row[1],
                "target_node_id": row[2],
                "relation_type": row[3],
                "weight": row[4],
                "source": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    async def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict]:
        """Return nodes whose ids are in node_ids, preserving DB metadata."""
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        rows = await self._fetchall(
            f"""
            SELECT id, type, label, source, created_at
            FROM nodes
            WHERE id IN ({placeholders})
            """,
            tuple(node_ids),
        )
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    async def search_information_nodes(
        self,
        query_terms: list[str],
        *,
        limit: int = 10,
    ) -> list[dict]:
        """Search information nodes by label and rich node properties."""
        terms = [term.strip().lower() for term in query_terms if term.strip()]
        if not terms:
            return []
        clauses: list[str] = []
        params: list[object] = []
        for term in terms[:8]:
            pattern = f"%{term}%"
            clauses.append("(lower(n.label) LIKE ? OR lower(COALESCE(np.properties, '')) LIKE ?)")
            params.extend([pattern, pattern])
        params.append(max(1, min(100, limit)))
        rows = await self._fetchall(
            f"""
            SELECT n.id, n.type, n.label, n.source, n.created_at, np.properties
            FROM nodes n
            LEFT JOIN node_properties np ON n.id = np.node_id
            WHERE n.type = 'information'
              AND ({' OR '.join(clauses)})
            ORDER BY n.created_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        results: list[dict] = []
        for row in rows:
            props = json.loads(row[5]) if row[5] else {}
            results.append(
                {
                    "id": row[0],
                    "type": row[1],
                    "label": row[2],
                    "source": row[3],
                    "created_at": row[4],
                    "properties": props,
                }
            )
        return results

    async def get_neighbors(
        self,
        node_id: str,
        relation_type: str | None = None,
        direction: str = "both",
        limit: int = 50,
    ) -> list[dict]:
        """Return neighboring nodes with the connecting edge.

        direction:
            out: node_id -> neighbor
            in: neighbor -> node_id
            both: either direction
        """
        relation_clause = "AND e.relation_type = ?" if relation_type else ""
        params: list[object] = []

        if direction == "out":
            direction_clause = "e.source_node_id = ?"
            neighbor_expr = "e.target_node_id"
            params.append(node_id)
        elif direction == "in":
            direction_clause = "e.target_node_id = ?"
            neighbor_expr = "e.source_node_id"
            params.append(node_id)
        else:
            direction_clause = "(e.source_node_id = ? OR e.target_node_id = ?)"
            neighbor_expr = (
                "CASE WHEN e.source_node_id = ? THEN e.target_node_id "
                "ELSE e.source_node_id END"
            )
            params.extend([node_id, node_id, node_id])

        if relation_type:
            params.append(relation_type)
        params.append(limit)

        rows = await self._fetchall(
            f"""
            SELECT n.id, n.type, n.label, n.source, n.created_at,
                   e.id, e.source_node_id, e.target_node_id, e.relation_type,
                   e.weight, e.source, e.created_at
            FROM edges e
            JOIN nodes n ON n.id = {neighbor_expr}
            WHERE {direction_clause}
              {relation_clause}
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
                "edge": {
                    "id": row[5],
                    "source_node_id": row[6],
                    "target_node_id": row[7],
                    "relation_type": row[8],
                    "weight": row[9],
                    "source": row[10],
                    "created_at": row[11],
                },
            }
            for row in rows
        ]

    async def get_edges_for_nodes(self, node_ids: list[str], limit: int = 200) -> list[dict]:
        """Return edges where both endpoints are inside node_ids."""
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        params = tuple([*node_ids, *node_ids, limit])
        rows = await self._fetchall(
            f"""
            SELECT id, source_node_id, target_node_id, relation_type, weight, source, created_at
            FROM edges
            WHERE source_node_id IN ({placeholders})
              AND target_node_id IN ({placeholders})
            ORDER BY weight DESC, created_at DESC
            LIMIT ?
            """,
            params,
        )
        return [
            {
                "id": row[0],
                "source_node_id": row[1],
                "target_node_id": row[2],
                "relation_type": row[3],
                "weight": row[4],
                "source": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    async def get_top_nodes_by_edge_count(
        self, hours: int | None = None, limit: int = 10
    ) -> list[dict]:
        if hours is not None:
            cutoff = self._cutoff(hours)
            sql = """
                SELECT n.id, n.type, n.label, n.source, n.created_at,
                       COUNT(e.id) AS edge_count,
                       COALESCE(SUM(e.weight), 0) AS total_weight
                FROM nodes n
                JOIN edges e ON n.id = e.source_node_id OR n.id = e.target_node_id
                WHERE e.created_at >= ?
                GROUP BY n.id
                ORDER BY edge_count DESC, total_weight DESC
                LIMIT ?
            """
            params = (cutoff, limit)
        else:
            sql = """
                SELECT n.id, n.type, n.label, n.source, n.created_at,
                       COUNT(e.id) AS edge_count,
                       COALESCE(SUM(e.weight), 0) AS total_weight
                FROM nodes n
                JOIN edges e ON n.id = e.source_node_id OR n.id = e.target_node_id
                GROUP BY n.id
                ORDER BY edge_count DESC, total_weight DESC
                LIMIT ?
            """
            params = (limit,)
        rows = await self._fetchall(sql, params)
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
                "edge_count": row[5],
                "total_weight": row[6],
            }
            for row in rows
        ]

    async def get_top_information_nodes_by_semantic_edge_count(
        self,
        hours: int | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return information hubs using only information-to-information edges."""
        if hours is not None:
            cutoff = self._cutoff(hours)
            sql = """
                SELECT n.id, n.type, n.label, n.source, n.created_at,
                       COUNT(e.id) AS edge_count,
                       COALESCE(SUM(e.weight), 0) AS total_weight
                FROM nodes n
                JOIN edges e ON n.id = e.source_node_id OR n.id = e.target_node_id
                JOIN nodes other ON other.id = CASE
                    WHEN n.id = e.source_node_id THEN e.target_node_id
                    ELSE e.source_node_id
                END
                WHERE e.created_at >= ?
                  AND n.type = 'information'
                  AND other.type = 'information'
                  AND e.relation_type != 'has_tag'
                  AND e.source_node_id != e.target_node_id
                GROUP BY n.id
                ORDER BY edge_count DESC, total_weight DESC
                LIMIT ?
            """
            params = (cutoff, limit)
        else:
            sql = """
                SELECT n.id, n.type, n.label, n.source, n.created_at,
                       COUNT(e.id) AS edge_count,
                       COALESCE(SUM(e.weight), 0) AS total_weight
                FROM nodes n
                JOIN edges e ON n.id = e.source_node_id OR n.id = e.target_node_id
                JOIN nodes other ON other.id = CASE
                    WHEN n.id = e.source_node_id THEN e.target_node_id
                    ELSE e.source_node_id
                END
                WHERE n.type = 'information'
                  AND other.type = 'information'
                  AND e.relation_type != 'has_tag'
                  AND e.source_node_id != e.target_node_id
                GROUP BY n.id
                ORDER BY edge_count DESC, total_weight DESC
                LIMIT ?
            """
            params = (limit,)
        rows = await self._fetchall(sql, params)
        return [
            {
                "id": row[0],
                "type": row[1],
                "label": row[2],
                "source": row[3],
                "created_at": row[4],
                "edge_count": row[5],
                "total_weight": row[6],
            }
            for row in rows
        ]

    async def get_collisions_within_hours(self, hours: int, min_occurrences: int = 2) -> list[dict]:
        cutoff = self._cutoff(hours)
        rows = await self._fetchall(
            """
            SELECT o.node_id, n.type, n.label, COUNT(DISTINCT o.raw_log_id) AS occurrence_count
            FROM occurrences o
            JOIN nodes n ON n.id = o.node_id
            WHERE o.created_at >= ?
            GROUP BY o.node_id
            HAVING occurrence_count >= ?
            """,
            (cutoff, min_occurrences),
        )
        return [
            {
                "node_id": row[0],
                "type": row[1],
                "label": row[2],
                "occurrence_count": row[3],
            }
            for row in rows
        ]

    async def get_source_logs_for_node(
        self, node_id: str, hours: int | None = None
    ) -> list[dict]:
        if hours is not None:
            cutoff = self._cutoff(hours)
            sql = """
                SELECT raw_log_id, created_at
                FROM occurrences
                WHERE node_id = ?
                  AND created_at >= ?
            """
            params = (node_id, cutoff)
        else:
            sql = """
                SELECT raw_log_id, created_at
                FROM occurrences
                WHERE node_id = ?
            """
            params = (node_id,)
        rows = await self._fetchall(sql, params)
        return [
            {
                "raw_log_id": row[0],
                "created_at": row[1],
            }
            for row in rows
        ]

    async def set_node_property(self, node_id: str, key: str, value: object) -> None:
        """Set a single JSON-serializable property on a node."""
        if self._db is None:
            raise RuntimeError("GraphDB not initialized")
        async def op():
            row = await self._db.execute(
                "SELECT properties FROM node_properties WHERE node_id = ?",
                (node_id,),
            )
            result = await row.fetchone()
            props = json.loads(result[0]) if result else {}
            props[key] = value
            await self._db.execute(
                """
                INSERT INTO node_properties (node_id, properties, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    properties = excluded.properties,
                    updated_at = excluded.updated_at
                """,
                (node_id, json.dumps(props, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )
            await self._db.commit()
        await self._retry_db_op(op)

    async def get_node_properties(self, node_id: str) -> dict[str, object]:
        """Get all properties for a node as a dict."""
        if self._db is None:
            raise RuntimeError("GraphDB not initialized")
        row = await self._db.execute(
            "SELECT properties FROM node_properties WHERE node_id = ?",
            (node_id,),
        )
        result = await row.fetchone()
        return json.loads(result[0]) if result else {}

    async def get_nodes_by_property(self, node_type: str, key: str, value: object) -> list[dict]:
        """Get nodes of a given type with a specific property value."""
        json_path = f"$.{key}"
        # SQLite json_extract returns 0/1 for booleans
        if isinstance(value, bool):
            db_value = 1 if value else 0
        else:
            db_value = value
        rows = await self._fetchall(
            """
            SELECT n.id, n.type, n.label, n.source, n.created_at, np.properties
            FROM nodes n
            JOIN node_properties np ON n.id = np.node_id
            WHERE n.type = ?
              AND json_extract(np.properties, ?) = ?
            """,
            (node_type, json_path, db_value),
        )
        results = []
        for row in rows:
            props = json.loads(row[5]) if row[5] else {}
            results.append(
                {
                    "id": row[0],
                    "type": row[1],
                    "label": row[2],
                    "source": row[3],
                    "created_at": row[4],
                    "properties": props,
                }
            )
        return results

    async def add_residual_feedback(self, proposal_label: str, reason: str) -> None:
        """Append a Residual rejection feedback entry."""
        import uuid
        await self._execute(
            "INSERT INTO residual_feedback (id, proposal_label, reason) VALUES (?, ?, ?)",
            (f"hf_{uuid.uuid4().hex[:8]}", proposal_label, reason),
        )

    async def get_residual_feedback(self, limit: int = 100) -> list[dict]:
        """Get recent Residual rejection feedback entries."""
        rows = await self._fetchall(
            """
            SELECT proposal_label, reason, created_at
            FROM residual_feedback
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {"proposal_label": row[0], "reason": row[1], "created_at": row[2]}
            for row in rows
        ]
