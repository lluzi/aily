"""Orchestrator for the ARMY OF TOP MINDS thinking system.

Coordinates the full thinking pipeline from input to output:
1. Context building from GraphDB (batched queries)
2. Parallel framework analysis (TRIZ, McKinsey, GStack)
3. Cross-framework synthesis (handles 1-3 frameworks)
4. Persuasive output formatting

With structured logging, content hash caching, and partial failure handling.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aily.thinking.models import KnowledgePayload, ThinkingResult

from aily.thinking.frameworks.gstack import GStackAnalyzer
from aily.thinking.frameworks.mckinsey import McKinseyAnalyzer
from aily.thinking.frameworks.triz import TrizAnalyzer
from aily.thinking.models import (
    FrameworkInsight,
    FrameworkType,
    InsightPriority,
    KnowledgePayload,
    ThinkingResult,
)
from aily.thinking.output.formatter import OutputFormatter
from aily.thinking.synthesis.engine import SynthesisEngine

logger = logging.getLogger(__name__)


class ThinkingOrchestrator:
    """Orchestrates the full ARMY OF TOP MINDS thinking pipeline.

    The pipeline:
    1. Ingest knowledge payload
    2. Build context from GraphDB (batched queries)
    3. Run 3 framework analyzers in parallel with partial failure handling
    4. Synthesize cross-framework insights (adaptive: 1-3 frameworks)
    5. Format output for delivery

    Attributes:
        graph_db: GraphDB instance for context retrieval.
        llm_client: LLM client for analysis operations.
        config: Thinking configuration.
        synthesis_engine: Engine for merging framework insights.
        output_formatter: Formatter for persuasive output.
        _cache: Content hash cache for duplicate analysis prevention.
    """

    def __init__(
        self,
        llm_client: Any,
        graph_db: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            llm_client: LLM client for analysis.
            graph_db: Optional GraphDB instance for context.
            config: Optional configuration dict.
        """
        self.llm_client = llm_client
        self.graph_db = graph_db
        self.config = config or {}
        self._cache: dict[str, tuple[float, ThinkingResult]] = {}  # hash -> (timestamp, result)
        self._cache_ttl_seconds = self.config.get("cache_ttl_seconds", 3600)  # 1 hour default

        # Initialize components
        self.synthesis_engine = SynthesisEngine(llm_client, self.config.get("synthesis"))
        self.output_formatter = OutputFormatter(llm_client, self.config.get("output"))

        # Initialize framework analyzers
        self.analyzers: dict[FrameworkType, Any] = {
            FrameworkType.TRIZ: TrizAnalyzer(llm_client, self.config.get("triz")),
            FrameworkType.MCKINSEY: McKinseyAnalyzer(llm_client, self.config.get("mckinsey")),
            FrameworkType.GSTACK: GStackAnalyzer(llm_client, self.config.get("gstack")),
        }

    def _compute_content_hash(self, payload: KnowledgePayload, options: dict[str, Any]) -> str:
        """Compute SHA256 hash of content + options for caching.

        Args:
            payload: Knowledge payload.
            options: Processing options.

        Returns:
            Hex digest of content hash.
        """
        content = f"{payload.content}:{payload.source_url}:{sorted(options.items())}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _get_cached_result(self, content_hash: str) -> ThinkingResult | None:
        """Get cached result if not expired.

        Args:
            content_hash: Content hash key.

        Returns:
            Cached ThinkingResult or None if expired/missing.
        """
        if content_hash not in self._cache:
            return None

        timestamp, result = self._cache[content_hash]
        if time.time() - timestamp >= self._cache_ttl_seconds:
            # Expired
            del self._cache[content_hash]
            return None

        logger.info("Cache hit for content hash %s", content_hash[:8])
        return result

    def _cache_result(self, content_hash: str, result: ThinkingResult) -> None:
        """Cache result with current timestamp.

        Args:
            content_hash: Content hash key.
            result: ThinkingResult to cache.
        """
        self._cache[content_hash] = (time.time(), result)
        logger.info("Cached result for content hash %s", content_hash[:8])

    async def think(
        self,
        payload: KnowledgePayload,
        options: dict[str, Any] | None = None,
    ) -> ThinkingResult:
        """Execute the full thinking pipeline.

        Args:
            payload: Knowledge payload to analyze.
            options: Optional processing options:
                - frameworks: List of frameworks to run (default: all)
                - min_confidence: Minimum confidence threshold
                - max_insights: Maximum insights to return
                - output_format: "obsidian" or "feishu"
                - skip_cache: bool to bypass cache lookup

        Returns:
            ThinkingResult with complete analysis.
        """
        options = options or {}
        start_time = time.time()
        request_id = str(uuid.uuid4())

        # Structured logging context
        log_extra = {"request_id": request_id, "content_length": len(payload.content)}
        logger.info("Starting thinking analysis", extra=log_extra)

        # Check cache (unless skip_cache is True)
        if not options.get("skip_cache"):
            content_hash = self._compute_content_hash(payload, options)
            cached = self._get_cached_result(content_hash)
            if cached:
                logger.info("Returning cached result", extra=log_extra)
                return cached
        else:
            content_hash = None

        try:
            # Step 1: Build context
            context_start = time.time()
            enriched_payload = await self._build_context(payload)
            context_time_ms = int((time.time() - context_start) * 1000)
            logger.info("Context built in %s ms", context_time_ms, extra=log_extra)

            # Step 2: Select frameworks to run
            frameworks_to_run = options.get("frameworks", list(self.analyzers.keys()))
            selected_analyzers = [
                self.analyzers[f] for f in frameworks_to_run if f in self.analyzers
            ]
            logger.info(
                "Selected %s frameworks: %s",
                len(selected_analyzers),
                [a.framework_type.value for a in selected_analyzers],
                extra=log_extra,
            )

            # Step 3: Run frameworks in parallel with partial failure handling
            framework_start = time.time()
            framework_results = await self._run_frameworks(
                selected_analyzers, enriched_payload, log_extra
            )
            framework_time_ms = int((time.time() - framework_start) * 1000)
            logger.info(
                "Framework analysis completed in %s ms (%s/%s succeeded)",
                framework_time_ms,
                len(framework_results),
                len(selected_analyzers),
                extra=log_extra,
            )

            # Step 4: Synthesize results (adaptive: handles 1-3 frameworks)
            synthesis_start = time.time()
            synthesized = await self.synthesis_engine.synthesize(
                enriched_payload, framework_results
            )
            synthesis_time_ms = int((time.time() - synthesis_start) * 1000)
            logger.info(
                "Synthesis completed in %s ms (%s insights)",
                synthesis_time_ms,
                len(synthesized),
                extra=log_extra,
            )

            # Step 5: Filter and rank top insights
            top_insights = self.synthesis_engine.get_top_insights(
                synthesized,
                count=options.get("max_insights", 5),
                min_priority=options.get("min_priority", InsightPriority.MEDIUM),
            )

            # Step 6: Calculate overall confidence
            overall_confidence = self._calculate_overall_confidence(framework_results)

            # Step 7: Format output
            format_start = time.time()
            output_format = options.get("output_format", "obsidian")
            formatted_output = await self.output_formatter.format(
                ThinkingResult(
                    request_id=request_id,
                    payload=enriched_payload,
                    framework_insights=framework_results,
                    synthesized_insights=synthesized,
                    top_insights=top_insights,
                    confidence_score=overall_confidence,
                    processing_metadata={},
                ),
                enriched_payload,
                output_format,
            )
            format_time_ms = int((time.time() - format_start) * 1000)
            logger.info("Output formatted in %s ms", format_time_ms, extra=log_extra)

            # Calculate timing
            total_time_ms = int((time.time() - start_time) * 1000)

            # Build metadata
            processing_metadata = {
                "total_time_ms": total_time_ms,
                "context_time_ms": context_time_ms,
                "framework_time_ms": framework_time_ms,
                "synthesis_time_ms": synthesis_time_ms,
                "format_time_ms": format_time_ms,
                "frameworks_run": [f.value for f in frameworks_to_run],
                "framework_times_ms": {
                    fi.framework_type.value: fi.processing_time_ms
                    for fi in framework_results
                    if fi.processing_time_ms
                },
                "insights_generated": len(synthesized),
                "top_insights_count": len(top_insights),
                "cache_enabled": content_hash is not None,
            }

            result = ThinkingResult(
                request_id=request_id,
                payload=enriched_payload,
                framework_insights=framework_results,
                synthesized_insights=synthesized,
                top_insights=top_insights,
                confidence_score=overall_confidence,
                processing_metadata=processing_metadata,
                formatted_output={output_format: formatted_output},
            )

            # Cache result
            if content_hash:
                self._cache_result(content_hash, result)

            logger.info(
                "Thinking analysis completed in %s ms",
                total_time_ms,
                extra=log_extra,
            )

            return result

        except Exception as e:
            logger.error(
                "Thinking analysis failed: %s",
                e,
                extra=log_extra,
                exc_info=True,
            )
            raise

    async def think_parallel(
        self,
        payloads: list[KnowledgePayload],
        options: dict[str, Any] | None = None,
    ) -> list[ThinkingResult]:
        """Execute thinking pipeline on multiple payloads in parallel.

        Args:
            payloads: List of knowledge payloads to analyze.
            options: Optional processing options.

        Returns:
            List of ThinkingResults in same order as payloads.
        """
        tasks = [self.think(payload, options) for payload in payloads]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def _build_context(self, payload: KnowledgePayload) -> KnowledgePayload:
        """Enrich payload with context from GraphDB using batched queries.

        Args:
            payload: Original knowledge payload.

        Returns:
            Enriched payload with context nodes.
        """
        if not self.graph_db:
            return payload

        try:
            # Extract keywords from content for context lookup
            # This is a simple implementation - could use NLP for better keywords
            words = payload.content.lower().split()
            # Filter to potential entity words (capitalized in original, or longer words)
            keywords = [w for w in words if len(w) > 4][:10]  # Limit to 10 keywords

            if not keywords and not payload.source_url:
                return payload

            context_nodes: list[str] = []

            # Use get_recent_nodes() and filter by keyword matching in Python
            if keywords:
                try:
                    recent_nodes = await self.graph_db.get_recent_nodes(limit=100)
                    keyword_set = set(kw.lower() for kw in keywords)
                    for node in recent_nodes:
                        label = (node.get("label") or "").lower()
                        if any(kw in label for kw in keyword_set):
                            context_nodes.append(node["id"])
                except Exception as e:
                    logger.warning("GraphDB keyword query failed: %s", e)

            # If we have a source URL, look for related nodes via get_recent_nodes()
            if payload.source_url:
                try:
                    recent_nodes = await self.graph_db.get_recent_nodes(limit=100)
                    for node in recent_nodes:
                        label = (node.get("label") or "").lower()
                        if payload.source_url.lower() in label:
                            context_nodes.append(node["id"])
                except Exception as e:
                    logger.warning("GraphDB URL query failed: %s", e)

            # Deduplicate and update payload
            payload.context_nodes = list(set(context_nodes))
            logger.debug("Added %s context nodes", len(payload.context_nodes))

        except Exception:
            # Context enrichment is non-critical
            logger.warning("Context enrichment failed, continuing without context", exc_info=True)

        return payload

    async def _run_frameworks(
        self,
        analyzers: list[Any],
        payload: KnowledgePayload,
        log_extra: dict[str, Any],
    ) -> list[FrameworkInsight]:
        """Run framework analyzers in parallel with partial failure handling.

        Uses asyncio.gather with return_exceptions=True to ensure that
        partial failures (one framework timing out) don't lose results
        from successful frameworks.

        Args:
            analyzers: List of framework analyzers to run.
            payload: Knowledge payload to analyze.
            log_extra: Logging context.

        Returns:
            List of framework insights (may be partial if some failed).
        """
        async def run_analyzer(analyzer: Any) -> FrameworkInsight | Exception:
            analyzer_start = time.time()
            try:
                result = await analyzer.analyze(payload)
                analyzer_time_ms = int((time.time() - analyzer_start) * 1000)
                logger.info(
                    "Framework %s completed in %s ms",
                    analyzer.framework_type.value,
                    analyzer_time_ms,
                    extra={**log_extra, "framework": analyzer.framework_type.value},
                )
                return result
            except Exception as e:
                analyzer_time_ms = int((time.time() - analyzer_start) * 1000)
                logger.warning(
                    "Framework %s failed after %s ms: %s",
                    analyzer.framework_type.value,
                    analyzer_time_ms,
                    e,
                    extra={**log_extra, "framework": analyzer.framework_type.value},
                )
                return e

        tasks = [run_analyzer(a) for a in analyzers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Separate successes from failures
        successful: list[FrameworkInsight] = []
        failed: list[Exception] = []

        for result in results:
            if isinstance(result, Exception):
                failed.append(result)
            else:
                successful.append(result)

        if failed:
            logger.warning(
                "%s/%s frameworks failed: %s",
                len(failed),
                len(analyzers),
                [str(e) for e in failed],
                extra=log_extra,
            )

        return successful

    def _calculate_overall_confidence(
        self, framework_results: list[FrameworkInsight]
    ) -> float:
        """Calculate overall confidence from framework results.

        Args:
            framework_results: List of framework insights.

        Returns:
            Overall confidence score 0.0-1.0.
        """
        if not framework_results:
            return 0.0

        # Average confidence across frameworks
        avg_confidence = sum(fi.confidence for fi in framework_results) / len(
            framework_results
        )

        # Boost for multiple frameworks agreeing
        multi_framework_bonus = min(0.1, (len(framework_results) - 1) * 0.05)

        return min(1.0, avg_confidence + multi_framework_bonus)

    def get_analyzer(self, framework: FrameworkType) -> Any | None:
        """Get a specific framework analyzer.

        Args:
            framework: Framework type to get.

        Returns:
            Analyzer instance or None.
        """
        return self.analyzers.get(framework)

    def clear_cache(self) -> None:
        """Clear the content hash cache."""
        self._cache.clear()
        logger.info("Content cache cleared")

    async def close(self) -> None:
        """Close the orchestrator and cleanup resources."""
        self.clear_cache()
        # Close LLM client if it has an async close method
        if self.llm_client and hasattr(self.llm_client, 'close'):
            await self.llm_client.close()
        logger.info("Thinking orchestrator closed")
