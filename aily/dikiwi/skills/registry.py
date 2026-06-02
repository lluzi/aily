"""Skill registry for on-demand capability loading.

Skills are loaded based on stage and content type, not baked into agents.
Inspired by Clowder AI's skill framework.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aily.dikiwi.skills.base import Skill, SkillContext, SkillResult
from aily.dikiwi.stages import DikiwiStage

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Max cached skill instances (LRU cache size)
MAX_CACHED_SKILLS = 100


@dataclass(frozen=True)
class SkillMetadata:
    """Metadata for a registered skill."""

    name: str
    description: str
    version: str
    target_stages: list[str]
    content_types: list[str]
    skill_class: type[Skill]


class SkillRegistry:
    """Registry for discovering and loading skills on-demand.

    Skills are registered by:
    - Stage they apply to (e.g., INFORMATION, INSIGHT)
    - Content type they handle (e.g., "tech_content", "business_content")

    The registry loads skills lazily and caches instances.
    """

    # Default skill mappings: (stage, content_type) -> [skill_names]
    DEFAULT_SKILL_MAP: dict[tuple[str, str], list[str]] = {
        # Stage 2: INFORMATION (中书省 - classification)
        ("information", "tech_content"): [
            "tag_extraction",
            "tech_classification",
            "code_clustering",
        ],
        ("information", "business_content"): [
            "tag_extraction",
            "business_classification",
            "entity_extraction",
        ],
        ("information", "general"): [
            "tag_extraction",
            "general_classification",
        ],
        # Stage 4: INSIGHT (尚书省 - pattern detection)
        ("insight", "tech_content"): [
            "pattern_detection",
            "contradiction_analysis",
            "code_pattern_skill",
        ],
        ("insight", "business_content"): [
            "market_pattern_skill",
            "competitive_analysis",
            "gap_analysis",
        ],
        ("insight", "general"): [
            "pattern_detection",
            "gap_analysis",
        ],
        # Stage 5: WISDOM (吏部 - synthesis)
        ("wisdom", "tech_content"): [
            "synthesis",
            "framework_generation",
            "tech_contextualization",
        ],
        ("wisdom", "business_content"): [
            "synthesis",
            "decision_framework",
            "market_contextualization",
        ],
        ("wisdom", "general"): [
            "synthesis",
            "principle_extraction",
        ],
    }

    def __init__(self, max_cache_size: int = MAX_CACHED_SKILLS) -> None:
        self._skill_classes: dict[str, type[Skill]] = {}
        self._skill_metadata: dict[str, SkillMetadata] = {}
        self._skill_map = dict(self.DEFAULT_SKILL_MAP)

        # LRU cache for skill instances with bounded size
        self._max_cache_size = max_cache_size
        self._skill_instances: dict[str, Skill] = {}
        self._access_order: list[str] = []  # Tracks LRU order

        # Failure tracking for metrics/health monitoring
        self._failed_skills: list[dict[str, Any]] = []

        # Auto-discover built-in skills
        self._discover_builtin_skills()

    def _discover_builtin_skills(self) -> None:
        """Auto-discover skills from builtin directory."""
        builtin_dir = Path(__file__).parent / "builtin"
        if not builtin_dir.exists():
            return

        for skill_file in builtin_dir.glob("*.py"):
            if skill_file.name.startswith("_"):
                continue

            skill_name = skill_file.stem
            try:
                self._load_skill_class(skill_name)
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", skill_name, e)
                # Track failure for metrics/health monitoring
                self._failed_skills.append({
                    "skill_name": skill_name,
                    "error": str(e),
                    "stage": "discovery",
                    "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                })

    def _load_skill_class(self, name: str) -> type[Skill]:
        """Load a skill class by name."""
        if name in self._skill_classes:
            return self._skill_classes[name]

        # Try builtin skills first
        module_path = f"aily.dikiwi.skills.builtin.{name}"
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            # Try full module path
            try:
                module = importlib.import_module(name)
            except ImportError as e:
                raise ValueError(f"Cannot import skill {name}: {e}") from e

        # Find Skill subclass
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Skill) and obj is not Skill:
                self._skill_classes[name] = obj
                self._skill_metadata[name] = self._extract_metadata(name, obj)
                return obj

        raise ValueError(f"No Skill subclass found in {module_path}")

    def _extract_metadata(self, name: str, skill_class: type[Skill]) -> SkillMetadata:
        """Extract metadata from a skill class."""
        # Create temporary instance to read metadata
        try:
            temp_instance = skill_class()
            return SkillMetadata(
                name=temp_instance.name,
                description=temp_instance.description,
                version=temp_instance.version,
                target_stages=temp_instance.target_stages,
                content_types=temp_instance.content_types,
                skill_class=skill_class,
            )
        except Exception as e:
            logger.warning("Failed to extract metadata for %s: %s", name, e)
            return SkillMetadata(
                name=name,
                description="",
                version="1.0.0",
                target_stages=[],
                content_types=["*"],
                skill_class=skill_class,
            )

    def _get_cached_skill(self, name: str) -> Skill | None:
        """Get skill from LRU cache, updating access order."""
        if name not in self._skill_instances:
            return None
        # Update access order (move to end = most recently used)
        if name in self._access_order:
            self._access_order.remove(name)
        self._access_order.append(name)
        return self._skill_instances[name]

    def _cache_skill(self, name: str, skill: Skill) -> None:
        """Add skill to LRU cache, evicting oldest if at capacity."""
        # Evict oldest if at capacity
        while len(self._skill_instances) >= self._max_cache_size and self._access_order:
            oldest = self._access_order.pop(0)
            if oldest in self._skill_instances:
                del self._skill_instances[oldest]
                logger.debug("Evicted skill from cache: %s", oldest)

        # Add new skill
        self._skill_instances[name] = skill
        self._access_order.append(name)

    def register_skill(
        self,
        name: str,
        skill_class: type[Skill],
        stages: list[str] | None = None,
        content_types: list[str] | None = None,
    ) -> None:
        """Register a skill class.

        Args:
            name: Unique skill identifier
            skill_class: The Skill subclass to register
            stages: Optional override for target stages
            content_types: Optional override for content types
        """
        self._skill_classes[name] = skill_class

        # Create instance to read metadata
        temp = skill_class()
        self._skill_metadata[name] = SkillMetadata(
            name=name,
            description=temp.description,
            version=temp.version,
            target_stages=stages or temp.target_stages,
            content_types=content_types or temp.content_types,
            skill_class=skill_class,
        )

        logger.debug("Registered skill: %s@%s", name, temp.version)

    def register_skill_mapping(
        self,
        stage: str | DikiwiStage,
        content_type: str,
        skill_names: list[str],
    ) -> None:
        """Register which skills to use for a stage/content-type combination.

        Args:
            stage: The DIKIWI stage
            content_type: Content type identifier
            skill_names: List of skill names to load
        """
        stage_key = stage.name.lower() if isinstance(stage, DikiwiStage) else str(stage).lower()
        self._skill_map[(stage_key, content_type)] = skill_names
        logger.debug(
            "Registered skill mapping: (%s, %s) -> %s",
            stage_key,
            content_type,
            skill_names,
        )

    async def classify_content_type(self, content: str) -> str:
        """Classify content to determine which skills to use.

        This is a lightweight classifier that doesn't use LLM.
        For more sophisticated classification, use a classification skill.

        Returns:
            Content type: "tech_content", "business_content", or "general"
        """
        content_lower = content.lower()

        # Simple heuristics
        tech_indicators = [
            "code",
            "function",
            "api",
            "database",
            "server",
            "client",
            "python",
            "javascript",
            "typescript",
            "github",
        ]
        business_indicators = [
            "revenue",
            "customer",
            "market",
            "competitor",
            "strategy",
            "growth",
            "funding",
            "investor",
            "sales",
            "marketing",
        ]

        tech_score = sum(1 for ind in tech_indicators if ind in content_lower)
        business_score = sum(1 for ind in business_indicators if ind in content_lower)

        if tech_score > business_score and tech_score > 1:
            return "tech_content"
        elif business_score > tech_score and business_score > 1:
            return "business_content"
        return "general"

    async def get_skills(
        self,
        stage: str | DikiwiStage,
        content: str,
    ) -> list[Skill]:
        """Get skills for a stage and content.

        Args:
            stage: The DIKIWI stage
            content: Content to process (used for type classification)

        Returns:
            List of skill instances ready to execute
        """
        stage_key = stage.name.lower() if isinstance(stage, DikiwiStage) else str(stage).lower()
        content_type = await self.classify_content_type(content)

        # Get skill names from mapping
        skill_names = self._skill_map.get(
            (stage_key, content_type),
            self._skill_map.get((stage_key, "general"), []),
        )

        # Load and cache instances
        instances = []
        for name in skill_names:
            skill = await self.load_skill(name)
            if skill:
                instances.append(skill)

        return instances

    async def load_skill(self, name: str) -> Skill | None:
        """Load a skill by name.

        Args:
            name: Skill identifier

        Returns:
            Skill instance or None if not found
        """
        # Return cached instance (LRU order updated in _get_cached_skill)
        cached = self._get_cached_skill(name)
        if cached:
            return cached

        # Load class
        try:
            skill_class = self._load_skill_class(name)
        except ValueError as e:
            logger.error("Skill not found: %s - %s", name, e)
            self._failed_skills.append({
                "skill_name": name,
                "error": str(e),
                "stage": "load_class",
                "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            })
            return None

        # Create instance
        try:
            instance = skill_class()
            self._cache_skill(name, instance)
            return instance
        except Exception as e:
            logger.exception("Failed to instantiate skill %s: %s", name, e)
            self._failed_skills.append({
                "skill_name": name,
                "error": str(e),
                "stage": "instantiation",
                "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            })
            return None

    def get_skill_metadata(self, name: str) -> SkillMetadata | None:
        """Get metadata for a registered skill."""
        if name not in self._skill_metadata:
            # Try to load it
            try:
                self._load_skill_class(name)
            except ValueError:
                return None
        return self._skill_metadata.get(name)

    def list_skills(self) -> list[SkillMetadata]:
        """List all registered skills."""
        return list(self._skill_metadata.values())

    def get_skills_for_stage(self, stage: str | DikiwiStage) -> list[SkillMetadata]:
        """Get all skills that can handle a stage."""
        stage_key = stage.name.lower() if isinstance(stage, DikiwiStage) else str(stage).lower()

        return [
            meta
            for meta in self._skill_metadata.values()
            if not meta.target_stages or stage_key in [
                s.lower() for s in meta.target_stages
            ]
        ]

    async def execute_skills(
        self,
        stage: str | DikiwiStage,
        context: SkillContext,
    ) -> list[SkillResult]:
        """Execute all applicable skills for a stage.

        Args:
            stage: Current DIKIWI stage
            context: Execution context with content, LLM, etc.

        Returns:
            List of skill execution results
        """
        skills = await self.get_skills(stage, context.content)
        results = []

        for skill in skills:
            if skill.can_handle(
                stage.name.lower() if isinstance(stage, DikiwiStage) else str(stage).lower(),
                context.metadata.get("content_type", "*"),
            ):
                try:
                    result = await skill.run(context)
                    results.append(result)
                except Exception as e:
                    logger.exception("Skill %s failed: %s", skill.name, e)
                    results.append(
                        SkillResult.error_result(skill.name, str(e))
                    )

        return results

    def clear_cache(self) -> None:
        """Clear cached skill instances and LRU tracking."""
        self._skill_instances.clear()
        self._access_order.clear()
        logger.debug("Skill cache cleared")

    def get_metrics(self) -> dict[str, Any]:
        """Get registry metrics including failed skills and cache health."""
        # Calculate recent failures (last 24 hours)
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_failures = [
            f for f in self._failed_skills
            if datetime.fromisoformat(f["timestamp"]) > cutoff
        ]

        return {
            "registered_skills": len(self._skill_classes),
            "cached_instances": len(self._skill_instances),
            "max_cache_size": self._max_cache_size,
            "cache_utilization": len(self._skill_instances) / max(self._max_cache_size, 1),
            "skill_mappings": len(self._skill_map),
            "failed_skills_total": len(self._failed_skills),
            "failed_skills_recent_24h": len(recent_failures),
            "failed_skill_details": self._failed_skills[-10:] if self._failed_skills else [],
            "health_status": "degraded" if recent_failures else "healthy",
        }


# Global registry instance
_global_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    """Get the global skill registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
    return _global_registry


def reset_skill_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _global_registry
    _global_registry = None
