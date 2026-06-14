"""Daily LLM spend guard: counts, caps, resets."""

import pytest

from aily.llm import spend_guard


def setup_function():
    spend_guard.reset()


def test_disabled_when_zero():
    for _ in range(100):
        spend_guard.record_call(0)  # no cap -> never raises
    assert spend_guard.calls_today() == 0  # disabled path doesn't count


def test_cap_raises_after_ceiling():
    spend_guard.record_call(3)
    spend_guard.record_call(3)
    spend_guard.record_call(3)
    assert spend_guard.calls_today() == 3
    with pytest.raises(spend_guard.SpendCapExceeded):
        spend_guard.record_call(3)


def test_reset_clears_counter():
    spend_guard.record_call(5)
    spend_guard.reset()
    assert spend_guard.calls_today() == 0
