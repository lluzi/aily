"""Soft daily LLM-call ceiling — a guardrail for unattended operation.

A single huge batch dropped into the inbox could otherwise run the per-source
budget (bounded) across an unbounded number of sources. This caps aggregate
calls per calendar day. In-memory (resets on restart and at midnight); intended
as a soft safety net, not accounting. Disabled when max_calls <= 0.
"""

from __future__ import annotations

from datetime import datetime


class SpendCapExceeded(Exception):
    """Raised when the daily LLM call ceiling is reached."""


_state: dict[str, object] = {"date": None, "calls": 0}


def record_call(max_calls: int) -> None:
    """Count one LLM call; raise SpendCapExceeded if the daily ceiling is passed."""
    if not max_calls or max_calls <= 0:
        return
    today = datetime.now().date()
    if _state["date"] != today:
        _state["date"] = today
        _state["calls"] = 0
    _state["calls"] = int(_state["calls"]) + 1  # type: ignore[arg-type]
    if int(_state["calls"]) > max_calls:  # type: ignore[arg-type]
        raise SpendCapExceeded(
            f"Daily LLM call budget exceeded ({max_calls} calls); resets at midnight"
        )


def calls_today() -> int:
    return int(_state["calls"])  # type: ignore[arg-type]


def reset() -> None:
    _state["date"] = None
    _state["calls"] = 0
