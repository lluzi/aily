"""Reviewer agent - validates and refines draft LLM outputs."""

from __future__ import annotations

from typing import Any

from aily.dikiwi.agents.llm_tools import multi_agent_json
from aily.sessions.dikiwi_mind import LLMUsageBudget


class ReviewerAgent:
    """Agent that reviews a draft JSON response via LLM."""

    async def execute(
        self,
        *,
        llm_client: Any,
        stage: str,
        stage_key: str,
        producer_messages: list[dict[str, str]],
        reviewer_messages_factory: Any,
        temperature: float,
        budget: LLMUsageBudget | None = None,
    ) -> Any:
        """Generate and review a JSON response.

        Args:
            llm_client: Object with async chat_json method.
            stage: Human-readable stage name.
            stage_key: Budget bucket key.
            producer_messages: Messages for the producer (draft generation).
            reviewer_messages_factory: Callable that takes draft_json string and
                returns reviewer messages.
            temperature: Producer temperature. Reviewer gets max(0.1, temp - 0.05).
            budget: Optional usage budget.

        Returns:
            Reviewed JSON if valid, otherwise the original draft.
        """
        return await multi_agent_json(
            llm_client=llm_client,
            stage=stage,
            stage_key=stage_key,
            producer_messages=producer_messages,
            reviewer_messages_factory=reviewer_messages_factory,
            temperature=temperature,
            budget=budget,
        )
