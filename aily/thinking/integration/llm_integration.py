"""LLM integration for thinking system using Instructor.

Provides structured output validation with automatic retry using the Instructor
library - the industry standard for LLM structured output validation.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

import instructor
from pydantic import BaseModel

from aily.llm.client import LLMClient, LLMError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ThinkingLLMClient:
    """LLM client wrapper with structured output validation via Instructor.

    Uses Instructor library for automatic validation and retry with error
    context feedback to the LLM. This is the recommended 2024 pattern for
    reliable structured LLM outputs.

    Example:
        client = ThinkingLLMClient(base_llm_client)
        result = await client.analyze_with_schema(
            system_prompt="You are an expert...",
            user_content="Analyze this...",
            output_schema=MyPydanticModel,
            temperature=0.3,
        )
    """

    def __init__(
        self,
        base_client: LLMClient,
        max_retries: int = 3,
    ) -> None:
        """Initialize the thinking LLM client.

        Args:
            base_client: The base LLMClient for API calls.
            max_retries: Maximum retries on validation failure.
        """
        self.base_client = base_client
        self.max_retries = max_retries

        # Create instructor client patched with OpenAI-style interface
        # We adapt our LLMClient to work with instructor's interface
        self._instructor_client = self._create_instructor_client()

    def _create_instructor_client(self) -> instructor.AsyncInstructor:
        """Create an instructor client wrapped around our LLM client."""
        # Instructor requires an OpenAI-compatible client
        # We create a thin wrapper that adapts our LLMClient
        from openai import AsyncOpenAI

        # Create OpenAI client with same config as our LLMClient
        openai_client = AsyncOpenAI(
            base_url=self.base_client.base_url,
            api_key=self.base_client.api_key,
            timeout=self.base_client.timeout,
        )

        # Patch with instructor for structured output validation
        return instructor.from_openai(openai_client)

    async def analyze_with_schema(
        self,
        system_prompt: str,
        user_content: str,
        output_schema: type[T],
        temperature: float = 0.3,
        max_tokens: int = 4000,
    ) -> T:
        """Analyze content and return validated structured output.

        Uses Instructor for automatic validation and retry. If validation
        fails, the error context is sent back to the LLM for correction.

        Args:
            system_prompt: System prompt defining the expert persona.
            user_content: User content to analyze.
            output_schema: Pydantic model class for output validation.
            temperature: LLM temperature (lower for consistency).
            max_tokens: Maximum tokens in response.

        Returns:
            Validated instance of output_schema.

        Raises:
            LLMError: If LLM call fails or validation fails after max retries.
        """
        try:
            # Use instructor for structured output with automatic retry
            response = await self._instructor_client.chat.completions.create(
                model=self.base_client.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_model=output_schema,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=self.max_retries,
            )
            return response

        except instructor.exceptions.InstructorRetryException as exc:
            # Instructor exhausted all retries
            logger.error(
                "Instructor failed after %s retries: %s",
                self.max_retries,
                exc,
            )
            raise LLMError(
                f"Structured output validation failed after {self.max_retries} retries: {exc}"
            ) from exc

        except Exception as exc:
            # Other errors (network, etc.)
            logger.error("LLM analysis failed: %s", exc)
            raise LLMError(f"LLM analysis failed: {exc}") from exc

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
    ) -> str:
        """Simple chat completion without structured output.

        Falls back to base client for non-structured calls.

        Args:
            messages: List of message dicts with role and content.
            temperature: LLM temperature.

        Returns:
            Raw LLM response string.
        """
        return await self.base_client.chat(messages, temperature=temperature)
