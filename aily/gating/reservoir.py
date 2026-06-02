"""Reservoir - accumulates and enriches content before gating.

The reservoir holds content, allows it to mix and enrich, forms
streams into rivers. Content gains "momentum" here through:
- Context building (GraphDB connections)
- Keyword extraction
- Entity recognition
- Cross-reference with existing knowledge
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from aily.gating.drainage import RainDrop, StreamType

logger = logging.getLogger(__name__)


@dataclass
class ContentPool:
    """A body of content in the reservoir.

    Multiple drops merge into pools. Pools gain depth
    (context, connections) before flowing to the dam.
    """

    id: str
    content: str
    source_drops: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    enriched_at: Optional[datetime] = None

    # Enrichment data
    keywords: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    related_pool_ids: list[str] = field(default_factory=list)
    context_nodes: list[str] = field(default_factory=list)

    # Quality metrics
    content_length: int = 0
    entity_density: float = 0.0
    novelty_score: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = self._generate_id()
        self.content_length = len(self.content)

    def _generate_id(self) -> str:
        content_hash = hashlib.sha256(
            f"{self.content}:{self.created_at.isoformat()}".encode()
        ).hexdigest()[:16]
        return f"pool_{content_hash}"

    @property
    def depth(self) -> float:
        """Calculate pool depth based on enrichment."""
        factors = [
            len(self.keywords) * 0.1,
            len(self.entities) * 0.2,
            len(self.related_pool_ids) * 0.15,
            self.entity_density * 0.3,
            self.novelty_score * 0.25,
        ]
        return sum(factors)

    @property
    def is_deep_enough(self) -> bool:
        """Has pool accumulated enough depth to flow?"""
        return self.depth >= 1.0


@dataclass
class River:
    """A formed flow from pool(s) heading to the dam.

    Rivers have momentum - they carry content with
    enough force to potentially break through the dam.
    """

    id: str
    pool_id: str
    content: str
    stream_type: StreamType
    momentum: float = 0.0  # Calculated from pool depth
    formed_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"river_{self.pool_id}_{int(self.formed_at.timestamp())}"

    @property
    def can_reach_dam(self) -> bool:
        """Does this river have enough momentum?"""
        return self.momentum >= 0.5


class ContentReservoir:
    """Accumulates and enriches content before gating.

    The reservoir is where content waits, gains context,
    and forms rivers strong enough to reach the dam.
    """

    def __init__(
        self,
        graph_db: Any | None = None,
        enrichment_interval: int = 5,
    ) -> None:
        """Initialize reservoir.

        Args:
            graph_db: Optional GraphDB for context enrichment
            enrichment_interval: Seconds between enrichment cycles
        """
        self.graph_db = graph_db
        self.pools: dict[str, ContentPool] = {}
        self.rivers: list[River] = []
        self.enrichment_interval = enrichment_interval
        self._enriching = False
        self._enrich_task: Any = None

    async def ingest(self, drop: RainDrop) -> ContentPool:
        """Ingest a RainDrop into the reservoir.

        Drops merge into pools. Similar content combines.

        Args:
            drop: RainDrop from drainage

        Returns:
            ContentPool containing the drop
        """
        # Check for similar existing pool
        pool = self._find_or_create_pool(drop)

        # Add drop to pool
        pool.source_drops.append(drop.id)

        logger.info(
            "[Reservoir] Ingested drop %s into pool %s (depth: %.2f)",
            drop.id[:12],
            pool.id[:12],
            pool.depth,
        )

        # Trigger enrichment
        await self._enrich_pool(pool)

        # Check if pool is deep enough to form a river
        if pool.is_deep_enough:
            river = await self._form_river(pool, drop)
            if river.can_reach_dam:
                await self._flow_to_dam(river)

        return pool

    def _find_or_create_pool(self, drop: RainDrop) -> ContentPool:
        """Find similar pool or create new one."""
        # Simple: create new pool per drop for now
        # Advanced: content similarity matching
        pool = ContentPool(
            id="",
            content=drop.content,
            source_drops=[drop.id],
        )
        self.pools[pool.id] = pool
        return pool

    async def _enrich_pool(self, pool: ContentPool) -> None:
        """Enrich a pool with context and metadata."""
        logger.info("[Reservoir] Enriching pool %s", pool.id[:12])

        # Extract keywords
        pool.keywords = self._extract_keywords(pool.content)

        # Calculate entity density
        pool.entity_density = len(pool.entities) / max(len(pool.content) / 100, 1)

        # Query GraphDB for related nodes
        if self.graph_db and pool.keywords:
            try:
                pool.context_nodes = await self._query_graph_context(pool.keywords)
            except Exception as e:
                logger.debug("GraphDB enrichment failed: %s", e)

        # Calculate novelty
        pool.novelty_score = await self._calculate_novelty(pool)

        pool.enriched_at = datetime.utcnow()

        logger.info(
            "[Reservoir] Pool %s enriched: keywords=%d, entities=%d, depth=%.2f",
            pool.id[:12],
            len(pool.keywords),
            len(pool.entities),
            pool.depth,
        )

    def _extract_keywords(self, content: str) -> list[str]:
        """Extract key terms from content."""
        # Simple extraction - can be enhanced with NLP
        import re

        # Extract capitalized phrases and technical terms
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', content)
        tech_terms = re.findall(r'\b[A-Z]{2,}\b', content)

        # Common insight keywords
        insight_words = [
            "problem", "solution", "challenge", "opportunity",
            "contradiction", "insight", "trend", "pattern",
            "growth", "market", "product", "strategy",
        ]
        found_insight = [w for w in insight_words if w in content.lower()]

        all_keywords = list(set(words + tech_terms + found_insight))
        return all_keywords[:20]  # Top 20

    async def _query_graph_context(self, keywords: list[str]) -> list[str]:
        """Query GraphDB for related nodes."""
        if not self.graph_db:
            return []

        # This would query GraphDB for entities matching keywords
        # Returning empty for now - implement based on GraphDB interface
        return []

    async def _calculate_novelty(self, pool: ContentPool) -> float:
        """Calculate how novel this content is vs existing pools."""
        if len(self.pools) <= 1:
            return 1.0  # First pool is 100% novel

        # Check similarity to other recent pools
        novel_scores = []
        for other_id, other in self.pools.items():
            if other_id == pool.id:
                continue
            if (datetime.utcnow() - other.created_at) > timedelta(hours=24):
                continue  # Only compare to recent content

            # Simple Jaccard similarity on keywords
            overlap = set(pool.keywords) & set(other.keywords)
            union = set(pool.keywords) | set(other.keywords)
            similarity = len(overlap) / max(len(union), 1)
            novel_scores.append(1 - similarity)

        return sum(novel_scores) / max(len(novel_scores), 1) if novel_scores else 1.0

    async def _form_river(self, pool: ContentPool, drop: RainDrop) -> River:
        """Form a river from a deep pool."""
        river = River(
            id="",
            pool_id=pool.id,
            content=pool.content,
            stream_type=drop.stream_type,
            momentum=pool.depth,
            metadata={
                "keywords": pool.keywords,
                "entities": pool.entities,
                "context_nodes": pool.context_nodes,
                "source_url": drop.metadata.get("source_url"),
                "open_id": drop.creator_id,
                "message_id": drop.source_id,
            },
        )

        self.rivers.append(river)

        logger.info(
            "[Reservoir] Formed river %s from pool %s (momentum: %.2f)",
            river.id[:12],
            pool.id[:12],
            river.momentum,
        )

        return river

    async def _flow_to_dam(self, river: River) -> None:
        """Flow a river to the dam for gating."""
        logger.info(
            "[Reservoir] Flowing river %s to dam (momentum: %.2f)",
            river.id[:12],
            river.momentum,
        )
        # This connects to the InsightDam

    async def start_enrichment(self) -> None:
        """Start continuous enrichment process."""
        import asyncio
        self._enriching = True
        self._enrich_task = asyncio.create_task(self._enrich_loop())
        logger.info("[Reservoir] Enrichment started")

    async def stop_enrichment(self) -> None:
        """Stop enrichment and flow all rivers."""
        self._enriching = False
        if self._enrich_task:
            self._enrich_task.cancel()
            try:
                await self._enrich_task
            except Exception:
                pass

        # Flow all remaining rivers
        for river in self.rivers:
            if river.can_reach_dam:
                await self._flow_to_dam(river)

        logger.info("[Reservoir] Enrichment stopped")

    async def _enrich_loop(self) -> None:
        """Continuously enrich pools."""
        import asyncio
        while self._enriching:
            for pool in self.pools.values():
                if not pool.enriched_at or \
                   (datetime.utcnow() - pool.enriched_at).seconds > self.enrichment_interval:
                    await self._enrich_pool(pool)
            await asyncio.sleep(self.enrichment_interval)

    def get_stats(self) -> dict[str, Any]:
        """Get reservoir statistics."""
        return {
            "pools": len(self.pools),
            "rivers": len(self.rivers),
            "avg_pool_depth": sum(p.depth for p in self.pools.values()) / max(len(self.pools), 1),
            "rivers_ready": sum(1 for r in self.rivers if r.can_reach_dam),
        }
