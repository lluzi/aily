"""Kimi LLM Client - Moonshot API integration for DIKIWI.

Uses the OpenAI-compatible Kimi Open Platform at https://api.moonshot.cn/v1.
Recommended model: kimi-k2.6
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aily.llm.client import LLMClient

logger = logging.getLogger(__name__)


class KimiClient(LLMClient):
    """Kimi API client for LLM-based DIKIWI processing.

    Kimi API is OpenAI-compatible. This client provides:
    - Long context (up to 128k tokens)
    - JSON mode for structured outputs
    - No hardcoded fallbacks - pure LLM reasoning

    Usage:
        client = KimiClient(api_key="sk-...")
        result = await client.chat_json(messages=[...])
    """

    DEFAULT_MODEL = "kimi-k2.6"
    BASE_URL = "https://api.moonshot.cn/v1"
    CHAT_COMPLETIONS_URL = f"{BASE_URL}/chat/completions"

    @staticmethod
    def resolve_api_key(explicit_api_key: str = "") -> str:
        """Resolve Kimi credentials from explicit input or common env names."""
        return (
            explicit_api_key
            or os.getenv("KIMI_API_KEY", "")
            or os.getenv("MOONSHOT_API_KEY", "")
            or os.getenv("LLM_API_KEY", "")
        )

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = 300.0,
        max_retries: int = 2,
        thinking: bool = False,
        max_concurrency: int = 1,
        min_interval_seconds: float = 0.0,
    ) -> None:
        """Initialize Kimi client.

        Args:
            api_key: Kimi API key (from https://platform.kimi.com)
            model: Model name (recommended: kimi-k2.6)
            timeout: Request timeout in seconds (higher for long contexts)
            max_retries: Number of retries on failure
            thinking: Enable thinking mode for kimi-k2.x models (default False for batch speed)
        """
        super().__init__(
            base_url=self.BASE_URL,
            api_key=self.resolve_api_key(api_key),
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            thinking=thinking,
            max_concurrency=max_concurrency,
            min_interval_seconds=min_interval_seconds,
        )
        logger.info("Kimi client initialized with model: %s (thinking: %s)", model, thinking)

    async def analyze_with_reasoning(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """Analyze content with explicit reasoning.

        Unlike hardcoded rules, this uses LLM to reason through the task
        and provide structured output with confidence scores.

        Args:
            prompt: The analysis prompt
            system_prompt: Optional system context
            temperature: Sampling temperature

        Returns:
            Dict with 'result', 'reasoning', and 'confidence' keys
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Request structured JSON output
        response = await self.chat_json(messages, temperature=temperature)

        if not isinstance(response, dict):
            logger.warning("Kimi returned non-JSON response, wrapping")
            return {
                "result": str(response),
                "reasoning": "Response was not in expected JSON format",
                "confidence": 0.5,
            }

        return response

    async def extract_structured(
        self,
        content: str,
        extraction_schema: dict[str, Any],
        context: str = "",
    ) -> dict[str, Any]:
        """Extract structured data using LLM.

        Replaces rule-based extraction with LLM-powered understanding.

        Args:
            content: Raw content to extract from
            extraction_schema: Schema describing expected output
            context: Additional context for extraction

        Returns:
            Structured extraction result
        """
        schema_desc = json.dumps(extraction_schema, indent=2)

        prompt = f"""Extract structured information from the following content.

Context: {context}

Content:
---
{content[:15000]}
---

Extraction Schema:
{schema_desc}

Provide the extraction as valid JSON matching the schema above.
Include confidence scores (0.0-1.0) for each extracted item."""

        try:
            result = await self.chat_json(
                messages=[{
                    "role": "system",
                    "content": "You are an expert information extraction system. Extract accurate, structured data from content."
                }, {"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return result if isinstance(result, dict) else {"extracted": result}
        except Exception as exc:
            logger.error("Kimi extraction failed: %s", exc)
            raise

    async def classify_semantic(
        self,
        content: str,
        categories: list[str],
        context: str = "",
    ) -> dict[str, Any]:
        """Classify content into categories using semantic understanding.

        Replaces keyword-based classification with LLM semantic understanding.

        Args:
            content: Content to classify
            categories: List of possible categories
            context: Classification context

        Returns:
            Dict with 'category', 'confidence', and 'reasoning'
        """
        categories_str = "\n".join(f"- {cat}" for cat in categories)

        prompt = f"""Classify the following content into one of the categories.

Context: {context}

Categories:
{categories_str}

Content:
---
{content}
---

Respond with JSON:
{{
    "category": "selected_category",
    "confidence": 0.0-1.0,
    "reasoning": "Why this category fits",
    "alternative_categories": ["other", "possible", "categories"]
}}"""

        result = await self.chat_json(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        if not isinstance(result, dict):
            return {
                "category": categories[0] if categories else "unknown",
                "confidence": 0.0,
                "reasoning": "Classification failed",
            }

        return result

    async def synthesize(
        self,
        items: list[dict[str, Any]],
        synthesis_goal: str,
        output_format: str = "principles",
    ) -> dict[str, Any]:
        """Synthesize multiple items into higher-level understanding.

        Replaces rule-based synthesis with LLM-powered synthesis.

        Args:
            items: Items to synthesize
            synthesis_goal: What we're trying to understand
            output_format: Desired output format

        Returns:
            Synthesis result with principles/insights
        """
        items_desc = "\n".join(
            f"- [{i.get('type', 'item')}] {i.get('content', str(i))[:200]}"
            for i in items[:20]
        )

        prompt = f"""Synthesize the following items into {output_format}.

Goal: {synthesis_goal}

Items:
{items_desc}

Provide synthesis as JSON:
{{
    "synthesis": [
        {{
            "principle": "Core insight",
            "reasoning": "Why this emerges from the data",
            "supporting_items": [0, 1, 2],
            "confidence": 0.0-1.0
        }}
    ],
    "patterns": ["pattern1", "pattern2"],
    "contradictions": ["any contradictions found"],
    "gaps": ["any knowledge gaps"]
}}"""

        result = await self.chat_json(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )

        return result if isinstance(result, dict) else {"synthesis": [], "patterns": []}


import json  # noqa: E402 - import at end to avoid circular issues
