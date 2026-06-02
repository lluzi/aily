from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class ConversationLogger:
    """Best-effort JSONL logger for LLM conversations.

    Logging is disabled unless `AILY_LLM_CONVERSATION_LOG` is set. This keeps
    normal runs cheap and avoids accidentally persisting prompt content when the
    operator did not ask for traces.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        configured_path = log_path or _configured_log_path()
        self.log_path = configured_path
        self.enabled = configured_path is not None
        self._lock = Lock()

    def log(
        self,
        *,
        stage: str,
        stage_key: str,
        messages: list[dict[str, str]],
        response: Any,
        temperature: float,
    ) -> None:
        if not self.enabled or self.log_path is None:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "stage_key": stage_key,
            "temperature": temperature,
            "messages": messages,
            "response": response,
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._lock:
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:
            logger.debug("Failed to write LLM conversation log", exc_info=True)


def _configured_log_path() -> Path | None:
    value = os.getenv("AILY_LLM_CONVERSATION_LOG", "").strip()
    if not value:
        return None
    return Path(value).expanduser()


_LOGGER = ConversationLogger()


def get_conversation_logger() -> ConversationLogger:
    return _LOGGER
