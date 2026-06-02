from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aily.graph.db import GraphDB
    from aily.llm.client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class RecallPrompt:
    """A recall question for active retrieval practice."""

    id: str
    note_id: str
    question_text: str
    answer_text: str
    question_type: str  # "open" | "cloze" | "choice"
    created_at: str
    review_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClozeDeletion:
    """A cloze deletion (fill-in-the-blank) recall item."""

    id: str
    note_id: str
    full_text: str
    cloze_text: str  # Text with [[...]] for blanks
    answer: str
    hint: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RecallQuestionGenerator:
    """Generate active recall questions from notes for retrieval practice."""

    def __init__(self, llm_client: LLMClient, graph_db: GraphDB) -> None:
        self.llm = llm_client
        self.graph_db = graph_db

    async def generate_questions(
        self,
        note_content: str,
        note_id: str,
        question_types: list[str] | None = None,
    ) -> list[RecallPrompt]:
        """Generate recall questions from note content.

        Args:
            note_content: The markdown content of the note
            note_id: Unique identifier for the source note
            question_types: Types of questions to generate ("open", "cloze", "choice")
                           Defaults to ["open", "cloze"]

        Returns:
            List of RecallPrompt objects
        """
        if question_types is None:
            question_types = ["open", "cloze"]

        system_prompt = (
            "You are an expert at creating active recall questions for learning. "
            "Given a note, generate questions that require understanding, not just memorization. "
            "Focus on key concepts, relationships, and applications.\n\n"
            "Output JSON with this structure:\n"
            '{"questions": [{\n'
            '  "question_text": "string",\n'
            '  "answer_text": "string",\n'
            '  "question_type": "open|cloze|choice",\n'
            '  "distractors": ["string"]  // Only for choice type\n'
            "}]}\n\n"
            "Guidelines:\n"
            '- Open-ended: Start with "Explain", "Describe", "Compare", "Why", "How"\n'
            "- Cloze: Create fill-in-the-blank with the most important 2-3 terms\n"
            "- Choice: Generate 3 plausible distractors + 1 correct answer\n"
            "- Questions should require reconstructing knowledge, not recognition"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Note content:\n{note_content[:4000]}"},
        ]

        try:
            result = await self.llm.chat_json(messages, temperature=0.7)
        except Exception as exc:
            logger.warning("Failed to generate questions for note %s: %s", note_id, exc)
            return []

        questions_data = result.get("questions", [])
        logger.info("Generated %d questions for note %s", len(questions_data), note_id)
        prompts: list[RecallPrompt] = []
        now = datetime.now(timezone.utc).isoformat()

        for q_data in questions_data:
            q_type = q_data.get("question_type", "open")
            if q_type not in question_types:
                continue

            prompt_id = str(uuid.uuid4())
            prompt = RecallPrompt(
                id=prompt_id,
                note_id=note_id,
                question_text=q_data.get("question_text", ""),
                answer_text=q_data.get("answer_text", ""),
                question_type=q_type,
                created_at=now,
                review_count=0,
                metadata={"distractors": q_data.get("distractors", [])} if q_type == "choice" else {},
            )
            prompts.append(prompt)

            # Store in graph database
            await self._store_prompt(prompt)

        return prompts

    async def generate_cloze(
        self,
        note_content: str,
        note_id: str,
    ) -> list[ClozeDeletion]:
        """Generate cloze deletions (fill-in-the-blank) from note content.

        Args:
            note_content: The markdown content of the note
            note_id: Unique identifier for the source note

        Returns:
            List of ClozeDeletion objects
        """
        system_prompt = (
            "You are an expert at creating cloze deletions for spaced repetition. "
            "Given a note, identify the most important 2-3 terms or concepts to blank out. "
            "Create deletions that test understanding of key ideas.\n\n"
            "Output JSON with this structure:\n"
            '{"clozes": [{\n'
            '  "full_text": "The complete sentence",\n'
            '  "cloze_text": "The [[answer]] with blanks",\n'
            '  "answer": "the answer",\n'
            '  "hint": "optional hint"\n'
            "}]}\n\n"
            "Guidelines:\n"
            "- Select the most conceptually important terms (not trivial words)\n"
            "- Ensure the context makes the answer inferable\n"
            "- Use [[...]] syntax to mark blanks\n"
            "- Keep full_text as the original sentence for reference"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Note content:\n{note_content[:4000]}"},
        ]

        try:
            result = await self.llm.chat_json(messages, temperature=0.7)
        except Exception as exc:
            logger.warning("Failed to generate cloze deletions for note %s: %s", note_id, exc)
            return []

        clozes_data = result.get("clozes", [])
        logger.info("Generated %d cloze deletions for note %s", len(clozes_data), note_id)
        clozes: list[ClozeDeletion] = []
        now = datetime.now(timezone.utc).isoformat()

        for c_data in clozes_data:
            cloze_id = str(uuid.uuid4())
            cloze = ClozeDeletion(
                id=cloze_id,
                note_id=note_id,
                full_text=c_data.get("full_text", ""),
                cloze_text=c_data.get("cloze_text", ""),
                answer=c_data.get("answer", ""),
                hint=c_data.get("hint", ""),
                created_at=now,
            )
            clozes.append(cloze)

            # Store as a recall prompt with cloze type
            prompt = RecallPrompt(
                id=cloze_id,
                note_id=note_id,
                question_text=cloze.cloze_text,
                answer_text=cloze.answer,
                question_type="cloze",
                created_at=now,
                review_count=0,
                metadata={"full_text": cloze.full_text, "hint": cloze.hint},
            )
            await self._store_prompt(prompt)

        return clozes

    async def get_due_questions(self, limit: int = 10) -> list[RecallPrompt]:
        """Get questions that are due for review.

        Args:
            limit: Maximum number of questions to return

        Returns:
            List of RecallPrompt objects sorted by priority
        """
        # Fetch recall_prompt nodes from the graph database
        nodes = await self.graph_db.get_nodes_by_type("recall_prompt")

        prompts: list[RecallPrompt] = []
        for node in nodes:
            # Parse metadata from label (stored as JSON string)
            try:
                metadata = json.loads(node.get("label", "{}"))
            except json.JSONDecodeError:
                continue

            prompt = RecallPrompt(
                id=node.get("id", ""),
                note_id=metadata.get("note_id", ""),
                question_text=metadata.get("question_text", ""),
                answer_text=metadata.get("answer_text", ""),
                question_type=metadata.get("question_type", "open"),
                created_at=node.get("created_at", ""),
                review_count=metadata.get("review_count", 0),
                metadata=metadata.get("extra", {}),
            )
            prompts.append(prompt)

        # Sort by review_count (fewer reviews = higher priority)
        prompts.sort(key=lambda p: (p.review_count, p.created_at))

        return prompts[:limit]

    async def _store_prompt(self, prompt: RecallPrompt) -> None:
        """Store a recall prompt in the graph database.

        Args:
            prompt: The RecallPrompt to store
        """
        # Serialize prompt data as JSON in the label field
        metadata = {
            "note_id": prompt.note_id,
            "question_text": prompt.question_text,
            "answer_text": prompt.answer_text,
            "question_type": prompt.question_type,
            "review_count": prompt.review_count,
            "extra": prompt.metadata,
        }

        # Insert the recall prompt node
        await self.graph_db.insert_node(
            node_id=prompt.id,
            node_type="recall_prompt",
            label=json.dumps(metadata, ensure_ascii=False),
            source=f"recall_generator:{prompt.note_id}",
        )

        # Create edge linking to source note
        edge_id = str(uuid.uuid4())
        await self.graph_db.insert_edge(
            edge_id=edge_id,
            source_node_id=prompt.id,
            target_node_id=prompt.note_id,
            relation_type="tests_knowledge_of",
            weight=1.0,
            source=f"recall_generator:{prompt.note_id}",
        )

    def add_recall_section(self, digest_markdown: str) -> str:
        """Append a recall questions section to a digest markdown.

        This is a synchronous helper for adding a recall section template.
        The actual questions should be fetched and formatted by the caller.

        Args:
            digest_markdown: The existing digest markdown

        Returns:
            Markdown with recall section appended
        """
        lines = [
            "",
            "## Active Recall",
            "",
            "Test your understanding with these questions:",
            "",
            "*Questions will be populated based on your recent notes.*",
            "",
            "---",
            "",
            "**How to use:** Try to answer each question from memory before checking the answer. "
            "This retrieval practice strengthens memory 50-100% more than passive review.",
            "",
        ]

        return digest_markdown + "\n".join(lines)

    async def format_recall_section(self, questions: list[RecallPrompt]) -> str:
        """Format a list of recall questions as markdown.

        Args:
            questions: List of RecallPrompt objects to format

        Returns:
            Markdown formatted recall section
        """
        if not questions:
            return ""

        lines = [
            "",
            "## Active Recall",
            "",
            "Test your understanding with these questions:",
            "",
        ]

        for i, q in enumerate(questions, 1):
            lines.append(f"### Q{i}: {q.question_type.upper()}")
            lines.append("")
            lines.append(f"**Question:** {q.question_text}")
            lines.append("")

            if q.question_type == "cloze":
                hint = q.metadata.get("hint", "")
                if hint:
                    lines.append(f"*Hint: {hint}*")
                    lines.append("")

            # Answer in a collapsible details block
            lines.append("<details>")
            lines.append("<summary>Click to reveal answer</summary>")
            lines.append("")
            lines.append(f"**Answer:** {q.answer_text}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

            if q.question_type == "choice" and q.metadata.get("distractors"):
                lines.append("*Distractors:* " + ", ".join(q.metadata["distractors"]))
                lines.append("")

        lines.extend([
            "---",
            "",
            "**How to use:** Try to answer each question from memory before checking the answer. "
            "This retrieval practice strengthens memory 50-100% more than passive review.",
            "",
        ])

        return "\n".join(lines)
