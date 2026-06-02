"""Skill system for DIKIWI.

Skills are on-demand capabilities loaded per stage.
Inspired by Clowder AI's skill framework and gstack's slash commands.
"""

from aily.dikiwi.skills.base import Skill, SkillContext, SkillResult
from aily.dikiwi.skills.registry import (
    SkillMetadata,
    SkillRegistry,
    get_skill_registry,
    reset_skill_registry,
)

__all__ = [
    "Skill",
    "SkillContext",
    "SkillResult",
    "SkillMetadata",
    "SkillRegistry",
    "get_skill_registry",
    "reset_skill_registry",
]