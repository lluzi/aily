from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aily.graph.db import GraphDB
    from aily.llm.client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class AtomicNote:
    """A single atomic idea extracted from content.

    Each note represents one discrete concept, fact, or insight
    suitable for Zettelkasten-style knowledge management.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    source_url: str = ""
    raw_log_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = field(default_factory=list)


@dataclass
class ConnectionSuggestion:
    """A suggested connection between an atomic note and an existing node.

    Represents a potential relationship that could enrich the knowledge graph.
    """

    target_node_id: str = ""
    relationship_type: str = ""
    confidence_score: float = 0.0
    explanation: str = ""


class AtomicNoteGenerator:
    """Breaks content into atomic notes and suggests connections.

    Based on neuroscience research about elaborative encoding, this generator
    splits captures into single-idea chunks to maximize knowledge retention
    and enable meaningful connections in a Zettelkasten system.
    """

    def __init__(self, llm_client: "LLMClient", graph_db: "GraphDB") -> None:
        self.llm_client = llm_client
        self.graph_db = graph_db

    async def atomize(self, content: str, source_url: str, raw_log_id: str) -> list[AtomicNote]:
        """Break content into atomic notes (single-idea chunks).

        Uses LLM to split content into 1-3 sentence chunks, each representing
        a single discrete idea suitable for atomic note-taking.

        Args:
            content: The raw content to atomize
            source_url: Original source URL for attribution
            raw_log_id: Reference to the raw log entry

        Returns:
            List of AtomicNote objects, each containing one atomic idea
        """
        if not content or not content.strip():
            logger.debug("Atomize called with empty content")
            return []

        prompt = self._build_atomization_prompt(content)

        try:
            result = await self.llm_client.chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert at breaking down content into atomic notes. "
                            "Each atomic note should contain exactly ONE discrete idea, "
                            "fact, or insight. Keep each note to 1-3 sentences maximum. "
                            "Return your response as a JSON object with an 'notes' array."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
        except Exception as exc:
            # Fallback: treat entire content as single atomic note
            logger.warning("LLM atomization failed, using fallback: %s", exc)
            note = AtomicNote(
                content=content.strip(),
                source_url=source_url,
                raw_log_id=raw_log_id,
            )
            await self._store_atomic_note(note)
            return [note]

        notes_data = result.get("notes", []) if isinstance(result, dict) else []

        if not notes_data:
            # Fallback if LLM returns empty or malformed
            note = AtomicNote(
                content=content.strip(),
                source_url=source_url,
                raw_log_id=raw_log_id,
            )
            await self._store_atomic_note(note)
            return [note]

        atomic_notes: list[AtomicNote] = []
        logger.info("Atomized content into %d notes from %s", len(notes_data), source_url)
        for note_data in notes_data:
            if isinstance(note_data, str):
                note_content = note_data
                tags: list[str] = []
            elif isinstance(note_data, dict):
                note_content = note_data.get("content", "")
                tags = note_data.get("tags", [])
            else:
                continue

            if not note_content or not note_content.strip():
                continue

            note = AtomicNote(
                content=note_content.strip(),
                source_url=source_url,
                raw_log_id=raw_log_id,
                tags=tags if isinstance(tags, list) else [],
            )
            await self._store_atomic_note(note)
            atomic_notes.append(note)

        return atomic_notes

    def _build_atomization_prompt(self, content: str) -> str:
        """Build the prompt for atomization."""
        return f"""Break the following content into atomic notes.

Rules:
- Each note should contain exactly ONE discrete idea, fact, or insight
- Keep each note to 1-3 sentences maximum
- Extract tags for each note (optional, 0-3 tags per note)
- Preserve the original meaning and key details

Content to atomize:
---
{content}
---

Return your response as JSON in this format:
{{
    "notes": [
        {{
            "content": "The atomic note text (1-3 sentences)",
            "tags": ["tag1", "tag2"]
        }}
    ]
}}"""

    async def _store_atomic_note(self, note: AtomicNote) -> None:
        """Store the atomic note as a node in the graph database."""
        await self.graph_db.insert_node(
            node_id=note.id,
            node_type="atomic_note",
            label=note.content[:200],  # Truncate for label
            source=note.source_url,
        )
        # Store the occurrence link
        occurrence_id = str(uuid.uuid4())
        await self.graph_db.insert_occurrence(
            occurrence_id=occurrence_id,
            node_id=note.id,
            raw_log_id=note.raw_log_id,
        )

    async def suggest_connections(self, note: AtomicNote) -> list[ConnectionSuggestion]:
        """Find similar existing notes via GraphDB for potential connections.

        Uses keyword matching and simple semantic similarity to find
        potentially related notes in the knowledge graph.

        Args:
            note: The atomic note to find connections for

        Returns:
            List of ConnectionSuggestion objects with top 5 potential connections
        """
        # Get all existing atomic notes
        existing_nodes = await self.graph_db.get_nodes_by_type("atomic_note")

        if not existing_nodes:
            return []

        # Calculate similarity scores
        suggestions: list[ConnectionSuggestion] = []
        note_words = set(note.content.lower().split())

        for node in existing_nodes:
            # Skip self-matches
            if node["id"] == note.id:
                continue

            node_label = node.get("label", "")
            if not node_label:
                continue

            # Simple keyword overlap similarity
            node_words = set(node_label.lower().split())
            if not note_words or not node_words:
                continue

            intersection = note_words & node_words
            union = note_words | node_words

            if not union:
                continue

            similarity = len(intersection) / len(union)

            # Boost score for significant keyword overlap
            if len(intersection) >= 3:
                similarity += 0.1

            if similarity > 0.1:  # Minimum threshold
                explanation = self._generate_explanation(note.content, node_label, intersection)
                suggestions.append(
                    ConnectionSuggestion(
                        target_node_id=node["id"],
                        relationship_type="suggested_link",
                        confidence_score=min(similarity, 1.0),
                        explanation=explanation,
                    )
                )

        # Sort by confidence and take top 5
        suggestions.sort(key=lambda x: x.confidence_score, reverse=True)
        top_suggestions = suggestions[:5]
        logger.info("Found %d connection suggestions for note %s", len(top_suggestions), note.id)

        # Store suggested connections as edges
        for suggestion in top_suggestions:
            edge_id = str(uuid.uuid4())
            await self.graph_db.insert_edge(
                edge_id=edge_id,
                source_node_id=note.id,
                target_node_id=suggestion.target_node_id,
                relation_type="suggested_link",
                weight=suggestion.confidence_score,
                source="atomicizer_suggestion",
            )

        return top_suggestions

    def _generate_explanation(
        self, source_content: str, target_label: str, shared_words: set[str]
    ) -> str:
        """Generate a human-readable explanation for the connection."""
        if shared_words:
            shared_terms = ", ".join(sorted(shared_words)[:5])
            return f"Shared concepts: {shared_terms}"
        return "Semantic similarity detected between notes"
