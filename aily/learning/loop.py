from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from watchfiles import awatch

from aily.graph.db import GraphDB
from aily.llm.client import LLMClient
from aily.queue.db import QueueDB

logger = logging.getLogger(__name__)


class LearningLoop:
    def __init__(
        self,
        vault_path: Path,
        queue_db: QueueDB,
        graph_db: GraphDB,
        llm: LLMClient,
        draft_folder: str = "Aily Drafts",
        debounce_seconds: float = 5.0,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.queue_db = queue_db
        self.graph_db = graph_db
        self.llm = llm
        self.draft_folder = draft_folder
        self.debounce_seconds = debounce_seconds
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._pending: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info("LearningLoop started")

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("LearningLoop stopped")

    async def _loop(self) -> None:
        try:
            async for changes in awatch(self.vault_path, stop_event=self._stop_event):
                for change, path_str in changes:
                    path = Path(path_str)
                    if path.suffix != ".md":
                        continue
                    if self.draft_folder in path.parts:
                        continue
                    rel_path = path.relative_to(self.vault_path)
                    if str(rel_path) not in self._pending:
                        self._pending[str(rel_path)] = asyncio.create_task(
                            self._handle_change_with_debounce(rel_path)
                        )
        except Exception:
            logger.exception("LearningLoop watcher failed")

    async def _handle_change_with_debounce(self, rel_path: Path) -> None:
        await asyncio.sleep(self.debounce_seconds)
        self._pending.pop(str(rel_path), None)
        if self._stop_event.is_set():
            return
        await self._process_file(rel_path)

    async def _process_file(self, rel_path: Path) -> None:
        full_path = self.vault_path / rel_path
        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to read file: %s", full_path)
            return

        if not self._is_aily_generated(content):
            return

        snapshot = await self.queue_db.get_note_snapshot(str(rel_path))
        if snapshot is None:
            return

        original = snapshot["original_markdown"]
        logger.info("Processing changes for note: %s", rel_path)

        system_prompt = (
            "You are Aily's learning loop. A user has edited a note you originally wrote. "
            "Compare the original markdown with the current markdown. "
            "Output JSON with these keys: "
            'new_entities (list of {label, type}), '
            'corrected_entities (list of {old_label, new_label, type}), '
            'new_edges (list of {source_label, target_label, relation_type}), '
            'inferred_preferences (list of strings describing what the user cares about). '
            "Only include meaningful changes."
        )
        user_content = f"Original:\n{original[:4000]}\n\nCurrent:\n{content[:4000]}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            result = await self.llm.chat_json(messages, temperature=0.3)
        except Exception:
            logger.exception("LLM diff failed for %s", rel_path)
            return

        await self._persist_insights(result, str(rel_path))

    @staticmethod
    def _is_aily_generated(content: str) -> bool:
        first_lines = "\n".join(content.splitlines()[:20])
        return bool(re.search(r"^aily_generated:\s*true", first_lines, re.MULTILINE))

    async def _persist_insights(self, result: dict, note_path: str) -> None:
        new_entities = result.get("new_entities", [])
        corrected_entities = result.get("corrected_entities", [])
        new_edges = result.get("new_edges", [])
        inferred_preferences = result.get("inferred_preferences", [])

        for entity in new_entities:
            node_id = str(uuid.uuid4())
            await self.graph_db.insert_node(
                node_id,
                entity.get("type", "concept"),
                entity.get("label", "Unknown"),
                f"learning_loop:{note_path}",
            )

        for edge in new_edges:
            edge_id = str(uuid.uuid4())
            source_label = edge.get("source_label", "")
            target_label = edge.get("target_label", "")
            relation_type = edge.get("relation_type", "related")
            if source_label and target_label:
                await self.graph_db.insert_edge(
                    edge_id,
                    source_label,
                    target_label,
                    relation_type,
                    1.0,
                    f"learning_loop:{note_path}",
                )

        for pref in inferred_preferences:
            pref_id = str(uuid.uuid4())
            await self.graph_db.insert_preference(pref_id, pref, note_path)

        logger.info(
            "Persisted learning insights for %s: %s new entities, %s preferences",
            note_path,
            len(new_entities),
            len(inferred_preferences),
        )
