"""Producer agent - generates draft LLM outputs."""

from __future__ import annotations

from typing import Any

from aily.dikiwi.agents.llm_tools import chat_json
from aily.sessions.dikiwi_mind import LLMUsageBudget


class ProducerAgent:
    """Agent that produces a draft JSON response via LLM."""

    async def execute(
        self,
        *,
        llm_client: Any,
        stage: str,
        stage_key: str,
        messages: list[dict[str, str]],
        temperature: float,
        budget: LLMUsageBudget | None = None,
    ) -> Any:
        """Generate a draft JSON response.

        Args:
            llm_client: Object with async chat_json method.
            stage: Human-readable stage name.
            stage_key: Budget bucket key.
            messages: LLM message list.
            temperature: Sampling temperature.
            budget: Optional usage budget.

        Returns:
            Parsed JSON response.
        """
        return await chat_json(
            llm_client=llm_client,
            stage=stage,
            stage_key=stage_key,
            messages=messages,
            temperature=temperature,
            budget=budget,
        )
