"""LLM Router - Unified interface for multiple LLM backends.

Supports:
1. Standard API (per-token billing): Kimi, OpenAI, Anthropic, etc.
2. Coding Plan (fixed monthly): ByteDance Ark, Aliyun Bailian

Usage:
    # Standard API for data processing
    llm = LLMRouter.standard_kimi(api_key="sk-...")

    # Coding Plan for interactive coding
    llm = LLMRouter.coding_plan_ark(api_key="sk-sp-...", model="kimi-k2.5")

    # Auto-select based on task type
    llm = LLMRouter.for_task("data_extraction", config)
    llm = LLMRouter.for_task("code_generation", config)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol

from aily.llm.client import LLMClient
from aily.llm.kimi_client import KimiClient
from aily.llm.coding_plan_client import CodingPlanClient

logger = logging.getLogger(__name__)


class APIType(Enum):
    """Types of LLM APIs available."""

    STANDARD = auto()  # Per-token billing (Kimi, OpenAI, etc.)
    CODING_PLAN = auto()  # Fixed monthly (Ark, Bailian)


@dataclass
class LLMConfig:
    """Configuration for LLM selection."""

    # Standard API settings
    standard_api_key: str = ""
    standard_base_url: str = "https://api.moonshot.cn/v1"
    standard_model: str = "kimi-k2.6"

    # Coding Plan settings
    coding_plan_api_key: str = ""
    coding_plan_provider: str = "ark"  # ark, bailian, zhipu
    coding_plan_model: str = "kimi-k2.6"
    coding_plan_base_url: str = ""

    # Routing preferences
    prefer_coding_plan_for: list[str] = None  # Tasks to route to coding plan
    prefer_standard_for: list[str] = None  # Tasks to route to standard API

    def __post_init__(self):
        if self.prefer_coding_plan_for is None:
            self.prefer_coding_plan_for = [
                "code_generation",
                "code_review",
                "interactive_coding",
                "architecture_design",
            ]
        if self.prefer_standard_for is None:
            self.prefer_standard_for = [
                "data_extraction",
                "classification",
                "batch_processing",
                "information_synthesis",
            ]


class LLMInterface(Protocol):
    """Protocol for LLM clients."""

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        response_format: dict[str, str] | None = None,
    ) -> str: ...

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> Any: ...


class LLMRouter:
    """Routes LLM requests to appropriate backend.

    Decides between Standard API and Coding Plan based on:
    1. Task type (data processing vs coding)
    2. Availability (which keys are configured)
    3. Cost optimization (batch vs interactive)
    """

    @staticmethod
    def standard_kimi(
        api_key: str,
        model: str = "kimi-k2.6",
        thinking: bool = False,
        max_concurrency: int = 1,
        min_interval_seconds: float = 6.0,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> KimiClient:
        """Create standard Kimi API client.

        Best for:
        - Data extraction and processing
        - Batch operations
        - Information classification
        - Long context processing (128k)

        Pricing: Per-token (input/output)
        """
        return KimiClient(
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            thinking=thinking,
            max_concurrency=max_concurrency,
            min_interval_seconds=min_interval_seconds,
        )

    @staticmethod
    def coding_plan_ark(
        api_key: str,
        model: str = "kimi-k2.6",
    ) -> CodingPlanClient:
        """Create ByteDance Ark Coding Plan client.

        Best for:
        - Interactive coding workflows
        - Code generation and review
        - Architecture discussions
        - Real-time assistance

        Pricing: Fixed monthly (¥40-200/month)
        """
        return CodingPlanClient.from_provider(
            provider="ark",
            api_key=api_key,
            model=model,
        )

    @staticmethod
    def coding_plan_bailian(
        api_key: str,
        model: str = "qwen3.5-plus",
    ) -> CodingPlanClient:
        """Create Aliyun Bailian Coding Plan client.

        Best for:
        - Qwen models with image understanding
        - Interactive coding workflows
        - Chinese language tasks

        Pricing: Fixed monthly (¥40-200/month)
        """
        return CodingPlanClient.from_provider(
            provider="bailian",
            api_key=api_key,
            model=model,
        )

    @staticmethod
    def coding_plan_zhipu(
        api_key: str,
        model: str = "glm-4.7",
    ) -> CodingPlanClient:
        """Create Zhipu AI Coding Plan client.

        Best for:
        - GLM models with strong Chinese performance
        - Anthropic-compatible tools

        Pricing: Fixed monthly
        """
        return CodingPlanClient.from_provider(
            provider="zhipu",
            api_key=api_key,
            model=model,
        )

    @staticmethod
    def standard_zhipu(
        api_key: str,
        model: str = "glm-5.1",
        max_concurrency: int = 1,
        min_interval_seconds: float = 0.0,
        timeout: float = 120.0,
        max_retries: int = 0,
    ) -> KimiClient:
        """Create standard Zhipu AI client (OpenAI-compatible API).

        Best for:
        - Data extraction and processing
        - Batch operations
        - Information classification
        - GLM-4-Flash (free tier)

        Pricing: Per-token (input/output), GLM-4-Flash is free
        """
        from aily.llm.client import LLMClient

        return LLMClient(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            min_interval_seconds=min_interval_seconds,
        )

    @staticmethod
    def standard_deepseek(
        api_key: str,
        model: str = "deepseek-v4-pro",
        thinking: bool = False,
        max_concurrency: int = 1,
        min_interval_seconds: float = 0.0,
        timeout: float = 120.0,
        max_retries: int = 0,
    ) -> LLMClient:
        """Create standard DeepSeek client (OpenAI-compatible API)."""
        return LLMClient(
            base_url="https://api.deepseek.com",
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            thinking=thinking,
            max_concurrency=max_concurrency,
            min_interval_seconds=min_interval_seconds,
        )

    @classmethod
    def for_task(cls, task_type: str, config: LLMConfig) -> LLMInterface:
        """Auto-select LLM backend based on task type.

        Args:
            task_type: Type of task (e.g., "data_extraction", "code_generation")
            config: LLM configuration

        Returns:
            Appropriate LLM client for the task
        """
        # Check if task prefers Coding Plan
        if task_type in config.prefer_coding_plan_for and config.coding_plan_api_key:
            logger.info("Routing %s to Coding Plan (interactive task)", task_type)
            if config.coding_plan_provider == "ark":
                return cls.coding_plan_ark(
                    api_key=config.coding_plan_api_key,
                    model=config.coding_plan_model,
                )
            elif config.coding_plan_provider == "bailian":
                return cls.coding_plan_bailian(
                    api_key=config.coding_plan_api_key,
                    model=config.coding_plan_model,
                )
            elif config.coding_plan_provider == "zhipu":
                return cls.coding_plan_zhipu(
                    api_key=config.coding_plan_api_key,
                    model=config.coding_plan_model,
                )

        # Default to Standard API
        if config.standard_api_key:
            logger.info("Routing %s to Standard API (batch/data task)", task_type)
            return cls.standard_kimi(
                api_key=config.standard_api_key,
                model=config.standard_model,
            )

        # Fallback: try Coding Plan if no standard key
        if config.coding_plan_api_key:
            logger.warning("No standard API key, falling back to Coding Plan for %s", task_type)
            return cls.coding_plan_ark(
                api_key=config.coding_plan_api_key,
                model=config.coding_plan_model,
            )

        raise ValueError("No LLM API key configured")

    @classmethod
    def create_dikiwi_mind(
        cls,
        config: LLMConfig,
        graph_db: Any,
        use_coding_plan: bool = False,
    ) -> Any:
        """Create appropriate DIKIWI mind based on configuration.

        Args:
            config: LLM configuration
            graph_db: Graph database instance
            use_coding_plan: Force use of Coding Plan even for data tasks

        Returns:
            DikiwiMindLLM or DikiwiMindCodingPlan
        """
        from aily.sessions.dikiwi_mind_llm import DikiwiMindLLM

        if use_coding_plan and config.coding_plan_api_key:
            # Create Coding Plan client wrapper
            coding_client = cls.coding_plan_ark(
                api_key=config.coding_plan_api_key,
                model=config.coding_plan_model,
            )
            # Note: DikiwiMindLLM expects a client with chat_json method
            # CodingPlanClient has create_message_json
            return DikiwiMindLLM(
                llm_client=coding_client,  # type: ignore
                graph_db=graph_db,
                model=config.coding_plan_model,
            )
        else:
            # Standard Kimi API
            return DikiwiMindLLM(
                kimi_api_key=config.standard_api_key,
                graph_db=graph_db,
                model=config.standard_model,
            )


# Convenience functions for common use cases

def get_llm_for_data_extraction(config: LLMConfig) -> LLMInterface:
    """Get LLM optimized for data extraction tasks.

    Data extraction needs:
    - Long context (for large documents)
    - JSON mode support
    - Reliable structured output
    - Cost-effective for batch processing

    Recommendation: Standard API (Kimi 32k/128k)
    """
    return LLMRouter.for_task("data_extraction", config)


def get_llm_for_classification(config: LLMConfig) -> LLMInterface:
    """Get LLM optimized for classification tasks.

    Classification needs:
    - Fast response
    - Consistent output format
    - Good for high-volume batch processing

    Recommendation: Standard API (Kimi 8k/32k)
    """
    return LLMRouter.for_task("classification", config)


def get_llm_for_coding(config: LLMConfig) -> LLMInterface:
    """Get LLM optimized for coding tasks.

    Coding needs:
    - Interactive response
    - Code understanding and generation
    - Multi-turn conversations
    - Cost-effective for interactive use

    Recommendation: Coding Plan (if available), else Standard
    """
    if config.coding_plan_api_key:
        return LLMRouter.coding_plan_ark(
            api_key=config.coding_plan_api_key,
            model=config.coding_plan_model,
        )
    return LLMRouter.standard_kimi(
        api_key=config.standard_api_key,
        model="kimi-k2.5",
    )


def get_llm_for_synthesis(config: LLMConfig) -> LLMInterface:
    """Get LLM optimized for synthesis tasks (WISDOM stage).

    Synthesis needs:
    - High reasoning capability
    - Long context for multiple inputs
    - Nuanced understanding

    Recommendation: Standard API (Kimi 32k/128k)
    """
    return LLMRouter.for_task("information_synthesis", config)
