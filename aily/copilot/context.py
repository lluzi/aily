"""Prompt context envelope helpers for Aily-Copilot."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


LAYER_ORDER = ("L1_SYSTEM", "L2_CONTEXT_LIBRARY", "L3_TURN_CONTEXT", "L4_CHAT_HISTORY", "L5_USER")


class CopilotContextEnvelopeBuilder:
    """Build deterministic, auditable context envelopes for vault chat."""

    def build(
        self,
        *,
        user_message: str,
        search_results: list[dict[str, Any]] | None = None,
        previous_context: list[dict[str, Any]] | None = None,
        chat_history: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        layers = [
            _layer(
                "L1_SYSTEM",
                system_prompt
                or "You are Aily-Copilot. Answer only from supplied vault evidence unless explicitly asked to brainstorm.",
                stable=True,
                segments=[],
            ),
            _layer(
                "L2_CONTEXT_LIBRARY",
                _render_context_library(previous_context or []),
                stable=True,
                segments=_segments(previous_context or [], stable=True),
            ),
            _layer(
                "L3_TURN_CONTEXT",
                _render_search_context(search_results or []),
                stable=False,
                segments=_segments(search_results or [], stable=False),
            ),
            _layer(
                "L4_CHAT_HISTORY",
                _render_chat_history(chat_history or []),
                stable=False,
                segments=[],
            ),
            _layer("L5_USER", str(user_message or "").strip(), stable=False, segments=[]),
        ]
        serialized = "\n\n".join(f"## {layer['id']}\n{layer['text']}" for layer in layers if layer["text"])
        return {
            "envelope_id": f"ctx_{uuid4().hex}",
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "layers": layers,
            "layer_hashes": {layer["id"]: layer["hash"] for layer in layers},
            "combined_hash": _sha256(serialized),
            "citation_catalog": _citation_catalog(search_results or []),
            "serialized_text": serialized,
        }


def _layer(layer_id: str, text: str, *, stable: bool, segments: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = _normalize(text)
    return {
        "id": layer_id,
        "label": layer_id.replace("_", " ").title(),
        "stable": stable,
        "text": normalized,
        "segments": segments,
        "hash": _sha256(normalized),
    }


def _segments(items: list[dict[str, Any]], *, stable: bool) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for item in items:
        segment_id = str(item.get("relative_path") or item.get("citation_id") or "")
        if not segment_id:
            continue
        segments.append(
            {
                "id": segment_id,
                "stable": stable,
                "citation_id": item.get("citation_id", ""),
                "title": item.get("title", ""),
                "relative_path": item.get("relative_path", ""),
                "hash": item.get("sha256", ""),
            }
        )
    return segments


def _render_context_library(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["Previously available vault context:"]
    for item in items[:30]:
        lines.append(
            f"- [{item.get('citation_id', '')}] {item.get('title', '')} "
            f"({item.get('relative_path', '')})"
        )
    return "\n".join(lines)


def _render_search_context(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    blocks: list[str] = []
    for item in items[:20]:
        blocks.append(
            "<vault_note>\n"
            f"<citation_id>{item.get('citation_id', '')}</citation_id>\n"
            f"<title>{item.get('title', '')}</title>\n"
            f"<path>{item.get('relative_path', '')}</path>\n"
            f"<score>{item.get('score', '')}</score>\n"
            f"<excerpt>{item.get('excerpt', '')}</excerpt>\n"
            "</vault_note>"
        )
    return "\n\n".join(blocks)


def _render_chat_history(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages[-10:]:
        role = str(message.get("role") or "message").strip()
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[:1200]}")
    return "\n".join(lines)


def _citation_catalog(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "citation_id": item.get("citation_id", ""),
            "title": item.get("title", ""),
            "relative_path": item.get("relative_path", ""),
            "excerpt": item.get("excerpt", ""),
            "sha256": item.get("sha256", ""),
        }
        for item in items
    ]


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
