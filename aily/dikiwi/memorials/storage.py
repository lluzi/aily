"""Memorial storage - GraphDB for machine, Obsidian for human.

Dual storage system:
- GraphDB: Fast queries, machine-readable, full lineage
- Obsidian: Human-readable, markdown format, git-versioned

Includes retry logic and dead-letter queue for failed operations.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aily.dikiwi.memorials.models import Memorial

if TYPE_CHECKING:
    from aily.graph.db import GraphDB

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0


@dataclass
class FailedMemorialEntry:
    """Entry for failed memorial operations (dead-letter queue)."""

    memorial: Memorial
    operation: str  # "save", "get", "query"
    error: str
    retry_count: int = 0
    failed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    storage_type: str = ""  # "graphdb", "obsidian"


class MemorialStore(ABC):
    """Abstract base for memorial storage."""

    @abstractmethod
    async def save(self, memorial: Memorial) -> None:
        """Save a memorial."""
        pass

    @abstractmethod
    async def get(self, memorial_id: str) -> Memorial | None:
        """Get a memorial by ID."""
        pass

    @abstractmethod
    async def query(
        self,
        correlation_id: str | None = None,
        pipeline_id: str | None = None,
        stage: str | None = None,
    ) -> list[Memorial]:
        """Query memorials by criteria."""
        pass


class GraphDBMemorialStore(MemorialStore):
    """Store memorials in GraphDB for fast queries.

    Includes retry logic and dead-letter queue for failed operations.
    """

    def __init__(self, graph_db: GraphDB) -> None:
        self.graph_db = graph_db
        self._dead_letter_queue: list[FailedMemorialEntry] = []

    async def _execute_with_retry(
        self,
        operation: str,
        memorial: Memorial | None,
        fn: callable,
        *args,
        **kwargs,
    ) -> Any:
        """Execute a storage operation with retry logic.

        Args:
            operation: Name of the operation ("save", "get", "query")
            memorial: The memorial being processed (for DLQ tracking)
            fn: The function to execute
            *args, **kwargs: Arguments to pass to fn

        Returns:
            Result of fn, or raises after max retries
        """
        last_error = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                last_error = e
                logger.warning(
                    "Memorial %s attempt %d/%d failed: %s",
                    operation,
                    attempt,
                    MAX_RETRIES,
                    e,
                )

                if attempt < MAX_RETRIES:
                    # Exponential backoff: 1s, 2s, 4s
                    delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        # All retries exhausted - add to dead-letter queue
        if memorial:
            entry = FailedMemorialEntry(
                memorial=memorial,
                operation=operation,
                error=str(last_error),
                retry_count=MAX_RETRIES,
                storage_type="graphdb",
            )
            self._dead_letter_queue.append(entry)
            logger.error(
                "Memorial %s added to dead-letter queue after %d retries",
                memorial.memorial_id[:8],
                MAX_RETRIES,
            )

        raise last_error

    async def save(self, memorial: Memorial) -> None:
        """Save memorial as a graph node with retry."""
        node_props = memorial.to_graph_node()

        # Create Cypher query
        query = """
        MERGE (m:Memorial {id: $id})
        SET m = $props
        WITH m
        MATCH (p:Pipeline {id: $pipeline_id})
        MERGE (p)-[:HAS_MEMORIAL]->(m)
        """

        async def _do_save():
            await self.graph_db.query(
                query,
                {
                    "id": memorial.memorial_id,
                    "pipeline_id": memorial.pipeline_id,
                    "props": node_props,
                },
            )
            logger.debug("Saved memorial %s to GraphDB", memorial.memorial_id[:8])

        try:
            await self._execute_with_retry("save", memorial, _do_save)
        except Exception:
            # Already logged and added to DLQ
            raise

    async def get(self, memorial_id: str) -> Memorial | None:
        """Get memorial from GraphDB with retry."""
        query = """
        MATCH (m:Memorial {id: $id})
        RETURN m
        """

        async def _do_get():
            result = await self.graph_db.query(query, {"id": memorial_id})
            if result and len(result) > 0:
                props = result[0].get("m", {})
                return self._memorial_from_props(props)
            return None

        try:
            return await self._execute_with_retry("get", None, _do_get)
        except Exception:
            return None

    async def query(
        self,
        correlation_id: str | None = None,
        pipeline_id: str | None = None,
        stage: str | None = None,
    ) -> list[Memorial]:
        """Query memorials from GraphDB."""
        conditions = []
        params = {}

        if correlation_id:
            conditions.append("m.correlation_id = $correlation_id")
            params["correlation_id"] = correlation_id

        if pipeline_id:
            conditions.append("m.pipeline_id = $pipeline_id")
            params["pipeline_id"] = pipeline_id

        if stage:
            conditions.append("m.stage = $stage")
            params["stage"] = stage

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query_str = f"""
        MATCH (m:Memorial)
        WHERE {where_clause}
        RETURN m
        ORDER BY m.timestamp DESC
        """

        async def _do_query():
            results = await self.graph_db.query(query_str, params)
            return [
                self._memorial_from_props(r.get("m", {}))
                for r in results
            ]

        try:
            return await self._execute_with_retry("query", None, _do_query)
        except Exception:
            return []

    def get_dead_letter_queue(self) -> list[FailedMemorialEntry]:
        """Get all failed memorial entries for reprocessing."""
        return list(self._dead_letter_queue)

    def clear_dead_letter_queue(self) -> int:
        """Clear the dead-letter queue. Returns count cleared."""
        count = len(self._dead_letter_queue)
        self._dead_letter_queue.clear()
        return count

    def get_health_metrics(self) -> dict[str, Any]:
        """Get health metrics including DLQ status."""
        return {
            "dead_letter_queue_size": len(self._dead_letter_queue),
            "dead_letter_entries": [
                {
                    "memorial_id": e.memorial.memorial_id,
                    "operation": e.operation,
                    "error": e.error,
                    "retry_count": e.retry_count,
                    "failed_at": e.failed_at,
                }
                for e in self._dead_letter_queue
            ],
            "health_status": "degraded" if self._dead_letter_queue else "healthy",
        }

    def _memorial_from_props(self, props: dict[str, Any]) -> Memorial:
        """Reconstruct Memorial from graph properties."""
        from aily.dikiwi.memorials.models import MemorialDecisionType

        return Memorial(
            memorial_id=props.get("id", ""),
            correlation_id=props.get("correlation_id", ""),
            pipeline_id=props.get("pipeline_id", ""),
            stage=props.get("stage", ""),
            decision=MemorialDecisionType[props.get("decision", "PROMOTED")],
            input_hash=props.get("input_hash", ""),
            output_hash=props.get("output_hash", ""),
            reasoning=props.get("reasoning", ""),
            agent_id=props.get("agent_id", ""),
            gate_name=props.get("gate_name", ""),
            metadata=props.get("metadata", {}),
        )


class ObsidianMemorialStore(MemorialStore):
    """Store memorials as Obsidian markdown files.

    Human-readable, git-versioned, easily browsable.
    Includes in-memory index for fast lookups.
    """

    def __init__(self, vault_path: str | Path) -> None:
        self.vault_path = Path(vault_path)
        self.memorials_dir = self.vault_path / "Memorials"
        self.memorials_dir.mkdir(parents=True, exist_ok=True)

        # In-memory index: memorial_id -> file_path
        self._index: dict[str, Path] = {}
        self._index_built = False
        self._correlation_index: dict[str, list[str]] = {}
        self._pipeline_index: dict[str, list[str]] = {}

    async def save(self, memorial: Memorial) -> None:
        """Save memorial as markdown file and update index."""
        # Organize by month: Memorials/2024-01/memorial-id.md
        month_dir = self.memorials_dir / memorial.timestamp.strftime("%Y-%m")
        month_dir.mkdir(exist_ok=True)

        file_path = month_dir / f"{memorial.memorial_id}.md"

        try:
            file_path.write_text(memorial.to_markdown())
            logger.debug(
                "Saved memorial %s to Obsidian: %s",
                memorial.memorial_id[:8],
                file_path,
            )

            # Update index
            self._index[memorial.memorial_id] = file_path
            self._update_correlation_index(memorial.correlation_id, memorial.memorial_id)
            self._update_pipeline_index(memorial.pipeline_id, memorial.memorial_id)
        except Exception as e:
            logger.exception("Failed to save memorial to Obsidian")
            raise

    def _update_correlation_index(self, correlation_id: str, memorial_id: str) -> None:
        """Update correlation_id -> memorial_ids index."""
        if correlation_id not in self._correlation_index:
            self._correlation_index[correlation_id] = []
        if memorial_id not in self._correlation_index[correlation_id]:
            self._correlation_index[correlation_id].append(memorial_id)

    def _update_pipeline_index(self, pipeline_id: str, memorial_id: str) -> None:
        """Update pipeline_id -> memorial_ids index."""
        if pipeline_id not in self._pipeline_index:
            self._pipeline_index[pipeline_id] = []
        if memorial_id not in self._pipeline_index[pipeline_id]:
            self._pipeline_index[pipeline_id].append(memorial_id)

    async def _build_index(self) -> None:
        """Build in-memory index from existing memorial files."""
        if self._index_built:
            return

        self._index.clear()
        self._correlation_index.clear()
        self._pipeline_index.clear()

        for month_dir in self.memorials_dir.iterdir():
            if not month_dir.is_dir():
                continue

            for file_path in month_dir.glob("*.md"):
                try:
                    content = file_path.read_text()
                    memorial = self._parse_markdown(content)
                    self._index[memorial.memorial_id] = file_path
                    self._update_correlation_index(memorial.correlation_id, memorial.memorial_id)
                    self._update_pipeline_index(memorial.pipeline_id, memorial.memorial_id)
                except Exception as e:
                    logger.warning("Failed to index %s: %s", file_path, e)

        self._index_built = True
        logger.debug("Built Obsidian index: %d memorials", len(self._index))

    async def get(self, memorial_id: str) -> Memorial | None:
        """Get memorial from Obsidian vault using index."""
        # Build index if needed
        if not self._index_built:
            await self._build_index()

        # Check index first
        if memorial_id in self._index:
            file_path = self._index[memorial_id]
            if file_path.exists():
                try:
                    content = file_path.read_text()
                    return self._parse_markdown(content)
                except Exception as e:
                    logger.warning("Failed to parse memorial %s: %s", memorial_id, e)
                    return None

        # Fallback: search all directories (for newly added files)
        for month_dir in self.memorials_dir.iterdir():
            if month_dir.is_dir():
                file_path = month_dir / f"{memorial_id}.md"
                if file_path.exists():
                    try:
                        content = file_path.read_text()
                        memorial = self._parse_markdown(content)
                        # Update index
                        self._index[memorial_id] = file_path
                        return memorial
                    except Exception as e:
                        logger.warning("Failed to parse memorial %s: %s", memorial_id, e)
                        return None
        return None

    async def query(
        self,
        correlation_id: str | None = None,
        pipeline_id: str | None = None,
        stage: str | None = None,
    ) -> list[Memorial]:
        """Query memorials from Obsidian vault using index when possible."""
        # Build index if needed
        if not self._index_built:
            await self._build_index()

        memorials = []

        # Use index for correlation_id queries
        if correlation_id and not pipeline_id and not stage:
            memorial_ids = self._correlation_index.get(correlation_id, [])
            for memorial_id in memorial_ids:
                memorial = await self.get(memorial_id)
                if memorial:
                    memorials.append(memorial)
            return memorials

        # Use index for pipeline_id queries
        if pipeline_id and not correlation_id and not stage:
            memorial_ids = self._pipeline_index.get(pipeline_id, [])
            for memorial_id in memorial_ids:
                memorial = await self.get(memorial_id)
                if memorial:
                    memorials.append(memorial)
            return memorials

        # Full scan for complex queries (with stage filter)
        for month_dir in self.memorials_dir.iterdir():
            if not month_dir.is_dir():
                continue

            for file_path in month_dir.glob("*.md"):
                try:
                    content = file_path.read_text()
                    memorial = self._parse_markdown(content)

                    # Apply filters
                    if correlation_id and memorial.correlation_id != correlation_id:
                        continue
                    if pipeline_id and memorial.pipeline_id != pipeline_id:
                        continue
                    if stage and memorial.stage != stage:
                        continue

                    memorials.append(memorial)
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", file_path, e)

        # Sort by timestamp descending
        memorials.sort(key=lambda m: m.timestamp, reverse=True)
        return memorials

    def get_index_stats(self) -> dict[str, Any]:
        """Get index statistics for health monitoring."""
        return {
            "indexed_memorials": len(self._index),
            "correlation_entries": len(self._correlation_index),
            "pipeline_entries": len(self._pipeline_index),
            "index_built": self._index_built,
            "health_status": "healthy" if self._index_built else "initializing",
        }

    def _parse_markdown(self, content: str) -> Memorial:
        """Parse memorial from markdown format."""
        from datetime import datetime
        from aily.dikiwi.memorials.models import MemorialDecisionType

        lines = content.split("\n")
        props = {}

        for line in lines:
            if line.startswith("- **Pipeline**: "):
                props["pipeline_id"] = line.split("`")[1]
            elif line.startswith("- **Correlation**: "):
                props["correlation_id"] = line.split("`")[1]
            elif line.startswith("- **Stage**: "):
                props["stage"] = line.split(": ")[1]
            elif line.startswith("- **Decision**: "):
                decision_name = line.split(": ")[1]
                props["decision"] = MemorialDecisionType[decision_name]
            elif line.startswith("- **Gate**: "):
                gate = line.split(": ")[1]
                props["gate_name"] = "" if gate == "N/A" else gate
            elif line.startswith("- **Timestamp**: "):
                ts_str = line.split(": ")[1]
                props["timestamp"] = datetime.fromisoformat(ts_str)
            elif line.startswith("- **Agent**: "):
                props["agent_id"] = line.split("`")[1]
            elif line.startswith("- **Input Hash**: "):
                props["input_hash"] = line.split("`")[1]
            elif line.startswith("- **Output Hash**: "):
                props["output_hash"] = line.split("`")[1]

        # Extract memorial ID from title
        title_line = lines[0] if lines else ""
        memorial_id = title_line.replace("# Memorial: ", "").strip()

        return Memorial(
            memorial_id=memorial_id,
            correlation_id=props.get("correlation_id", ""),
            pipeline_id=props.get("pipeline_id", ""),
            stage=props.get("stage", ""),
            decision=props.get("decision", MemorialDecisionType.PROMOTED),
            input_hash=props.get("input_hash", ""),
            output_hash=props.get("output_hash", ""),
            reasoning="",  # Would need more sophisticated parsing
            agent_id=props.get("agent_id", ""),
            gate_name=props.get("gate_name", ""),
            timestamp=props.get("timestamp", datetime.now()),
        )


class DualMemorialStore(MemorialStore):
    """Store memorials in both GraphDB and Obsidian."""

    def __init__(
        self,
        graph_store: GraphDBMemorialStore,
        obsidian_store: ObsidianMemorialStore,
    ) -> None:
        self.graph_store = graph_store
        self.obsidian_store = obsidian_store

    async def save(self, memorial: Memorial) -> None:
        """Save to both stores."""
        # Save to GraphDB first (fast, reliable)
        await self.graph_store.save(memorial)

        # Then save to Obsidian (human-readable)
        try:
            await self.obsidian_store.save(memorial)
        except Exception as e:
            logger.warning("Failed to save memorial to Obsidian: %s", e)
            # Don't fail the whole operation if Obsidian fails

    async def get(self, memorial_id: str) -> Memorial | None:
        """Get from GraphDB (faster)."""
        return await self.graph_store.get(memorial_id)

    async def query(
        self,
        correlation_id: str | None = None,
        pipeline_id: str | None = None,
        stage: str | None = None,
    ) -> list[Memorial]:
        """Query from GraphDB (faster)."""
        return await self.graph_store.query(correlation_id, pipeline_id, stage)

    def get_health_metrics(self) -> dict[str, Any]:
        """Get health metrics from both stores."""
        return {
            "graphdb": self.graph_store.get_health_metrics(),
            "obsidian": self.obsidian_store.get_index_stats(),
        }
