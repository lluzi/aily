"""Queue integration for thinking system job processing.

Handles job types:
- thinking_analysis: Full multi-framework analysis
- thinking_quick: Fast single-framework analysis
- thinking_batch: Batch process multiple items
"""

from __future__ import annotations

import logging
from typing import Any

from aily.thinking.models import KnowledgePayload
from aily.thinking.orchestrator import ThinkingOrchestrator

logger = logging.getLogger(__name__)


class ThinkingJobHandler:
    """Handler for thinking-related queue jobs.

    Processes job types:
    - thinking_analysis: Full 3-framework analysis with synthesis
    - thinking_quick: Single framework fast analysis
    - thinking_batch: Process multiple payloads in parallel
    """

    def __init__(
        self,
        orchestrator: ThinkingOrchestrator,
        output_handler: Any | None = None,
    ) -> None:
        """Initialize the thinking job handler.

        Args:
            orchestrator: The thinking orchestrator for analysis.
            output_handler: Optional output handler for delivery.
        """
        self.orchestrator = orchestrator
        self.output_handler = output_handler

    async def handle_job(self, job: dict[str, Any]) -> dict[str, Any]:
        """Route and handle a thinking job.

        Args:
            job: The job dict with type, payload, and options.

        Returns:
            Job result dict with status and output.
        """
        job_type = job.get("type")
        job_id = job.get("id", "unknown")

        logger.info("Handling thinking job %s of type %s", job_id, job_type)

        try:
            if job_type == "thinking_analysis":
                return await self._handle_full_analysis(job)
            elif job_type == "thinking_quick":
                return await self._handle_quick_analysis(job)
            elif job_type == "thinking_batch":
                return await self._handle_batch_analysis(job)
            else:
                raise ValueError(f"Unknown thinking job type: {job_type}")

        except Exception as e:
            logger.error("Job %s failed: %s", job_id, e, exc_info=True)
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e),
            }

    async def _handle_full_analysis(self, job: dict[str, Any]) -> dict[str, Any]:
        """Handle full multi-framework analysis.

        Args:
            job: Job with payload containing content to analyze.

        Returns:
            Result with ThinkingResult data.
        """
        payload_data = job.get("payload", {})
        options = job.get("options", {})
        metadata = payload_data.get("metadata", {})

        source_url = payload_data.get("source_url")
        content = payload_data.get("content", "")

        # If URL provided, fetch the actual content
        if source_url:
            logger.info("Fetching URL content for analysis: %s", source_url)
            try:
                from aily.browser.fetcher import BrowserFetcher
                from aily.parser import registry

                fetcher = BrowserFetcher()
                raw_text = await fetcher.fetch(source_url)
                parsed = registry.parse(source_url, raw_text)

                # Use parsed content for analysis
                content = parsed.markdown
                source_title = parsed.title
                logger.info("URL fetched successfully: %s (%d chars)", source_title, len(content))
            except Exception as e:
                logger.error("Failed to fetch URL %s: %s", source_url, e)
                # Fall back to original content if fetch fails

        payload = KnowledgePayload(
            content=content,
            source_url=source_url,
            source_title=payload_data.get("source_title") if not source_url else locals().get("source_title"),
            metadata=metadata,
        )

        # Run full analysis
        result = await self.orchestrator.think(payload, options)

        # Deliver output if handler configured
        if self.output_handler:
            # Extract open_id from metadata for Feishu delivery
            delivery_options = {
                **options,
                "output_format": "both",
                "open_id": metadata.get("open_id", ""),
            }
            await self.output_handler.deliver(result, delivery_options)

        return {
            "job_id": job.get("id"),
            "status": "completed",
            "request_id": result.request_id,
            "confidence": result.confidence_score,
            "insights_count": len(result.top_insights),
            "formatted_output": result.formatted_output,
        }

    async def _handle_quick_analysis(self, job: dict[str, Any]) -> dict[str, Any]:
        """Handle quick single-framework analysis.

        Args:
            job: Job with payload and framework selection.

        Returns:
            Result with single framework output.
        """
        payload_data = job.get("payload", {})
        options = job.get("options", {})
        framework = options.get("framework", "mckinsey")  # Default to McKinsey

        payload = KnowledgePayload(
            content=payload_data.get("content", ""),
            source_url=payload_data.get("source_url"),
            source_title=payload_data.get("source_title"),
            metadata=payload_data.get("metadata", {}),
        )

        # Map framework string to enum
        from aily.thinking.models import FrameworkType

        framework_map = {
            "triz": FrameworkType.TRIZ,
            "mckinsey": FrameworkType.MCKINSEY,
            "gstack": FrameworkType.GSTACK,
        }
        framework_type = framework_map.get(framework, FrameworkType.MCKINSEY)

        # Run single framework
        options["frameworks"] = [framework_type]
        result = await self.orchestrator.think(payload, options)

        return {
            "job_id": job.get("id"),
            "status": "completed",
            "request_id": result.request_id,
            "framework": framework,
            "confidence": result.confidence_score,
            "insights_count": len(result.top_insights),
            "formatted_output": result.formatted_output,
        }

    async def _handle_batch_analysis(self, job: dict[str, Any]) -> dict[str, Any]:
        """Handle batch analysis of multiple payloads.

        Args:
            job: Job with list of payloads.

        Returns:
            Result with list of analysis results.
        """
        payloads_data = job.get("payloads", [])
        options = job.get("options", {})

        payloads = [
            KnowledgePayload(
                content=p.get("content", ""),
                source_url=p.get("source_url"),
                source_title=p.get("source_title"),
                metadata=p.get("metadata", {}),
            )
            for p in payloads_data
        ]

        # Run parallel analysis
        results = await self.orchestrator.think_parallel(payloads, options)

        # Deliver outputs if handler configured
        if self.output_handler:
            for result in results:
                if not isinstance(result, Exception):
                    await self.output_handler.deliver(result, options)

        return {
            "job_id": job.get("id"),
            "status": "completed",
            "batch_size": len(payloads),
            "results": [
                {
                    "request_id": r.request_id,
                    "confidence": r.confidence_score,
                    "insights_count": len(r.top_insights),
                }
                for r in results
                if not isinstance(r, Exception)
            ],
            "failed_count": sum(1 for r in results if isinstance(r, Exception)),
        }


def create_thinking_job(
    job_type: str,
    payload: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a thinking job payload for enqueuing.

    Args:
        job_type: One of thinking_analysis, thinking_quick, thinking_batch.
        payload: Content payload (or list for batch).
        options: Processing options.

    Returns:
        Job dict ready for queueing.
    """
    return {
        "type": job_type,
        "payload": payload,
        "options": options or {},
    }
