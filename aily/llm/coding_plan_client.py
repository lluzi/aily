"""Coding Plan Client - Anthropic-compatible API for interactive coding.

This client is designed for Coding Plan subscriptions from:
- ByteDance Ark (火山方舟): https://ark.cn-beijing.volces.com/api/coding
- Aliyun Bailian: https://coding.dashscope.aliyuncs.com/apps/anthropic

Key differences from standard API:
- Fixed monthly pricing vs per-token billing
- Anthropic-compatible interface (messages API)
- Designed for interactive coding assistants
- May have rate limits for batch processing

Usage:
    client = CodingPlanClient(
        api_key="sk-sp-xxxxx",  # Coding Plan API key
        base_url="https://ark.cn-beijing.volces.com/api/coding",
        model="kimi-k2.5"
    )
    response = await client.messages.create(...)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CodingPlanMessage:
    """Represents a message in the Anthropic format."""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class CodingPlanResponse:
    """Response from Coding Plan API."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id = data.get("id", "")
        self.model = data.get("model", "")
        self.content = data.get("content", [])
        self.usage = data.get("usage", {})
        self.stop_reason = data.get("stop_reason", "")

    @property
    def text(self) -> str:
        """Extract text content from response."""
        if isinstance(self.content, list) and len(self.content) > 0:
            return self.content[0].get("text", "")
        return str(self.content)


class CodingPlanClient:
    """Anthropic-compatible client for Coding Plan subscriptions.

    Supports providers:
    - ByteDance Ark: https://ark.cn-beijing.volces.com/api/coding
    - Aliyun Bailian: https://coding.dashscope.aliyuncs.com/apps/anthropic

    Example:
        client = CodingPlanClient(
            api_key="sk-sp-xxxxx",
            base_url="https://ark.cn-beijing.volces.com/api/coding",
            model="kimi-k2.5"
        )

        response = await client.create_message(
            messages=[
                {"role": "user", "content": "Write a Python function to sort a list"}
            ],
            max_tokens=1024
        )
        print(response.text)
    """

    # Provider configurations
    PROVIDERS = {
        "ark": {
            "name": "ByteDance Ark (火山方舟)",
            "base_url": "https://ark.cn-beijing.volces.com/api/coding",
            "models": [
                "kimi-k2.5",
                "glm-4.7",
                "deepseek-v3.2",
                "minimax-m2.5",
                "doubao-seed-2.0-code",
                "doubao-seed-2.0-pro",
                "doubao-seed-2.0-lite",
                "ark-code-latest",  # Dynamic model selection
            ],
        },
        "bailian": {
            "name": "Aliyun Bailian (百炼)",
            "base_url": "https://coding.dashscope.aliyuncs.com/apps/anthropic",
            "models": [
                "qwen3.5-plus",
                "kimi-k2.5",
                "glm-5",
                "MiniMax-M2.5",
            ],
        },
        "zhipu": {
            "name": "Zhipu AI (智谱)",
            "base_url": "https://open.bigmodel.cn/api/anthropic",
            "models": [
                "glm-5.1",
                "glm-5",
                "glm-4.7",
                "glm-4.5-air",
                "glm-4.5-flash",
            ],
        },
    }

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        """Initialize Coding Plan client.

        Args:
            api_key: Coding Plan API key (format: sk-sp-xxxxx for Bailian)
            base_url: Provider endpoint URL
            model: Model name (e.g., "kimi-k2.5", "glm-4.7")
            timeout: Request timeout
            max_retries: Retry attempts
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

        # Detect provider from base_url for logging
        self.provider = self._detect_provider(base_url)
        logger.info(
            "CodingPlanClient initialized: provider=%s, model=%s",
            self.provider,
            model,
        )

    def _detect_provider(self, base_url: str) -> str:
        """Detect provider from base URL."""
        if "volces.com" in base_url or "ark.cn" in base_url:
            return "ark"
        elif "dashscope" in base_url:
            return "bailian"
        elif "bigmodel.cn" in base_url:
            return "zhipu"
        return "unknown"

    async def create_message(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system: str | None = None,
        stream: bool = False,
    ) -> CodingPlanResponse:
        """Create a message using Anthropic-compatible API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            system: Optional system prompt
            stream: Whether to stream response

        Returns:
            CodingPlanResponse with generated content
        """
        headers = {
            "x-api-key": self.api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": stream,
        }

        if system:
            payload["system"] = system

        # Try request with retries
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/messages",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return CodingPlanResponse(data)

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 429:
                    logger.warning("Coding Plan rate limit hit, retrying...")
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
            except Exception as exc:
                last_error = exc
                logger.warning("Coding Plan request failed (attempt %s): %s", attempt + 1, exc)
                if attempt < self.max_retries:
                    await asyncio.sleep(1)

        raise RuntimeError(f"Coding Plan request failed: {last_error}")

    async def create_message_json(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.3,
        system: str | None = None,
    ) -> dict[str, Any]:
        """Create message and parse response as JSON.

        Useful for structured outputs from Coding Plan models.
        """
        # Add JSON instruction to system prompt
        json_system = system or ""
        json_system += "\nRespond with valid JSON only."

        response = await self.create_message(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            system=json_system.strip(),
        )

        import json
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse Coding Plan response as JSON: %s", exc)
            # Try to extract JSON from markdown code blocks
            text = response.text
            if "```json" in text:
                json_start = text.find("```json") + 7
                json_end = text.find("```", json_start)
                text = text[json_start:json_end].strip()
            elif "```" in text:
                json_start = text.find("```") + 3
                json_end = text.find("```", json_start)
                text = text[json_start:json_end].strip()
            return json.loads(text)

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Compatibility method matching LLMClient.chat_json interface.

        Used by DikiwiMind and other components that expect LLMClient interface.
        """
        # Extract system message if present
        system = None
        filtered_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content")
            else:
                filtered_messages.append(msg)

        return await self.create_message_json(
            messages=filtered_messages,
            max_tokens=4096,
            temperature=temperature,
            system=system,
        )

    @classmethod
    def from_provider(
        cls,
        provider: str,
        api_key: str,
        model: str | None = None,
    ) -> "CodingPlanClient":
        """Create client from provider name.

        Args:
            provider: One of 'ark', 'bailian', 'zhipu'
            api_key: API key for the provider
            model: Model name (defaults to provider's recommended model)

        Returns:
            Configured CodingPlanClient
        """
        config = cls.PROVIDERS.get(provider)
        if not config:
            raise ValueError(f"Unknown provider: {provider}. Use: {list(cls.PROVIDERS.keys())}")

        if model is None:
            # Use first model as default
            model = config["models"][0]

        return cls(
            api_key=api_key,
            base_url=config["base_url"],
            model=model,
        )

    def get_provider_info(self) -> dict[str, Any]:
        """Get information about the configured provider."""
        config = self.PROVIDERS.get(self.provider, {})
        return {
            "provider": self.provider,
            "name": config.get("name", "Unknown"),
            "model": self.model,
            "available_models": config.get("models", []),
            "base_url": self.base_url,
        }


import asyncio  # noqa: E402 - imported at end for typing
