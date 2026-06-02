"""门下省 (Menxia) - Institutional review gate with veto power.

The 门下省 was the Tang Dynasty's review ministry that could reject
imperial edicts (封驳 - feng bo). In DIKIWI, it reviews content quality
at the INFORMATION → KNOWLEDGE transition.

Includes CircuitBreaker for LLM call protection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aily.llm.client import LLMClient

from aily.sessions.base import CircuitBreakerMixin

logger = logging.getLogger(__name__)


class ReviewDecisionType(Enum):
    """Possible decisions from Menxia review."""

    APPROVE = auto()   # Content passes review
    REJECT = auto()    # 封驳 - content rejected, send back
    MODIFY = auto()    # Approve with modifications


@dataclass
class ReviewDecision:
    """Decision from 门下省 institutional review."""

    decision: ReviewDecisionType
    reason: str = ""
    quality_score: float = 0.0
    send_back_to: str = "information"
    modifications: dict[str, Any] | None = None


class MenxiaGate(CircuitBreakerMixin):
    """门下省 gate - Institutional review with veto power (封驳).

    Responsibilities:
    1. Quality assessment of INFORMATION stage output
    2. Tag validation (are tags appropriate?)
    3. Content classification verification
    4. Rejection with reason (封驳 mechanism)

    Can REJECT content and send back to INFORMATION for re-processing.
    This is the hard rail - no content passes to KNOWLEDGE without approval.

    Includes CircuitBreaker protection for LLM calls to prevent cascading failures.
    """

    DEFAULT_QUALITY_THRESHOLD = 0.6
    DEFAULT_CB_FAILURE_THRESHOLD = 3
    DEFAULT_CB_RECOVERY_TIMEOUT = timedelta(minutes=5)

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        quality_threshold: float = DEFAULT_QUALITY_THRESHOLD,
        circuit_failure_threshold: int = DEFAULT_CB_FAILURE_THRESHOLD,
        circuit_recovery_timeout: timedelta = DEFAULT_CB_RECOVERY_TIMEOUT,
    ) -> None:
        # Initialize CircuitBreakerMixin
        super().__init__(
            failure_threshold=circuit_failure_threshold,
            recovery_timeout=circuit_recovery_timeout,
        )
        self.llm_client = llm_client
        self.quality_threshold = quality_threshold
        self._review_count = 0
        self._rejection_count = 0
        self._circuit_failure_count = 0
        self._circuit_blocked_count = 0

    async def review(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ReviewDecision:
        """Perform institutional review of content.

        Args:
            content: The content to review
            metadata: Additional context (tags, classification, etc.)

        Returns:
            ReviewDecision with approve/reject/modify
        """
        self._review_count += 1

        if not self.llm_client:
            # Auto-approve if no LLM available
            return ReviewDecision(
                decision=ReviewDecisionType.APPROVE,
                reason="No LLM available for review - auto-approved",
                quality_score=0.5,
            )

        # Build review context
        tags = metadata.get("tags", {}) if metadata else {}
        classification = metadata.get("classification", "unknown")

        prompt = f"""You are 门下省 (Menxia), the review ministry.

Review the following content for quality before it enters the knowledge network.

Content:
{content[:2000]}

Tags: {tags}
Classification: {classification}

Evaluate on these criteria:
1. Clarity - Is the content understandable?
2. Usefulness - Does it contain actionable or valuable information?
3. Accuracy - Is it factually sound (to best of your knowledge)?
4. Completeness - Is it self-contained enough to be useful?

Respond in this format:
Decision: [APPROVE/REJECT/MODIFY]
Quality Score: [0.0-1.0]
Reason: [brief explanation]
Suggestions: [if rejecting, what needs improvement]"""

        # Check circuit breaker before making LLM call
        if not await self.can_execute():
            self._circuit_blocked_count += 1
            logger.warning("[MENXIA] Circuit breaker open - auto-approving content")
            return ReviewDecision(
                decision=ReviewDecisionType.APPROVE,
                reason="Circuit breaker open - auto-approved due to LLM unavailability",
                quality_score=0.5,
            )

        try:
            response = await self.llm_client.complete(prompt)
            decision = self._parse_decision(response)

            # Record success for circuit breaker
            await self.record_success()

            if decision.decision == ReviewDecisionType.REJECT:
                self._rejection_count += 1
                logger.info(
                    "[MENXIA] 封驳 - Content rejected (score: %.2f): %s",
                    decision.quality_score,
                    decision.reason,
                )
            else:
                logger.info(
                    "[MENXIA] Approved (score: %.2f): %s",
                    decision.quality_score,
                    decision.reason,
                )

            return decision

        except Exception as e:
            logger.exception("Menxia review failed")
            # Record failure for circuit breaker
            await self.record_failure()
            self._circuit_failure_count += 1
            # Fail open - approve but log error
            return ReviewDecision(
                decision=ReviewDecisionType.APPROVE,
                reason=f"Review error: {e}",
                quality_score=0.5,
            )

    def _parse_decision(self, response: str) -> ReviewDecision:
        """Parse LLM review response."""
        response_lower = response.lower()

        # Extract decision
        if "decision:" in response_lower:
            decision_line = response_lower.split("decision:")[1].split("\n")[0]
            if "reject" in decision_line:
                decision_type = ReviewDecisionType.REJECT
            elif "modify" in decision_line:
                decision_type = ReviewDecisionType.MODIFY
            else:
                decision_type = ReviewDecisionType.APPROVE
        else:
            decision_type = ReviewDecisionType.APPROVE

        # Extract quality score
        quality_score = 0.5
        if "quality score:" in response_lower:
            try:
                score_line = response_lower.split("quality score:")[1].split("\n")[0]
                # Extract number
                import re
                numbers = re.findall(r"[\d.]+", score_line)
                if numbers:
                    quality_score = float(numbers[0])
            except (ValueError, IndexError):
                pass

        # Extract reason
        reason = "No reason provided"
        if "reason:" in response_lower:
            try:
                reason = response.split("reason:")[1].split("\n")[0].strip()
            except IndexError:
                pass

        # Auto-reject if quality below threshold
        if quality_score < self.quality_threshold:
            decision_type = ReviewDecisionType.REJECT
            reason = f"{reason} (Quality score {quality_score:.2f} below threshold {self.quality_threshold})"

        return ReviewDecision(
            decision=decision_type,
            reason=reason,
            quality_score=quality_score,
        )

    async def batch_review(
        self,
        items: list[dict[str, Any]],
    ) -> list[ReviewDecision]:
        """Review multiple items efficiently."""
        decisions = []
        for item in items:
            decision = await self.review(
                content=item.get("content", ""),
                metadata=item.get("metadata"),
            )
            decisions.append(decision)
        return decisions

    def get_metrics(self) -> dict[str, Any]:
        """Get gate metrics including circuit breaker status."""
        # Get circuit breaker state from parent mixin
        circuit_state = getattr(self, '_circuit', None)
        return {
            "total_reviews": self._review_count,
            "rejections": self._rejection_count,
            "rejection_rate": (
                self._rejection_count / max(self._review_count, 1)
            ),
            "quality_threshold": self.quality_threshold,
            # Circuit breaker metrics
            "circuit_state": circuit_state.state if circuit_state else "unknown",
            "circuit_failures": getattr(circuit_state, 'failures', 0) if circuit_state else 0,
            "circuit_failure_count": self._circuit_failure_count,
            "circuit_blocked_count": self._circuit_blocked_count,
            "circuit_healthy": not circuit_state or circuit_state.state == "closed",
        }
