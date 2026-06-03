"""Guard the focusing decisions: removed subsystems stay removed, the kept
intake model and foundation-only gate stay in place."""

import importlib.util

import pytest

from aily.config import SETTINGS

REMOVED_MODULES = [
    "aily.sessions.reactor_scheduler",
    "aily.sessions.entrepreneur_scheduler",
    "aily.sessions.gstack_agent",
    "aily.gating.dam",
    "aily.gating.reservoir",
    "aily.gating.channels",
    "aily.thinking.frameworks.triz",
    "aily.thinking.orchestrator",
    "aily.dikiwi.skills",
    "aily.dikiwi.memorials",
    "aily.bot.webhook",
    "aily.push.feishu",
    "aily.voice.transcriber",
    "aily.learning.loop",
    "aily.scheduler.jobs",
]


def _absent(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is None
    except ModuleNotFoundError:
        # Parent package was removed too — still absent.
        return True


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_removed_modules_are_gone(module):
    assert _absent(module), f"{module} should have been removed"


def test_raindrop_intake_model_kept():
    # The RainDrop model is load-bearing even though the rest of gating is cut.
    assert importlib.util.find_spec("aily.gating.drainage") is not None
    from aily.gating.drainage import RainDrop, RainType, StreamType  # noqa: F401


def test_foundation_only_default():
    assert SETTINGS.dikiwi_foundation_only_ingestion is True


def test_mindsconfig_has_no_autonomous_flags():
    assert SETTINGS.minds.dikiwi_enabled is True
    for removed in ("innovation_enabled", "entrepreneur_enabled", "mac_enabled"):
        assert not hasattr(SETTINGS.minds, removed), f"MindsConfig.{removed} should be removed"


def test_app_imports():
    # The trimmed app must import cleanly.
    import aily.main  # noqa: F401
