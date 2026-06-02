from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UIEventHub:
    """In-memory event hub for Aily Studio.

    This is intentionally simple for the current UI event projection:
    - stores a rolling event buffer for replay
    - broadcasts events to websocket subscribers
    - indexes events by pipeline_id and upload_id for trace views
    """

    def __init__(self, max_events: int = 5000, trace_limit: int = 200) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._trace_limit = max(1, trace_limit)
        self._pipelines: dict[str, deque[dict[str, Any]]] = {}
        self._uploads: dict[str, deque[dict[str, Any]]] = {}
        self._runs: dict[str, deque[dict[str, Any]]] = {}
        self._event_log_path: Path | None = None
        self._next_seq = 1
        self._lock = asyncio.Lock()

    def configure_persistence(self, event_log_path: Path | None) -> None:
        self._event_log_path = event_log_path
        if event_log_path is not None:
            event_log_path.parent.mkdir(parents=True, exist_ok=True)

    async def load_persisted(self, limit: int | None = None) -> int:
        path = self._event_log_path
        if path is None or not path.exists():
            return 0
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        raw_events = [line for line in content.splitlines() if line.strip()]
        if limit is not None and limit > 0:
            raw_events = raw_events[-limit:]

        loaded = 0
        async with self._lock:
            for raw_event in raw_events:
                try:
                    event = json.loads(raw_event)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed UI event log line in %s", path)
                    continue
                if "seq" not in event:
                    event["seq"] = self._next_seq
                self._next_seq = max(self._next_seq, int(event.get("seq") or 0) + 1)
                self._index_event(event)
                loaded += 1
        return loaded

    async def query_persisted(
        self,
        *,
        run_id: str | None = None,
        pipeline_id: str | None = None,
        upload_id: str | None = None,
        event_type: str | None = None,
        after_seq: int | None = None,
        before_seq: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Query durable JSONL events by lineage IDs.

        The in-memory indexes are only a warm cache. Replay and restart-safe UI
        queries must read the persisted log so old runs remain inspectable after
        process restarts.
        """
        safe_limit = min(max(1, limit), 5000)
        path = self._event_log_path
        if path is None or not path.exists():
            return self._filter_events(
                list(self._events),
                run_id=run_id,
                pipeline_id=pipeline_id,
                upload_id=upload_id,
                event_type=event_type,
                after_seq=after_seq,
                before_seq=before_seq,
                limit=safe_limit,
            )
        return await asyncio.to_thread(
            self._query_event_log,
            path,
            run_id,
            pipeline_id,
            upload_id,
            event_type,
            after_seq,
            before_seq,
            safe_limit,
        )

    @staticmethod
    def _filter_events(
        events: list[dict[str, Any]],
        *,
        run_id: str | None,
        pipeline_id: str | None,
        upload_id: str | None,
        event_type: str | None,
        after_seq: int | None,
        before_seq: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for event in events:
            seq = int(event.get("seq") or 0)
            if after_seq is not None and seq <= after_seq:
                continue
            if before_seq is not None and seq >= before_seq:
                continue
            if run_id is not None and str(event.get("run_id", "")) != run_id:
                continue
            if pipeline_id is not None and str(event.get("pipeline_id", "")) != pipeline_id:
                continue
            if upload_id is not None and str(event.get("upload_id", "")) != upload_id:
                continue
            if event_type is not None and str(event.get("type", "")) != event_type:
                continue
            filtered.append(event)
            if after_seq is not None and len(filtered) >= limit:
                break
        return filtered[-limit:]

    @classmethod
    def _query_event_log(
        cls,
        path: Path,
        run_id: str | None,
        pipeline_id: str | None,
        upload_id: str | None,
        event_type: str | None,
        after_seq: int | None,
        before_seq: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        fallback_seq = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                fallback_seq += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed UI event log line in %s", path)
                    continue
                if "seq" not in event:
                    event["seq"] = fallback_seq
                seq = int(event.get("seq") or 0)
                if after_seq is not None and seq <= after_seq:
                    continue
                if before_seq is not None and seq >= before_seq:
                    continue
                if run_id is not None and str(event.get("run_id", "")) != run_id:
                    continue
                if pipeline_id is not None and str(event.get("pipeline_id", "")) != pipeline_id:
                    continue
                if upload_id is not None and str(event.get("upload_id", "")) != upload_id:
                    continue
                if event_type is not None and str(event.get("type", "")) != event_type:
                    continue
                events.append(event)
                if after_seq is not None and len(events) >= limit:
                    break
                if len(events) > limit:
                    events = events[-limit:]
        return events

    async def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {
            "id": payload.pop("id", str(uuid4())),
            "type": event_type,
            "timestamp": payload.pop("timestamp", _utc_now_iso()),
            **payload,
        }
        async with self._lock:
            event["seq"] = payload.pop("seq", self._next_seq)
            self._next_seq = max(self._next_seq, int(event["seq"]) + 1)
            self._index_event(event)
            if self._event_log_path is not None:
                await asyncio.to_thread(
                    self._append_event_to_log,
                    self._event_log_path,
                    event,
                )
            dead: list[asyncio.Queue[dict[str, Any]]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(queue)
            for queue in dead:
                self._subscribers.discard(queue)
        return event

    def _index_event(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        pipeline_id = event.get("pipeline_id")
        if pipeline_id:
            self._pipelines.setdefault(
                str(pipeline_id), deque(maxlen=self._trace_limit)
            ).append(event)
        upload_id = event.get("upload_id")
        if upload_id:
            self._uploads.setdefault(
                str(upload_id), deque(maxlen=self._trace_limit)
            ).append(event)
        run_id = event.get("run_id")
        if run_id:
            self._runs.setdefault(
                str(run_id), deque(maxlen=self._trace_limit)
            ).append(event)

    @staticmethod
    def _append_event_to_log(path: Path, event: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def subscribe(self, max_queue_size: int = 500) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return list(self._events)[-limit:]

    def pipeline_trace(self, pipeline_id: str) -> list[dict[str, Any]]:
        return list(self._pipelines.get(str(pipeline_id), []))

    def upload_trace(self, upload_id: str) -> list[dict[str, Any]]:
        return list(self._uploads.get(str(upload_id), []))

    def run_trace(self, run_id: str) -> list[dict[str, Any]]:
        return list(self._runs.get(str(run_id), []))

    def active_pipeline_ids(self) -> list[str]:
        recent: dict[str, dict[str, Any]] = {}
        for event in self._events:
            pipeline_ids: list[str] = []
            if event.get("pipeline_id"):
                pipeline_ids.append(str(event["pipeline_id"]))
            if isinstance(event.get("pipeline_ids"), list):
                pipeline_ids.extend(str(value) for value in event["pipeline_ids"] if value)
            for pipeline_id in pipeline_ids:
                recent[pipeline_id] = event
        active = []
        terminal = {"pipeline_completed", "pipeline_failed", "proposal_review_completed", "business_flow_skipped"}
        for pipeline_id, event in recent.items():
            if event.get("type") not in terminal:
                active.append(pipeline_id)
        return active


ui_event_hub = UIEventHub()


async def emit_ui_event(event_type: str, **payload: Any) -> dict[str, Any]:
    return await ui_event_hub.emit(event_type, **payload)
