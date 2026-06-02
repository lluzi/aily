from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


_UI_TELEMETRY_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "aily_ui_telemetry_context",
    default={},
)


def get_ui_telemetry_context() -> dict[str, Any]:
    return dict(_UI_TELEMETRY_CONTEXT.get({}))


@contextmanager
def ui_telemetry_scope(**values: Any):
    current = get_ui_telemetry_context()
    merged = dict(current)
    merged.update({k: v for k, v in values.items() if v is not None})
    token = _UI_TELEMETRY_CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _UI_TELEMETRY_CONTEXT.reset(token)

