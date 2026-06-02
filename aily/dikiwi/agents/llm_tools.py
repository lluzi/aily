"""Shared LLM helpers for DIKIWI agents.

Encapsulates budget-aware LLM calls and the producer-reviewer pattern
so stage agents stay focused on stage logic, not call plumbing.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from aily.llm.conversation_logger import get_conversation_logger
from aily.llm.prompt_registry import DikiwiPromptRegistry
from aily.ui.telemetry import ui_telemetry_scope

if TYPE_CHECKING:
    from aily.sessions.dikiwi_mind import LLMUsageBudget

logger = logging.getLogger(__name__)


async def chat_json(
    *,
    llm_client: Any,
    stage: str,
    stage_key: str | None = None,
    messages: list[dict[str, str]],
    temperature: float,
    budget: LLMUsageBudget | None = None,
) -> Any:
    """Budget-aware LLM JSON call.

    Args:
        llm_client: Object with async chat_json(messages, temperature) method.
        stage: Human-readable stage name for logging.
        stage_key: Budget bucket key (defaults to stage).
        messages: LLM message list.
        temperature: Sampling temperature.
        budget: Optional usage budget to reserve against.

    Returns:
        Parsed JSON response from the LLM.
    """
    reserve_key = stage_key or stage
    if budget is not None:
        budget.reserve(reserve_key)
        logger.info(
            "[DIKIWI] LLM call stage=%s key=%s used=%s stage_used=%s/%s",
            stage,
            reserve_key,
            budget.calls_used,
            budget.stage_calls.get(reserve_key, 0),
            budget.stage_round_limit,
        )
    stage_name = str(stage or "").strip().lower()
    workload = f"dikiwi.{stage_name}" if stage_name else None
    with ui_telemetry_scope(stage=stage_name.upper() if stage_name else None, workload=workload):
        result = await llm_client.chat_json(messages=messages, temperature=temperature)
    get_conversation_logger().log(
        stage=stage,
        stage_key=reserve_key,
        messages=messages,
        response=result,
        temperature=temperature,
    )
    return result


async def multi_agent_json(
    *,
    llm_client: Any,
    stage: str,
    stage_key: str,
    producer_messages: list[dict[str, str]],
    reviewer_messages_factory: Any,
    temperature: float,
    budget: LLMUsageBudget | None = None,
) -> Any:
    """Producer-reviewer pattern: two LLM calls with budget enforcement.

    Args:
        llm_client: Object with async chat_json(messages, temperature) method.
        stage: Human-readable stage name.
        stage_key: Budget bucket key.
        producer_messages: Messages for the producer (draft generation).
        reviewer_messages_factory: Callable that takes draft_json string and
            returns reviewer messages.
        temperature: Producer temperature. Reviewer gets max(0.1, temp - 0.05).
        budget: Optional usage budget to reserve against.

    Returns:
        Reviewed JSON if the reviewer returns a valid dict, otherwise the draft.
    """
    draft = await chat_json(
        llm_client=llm_client,
        stage=stage,
        stage_key=stage_key,
        messages=producer_messages,
        temperature=temperature,
        budget=budget,
    )

    if not isinstance(draft, dict):
        return draft

    draft_json = json.dumps(draft, ensure_ascii=False, indent=2)
    review_messages = reviewer_messages_factory(draft_json)

    try:
        reviewed = await chat_json(
            llm_client=llm_client,
            stage=stage,
            stage_key=stage_key,
            messages=review_messages,
            temperature=max(0.1, temperature - 0.05),
            budget=budget,
        )
        if isinstance(reviewed, dict):
            return reviewed
    except Exception as exc:
        logger.warning("[DIKIWI] Reviewer agent failed for %s (%s): %s", stage, stage_key, exc)

    return draft
