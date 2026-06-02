"""GraphDB client extensions for thinking system.

Provides schema initialization and CRUD operations for:
- thinking_insights: Stored insights with metadata
- framework_analyses: Raw framework analysis results
- insight_relationships: Links between insights and source nodes
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class ThinkingGraphClient:
    """Client for thinking-specific GraphDB operations.

    Extends the base GraphDB with tables for storing and retrieving
    thinking insights and their relationships.
    """

    def __init__(self, graph_db: Any) -> None:
        """Initialize the thinking graph client.

        Args:
            graph_db: The base GraphDB instance.
        """
        self.graph_db = graph_db

    async def initialize_thinking_schema(self) -> None:
        """Create thinking-specific tables in GraphDB.

        Creates:
        - thinking_insights: Stored insights
        - framework_analyses: Raw analysis results
        - insight_relationships: Links insights to source nodes
        """
        # thinking_insights table
        await self.graph_db.execute("""
            CREATE TABLE IF NOT EXISTS thinking_insights (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                confidence REAL NOT NULL,
                priority INTEGER NOT NULL,
                frameworks TEXT NOT NULL,  -- JSON array of framework names
                evidence TEXT,  -- JSON array
                contradictions TEXT,  -- JSON array
                action_items TEXT,  -- JSON array
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP  -- For TTL cleanup
            )
        """)

        # framework_analyses table
        await self.graph_db.execute("""
            CREATE TABLE IF NOT EXISTS framework_analyses (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                framework_type TEXT NOT NULL,
                insights TEXT NOT NULL,  -- JSON array
                confidence REAL NOT NULL,
                priority INTEGER NOT NULL,
                raw_analysis TEXT,  -- JSON blob
                processing_time_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # insight_relationships table
        await self.graph_db.execute("""
            CREATE TABLE IF NOT EXISTS insight_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                relationship_type TEXT DEFAULT 'derived_from',
                strength REAL DEFAULT 1.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (insight_id) REFERENCES thinking_insights(id)
            )
        """)

        # Indexes for efficient queries
        await self.graph_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_thinking_insights_request
            ON thinking_insights(request_id)
        """)
        await self.graph_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_thinking_insights_created
            ON thinking_insights(created_at)
        """)
        await self.graph_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_framework_analyses_request
            ON framework_analyses(request_id)
        """)
        await self.graph_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_insight_relationships_insight
            ON insight_relationships(insight_id)
        """)
        await self.graph_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_insight_relationships_node
            ON insight_relationships(node_id)
        """)

        logger.info("Thinking GraphDB schema initialized")

    async def store_insight(
        self,
        insight_id: str,
        request_id: str,
        insight: Any,  # SynthesizedInsight
        expires_at: datetime | None = None,
    ) -> None:
        """Store a synthesized insight.

        Args:
            insight_id: Unique insight identifier.
            request_id: Parent request identifier.
            insight: The SynthesizedInsight to store.
            expires_at: Optional expiration timestamp for TTL.
        """
        query = """
            INSERT INTO thinking_insights (
                id, request_id, title, description, confidence, priority,
                frameworks, evidence, contradictions, action_items, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        params = (
            insight_id,
            request_id,
            insight.title,
            insight.description,
            insight.confidence,
            insight.priority.value,
            json.dumps([f.value for f in insight.supporting_frameworks]),
            json.dumps(insight.evidence),
            json.dumps(insight.contradictions),
            json.dumps(insight.action_items),
            expires_at.isoformat() if expires_at else None,
        )

        await self.graph_db.execute(query, params)
        logger.debug("Stored insight %s for request %s", insight_id, request_id)

    async def store_framework_analysis(
        self,
        analysis_id: str,
        request_id: str,
        framework_insight: Any,  # FrameworkInsight
    ) -> None:
        """Store a framework analysis result.

        Args:
            analysis_id: Unique analysis identifier.
            request_id: Parent request identifier.
            framework_insight: The FrameworkInsight to store.
        """
        query = """
            INSERT INTO framework_analyses (
                id, request_id, framework_type, insights, confidence,
                priority, raw_analysis, processing_time_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """

        params = (
            analysis_id,
            request_id,
            framework_insight.framework_type.value,
            json.dumps(framework_insight.insights),
            framework_insight.confidence,
            framework_insight.priority.value,
            json.dumps(framework_insight.raw_analysis),
            framework_insight.processing_time_ms,
        )

        await self.graph_db.execute(query, params)
        logger.debug(
            "Stored %s analysis for request %s",
            framework_insight.framework_type.value,
            request_id,
        )

    async def link_insight_to_node(
        self,
        insight_id: str,
        node_id: str,
        relationship_type: str = "derived_from",
        strength: float = 1.0,
    ) -> None:
        """Create a relationship between an insight and a graph node.

        Args:
            insight_id: The insight identifier.
            node_id: The graph node identifier.
            relationship_type: Type of relationship.
            strength: Relationship strength 0.0-1.0.
        """
        query = """
            INSERT INTO insight_relationships (
                insight_id, node_id, relationship_type, strength
            ) VALUES (?, ?, ?, ?)
        """

        await self.graph_db.execute(query, (insight_id, node_id, relationship_type, strength))
        logger.debug("Linked insight %s to node %s", insight_id, node_id)

    async def get_related_insights(
        self,
        node_id: str,
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get insights related to a specific node.

        Args:
            node_id: The graph node identifier.
            min_confidence: Minimum confidence threshold.
            limit: Maximum number of results.

        Returns:
            List of insight records.
        """
        query = """
            SELECT i.*, r.strength as relationship_strength
            FROM thinking_insights i
            JOIN insight_relationships r ON i.id = r.insight_id
            WHERE r.node_id = ? AND i.confidence >= ?
            ORDER BY i.confidence DESC, r.strength DESC
            LIMIT ?
        """

        rows = await self.graph_db.fetchall(query, (node_id, min_confidence, limit))
        return [dict(row) for row in rows]

    async def get_insight_history(
        self,
        hours: int = 24,
        frameworks: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent insights within a time window.

        Args:
            hours: Time window in hours.
            frameworks: Optional filter by framework types.

        Returns:
            List of insight records.
        """
        if frameworks:
            # Build framework filter
            framework_conditions = " OR ".join(
                ["frameworks LIKE ?" for _ in frameworks]
            )
            framework_params = [f"%{f}%" for f in frameworks]

            query = f"""
                SELECT * FROM thinking_insights
                WHERE created_at >= datetime('now', '-{hours} hours')
                AND ({framework_conditions})
                ORDER BY created_at DESC
            """
            params = framework_params
        else:
            query = f"""
                SELECT * FROM thinking_insights
                WHERE created_at >= datetime('now', '-{hours} hours')
                ORDER BY created_at DESC
            """
            params = ()

        rows = await self.graph_db.fetchall(query, params)
        return [dict(row) for row in rows]

    async def delete_expired_insights(self) -> int:
        """Delete insights past their expiration date.

        Returns:
            Number of insights deleted.
        """
        query = """
            DELETE FROM thinking_insights
            WHERE expires_at IS NOT NULL AND expires_at < datetime('now')
        """

        cursor = await self.graph_db.execute(query)
        deleted = cursor.rowcount if cursor else 0
        logger.info("Deleted %s expired insights", deleted)
        return deleted

    async def get_insights_by_request(
        self,
        request_id: str,
    ) -> list[dict[str, Any]]:
        """Get all insights for a specific request.

        Args:
            request_id: The request identifier.

        Returns:
            List of insight records.
        """
        query = """
            SELECT * FROM thinking_insights
            WHERE request_id = ?
            ORDER BY confidence DESC
        """

        rows = await self.graph_db.fetchall(query, (request_id,))
        return [dict(row) for row in rows]
