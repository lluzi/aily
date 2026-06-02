"""Intelligent tagging engine for multimodal content.

Combines content-based, LLM-based, and knowledge graph-based tagging.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aily.chaos.tagger.content_based import ContentBasedTagger
from aily.chaos.tagger.llm_based import LLMBasedTagger

if TYPE_CHECKING:
    from aily.chaos.config import ChaosConfig
    from aily.chaos.types import ExtractedContentMultimodal

logger = logging.getLogger(__name__)


class IntelligentTagger:
    """Multi-layer intelligent tagging system."""

    def __init__(self, config: "ChaosConfig") -> None:
        self.config = config
        self.content_tagger = ContentBasedTagger()
        self.llm_tagger = LLMBasedTagger(config)

    async def tag(self, content: "ExtractedContentMultimodal") -> list[str]:
        """Generate comprehensive tags for content.

        Combines multiple tagging strategies for rich metadata.

        Args:
            content: Extracted content to tag

        Returns:
            List of unique tags
        """
        all_tags: set[str] = set()

        # Layer 1: Content-based tags (fast, deterministic)
        if self.config.tagging.content_based:
            content_tags = self.content_tagger.tag(content)
            all_tags.update(content_tags)
            logger.debug("Content-based tags: %s", content_tags)

        # Layer 2: LLM-based tags (semantic understanding)
        if self.config.tagging.llm_based:
            try:
                llm_tags = await self.llm_tagger.tag(content)
                all_tags.update(llm_tags)
                logger.debug("LLM-based tags: %s", llm_tags)
            except Exception as e:
                logger.warning("LLM tagging failed: %s", e)

        # Layer 3: Knowledge graph tags (relationship-based)
        # TODO: Implement in Phase 3

        # Normalize and limit
        tags = self._normalize_tags(all_tags)
        tags = self._limit_tags(tags, self.config.tagging.max_tags)

        logger.info("Generated %d tags for %s", len(tags), content.title or "content")
        return tags

    def _normalize_tags(self, tags: set[str]) -> list[str]:
        """Normalize tag format."""
        normalized: set[str] = set()

        for tag in tags:
            # Convert to lowercase
            tag = tag.lower().strip()

            # Replace spaces with hyphens
            tag = tag.replace(" ", "-")

            # Remove special characters except hyphens
            tag = "".join(c for c in tag if c.isalnum() or c == "-")

            # Remove leading/trailing hyphens
            tag = tag.strip("-")

            # Skip empty or too short tags
            if len(tag) < 2:
                continue

            normalized.add(tag)

        return sorted(normalized)

    def _limit_tags(self, tags: list[str], max_tags: int) -> list[str]:
        """Limit number of tags by priority."""
        if len(tags) <= max_tags:
            return tags

        # Priority order:
        # 1. Domain tags (eda, ai, semiconductor)
        # 2. Type tags (document, video, concept)
        # 3. Entity tags (company names, technologies)
        # 4. Generic tags

        priority_keywords = {
            # High priority domain tags
            "eda", "ai", "semiconductor", "architecture", "design",
            "mcp", "llm", "agent", "verification", "synthesis",
            # Type tags
            "document", "video", "image", "presentation",
            "concept", "methodology", "pattern", "framework",
        }

        # Sort by priority
        def priority(tag: str) -> int:
            if tag in priority_keywords:
                return 0
            if any(kw in tag for kw in priority_keywords):
                return 1
            return 2

        sorted_tags = sorted(tags, key=priority)
        return sorted_tags[:max_tags]
