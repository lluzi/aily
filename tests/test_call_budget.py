"""Size-aware per-source LLM call budget (M7)."""

from aily.config import SETTINGS
from aily.sessions.dikiwi_mind import DikiwiMind


def test_small_doc_uses_base_cap():
    assert DikiwiMind._max_calls_for_content("short note") == SETTINGS.dikiwi_max_llm_calls_per_source


def test_large_doc_scales_up_bounded():
    base = SETTINGS.dikiwi_max_llm_calls_per_source
    hard = SETTINGS.dikiwi_max_llm_calls_hard_cap
    mid = DikiwiMind._max_calls_for_content("x" * 40000)  # 8k base + 32k/4k = +8
    assert mid > base
    assert mid <= hard
    huge = DikiwiMind._max_calls_for_content("x" * 5_000_000)
    assert huge == hard  # clamped at the hard ceiling
