"""Aily Chaos - Multimodal content processing system.

Drop files into the chaos folder and they are automatically processed,
tagged, and converted to Zettelkasten notes.

Usage:
    from aily.chaos import ChaosProcessor

    processor = ChaosProcessor(watch_folder="/Users/luzi/aily_chaos")
    processor.start()
"""

from __future__ import annotations

from aily.chaos.config import ChaosConfig
from aily.chaos.processor import ChaosProcessor
from aily.chaos.types import (
    ExtractedContentMultimodal,
    ProcessingError,
    ProcessingJob,
    VisualElement,
)
from aily.chaos.watcher import FileWatcher

__all__ = [
    "ChaosConfig",
    "ChaosProcessor",
    "ExtractedContentMultimodal",
    "FileWatcher",
    "ProcessingError",
    "ProcessingJob",
    "VisualElement",
]
