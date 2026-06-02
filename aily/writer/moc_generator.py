"""Curated Map-of-Content generation for Aily Obsidian vaults."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def generate_curated_mocs(vault_path: Path, *, max_topics: int = 6, max_links_per_topic: int = 8) -> list[Path]:
    """Generate review query pages that do not create Obsidian graph hubs."""
    vault = vault_path.expanduser().resolve()
    moc_dir = vault / "99-MOC"
    moc_dir.mkdir(parents=True, exist_ok=True)
    notes = _collect_notes(vault)
    topics = _topic_index(notes, max_topics=max_topics)
    written = [
        _write_moc(moc_dir / "DIKIWI Knowledge Flow.md", _knowledge_flow(vault, notes)),
        _write_moc(moc_dir / "Source Evidence Map.md", _source_map(vault, notes)),
        _write_moc(moc_dir / "Topic Cluster Map.md", _topic_map(vault, topics, max_links_per_topic=max_links_per_topic)),
    ]
    return written


def _collect_notes(vault: Path) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if rel.startswith(("99-MOC/", "99-System/", "00-Chaos/canonical-markdown/", "00-Chaos/_assets/")):
            continue
        if rel == "00-Chaos/00 Zettelkasten Index.md":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        notes.append(
            {
                "path": path,
                "rel": rel,
                "stem": rel[:-3] if rel.endswith(".md") else rel,
                "top_dir": rel.split("/", 1)[0],
                "title": _first_h1(body) or path.stem.replace("_", " "),
                "frontmatter": frontmatter,
                "body": body,
                "topics": _frontmatter_list(frontmatter, "semantic_topics") or _frontmatter_list(frontmatter, "tags"),
                "source_ids": _frontmatter_list(frontmatter, "source_ids") or ([sid] if (sid := _frontmatter_scalar(frontmatter, "source_id")) else []),
            }
        )
    return notes


def _knowledge_flow(vault: Path, notes: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for stage in ("00-Chaos", "01-Data", "02-Information", "03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact", "08-Evaluations", "09-Business-Plans"):
        stage_notes = [note for note in notes if note["top_dir"] == stage]
        if not stage_notes:
            continue
        sections.append(
            f"## {stage}\n\n"
            f"{_stage_explanation(stage, len(stage_notes))}\n\n"
            f"```dataview\n"
            f'TABLE file.mtime AS "Updated", source_id AS "Source", semantic_topics AS "Topics"\n'
            f'FROM "{stage}"\n'
            f"SORT file.mtime DESC\n"
            f"LIMIT 8\n"
            f"```\n"
        )
    return _frontmatter("knowledge_flow") + "\n\n# DIKIWI Knowledge Flow\n\nThis page is a human review query surface, not a knowledge-graph connector. It uses Dataview queries and plain descriptions so it does not create artificial central nodes in Obsidian Graph View. Actual graph edges must come from content notes that explain why two ideas are connected.\n\n" + "\n\n".join(sections) + "\n"


def _source_map(vault: Path, notes: list[dict[str, Any]]) -> str:
    chaos = [note for note in notes if note["top_dir"] == "00-Chaos"]
    reports = [note for note in notes if note["top_dir"] in {"08-Evaluations", "09-Business-Plans", "10-Dossiers"}]
    source_count = len({sid for note in notes for sid in note["source_ids"]})
    return (
        _frontmatter("source_evidence")
        + "\n\n# Source Evidence Map\n\n"
        + f"This page helps reviewers inspect source coverage without creating graph edges. It covers {source_count} source identifier(s) found in note metadata. Use these queries when checking whether a business or Impact claim can be walked back to the original PDFs and their canonical Markdown representations.\n\n"
        + "## Source Equivalents\n\n"
        + "```dataview\n"
        + 'TABLE source_id AS "Source", page_count AS "Pages", screenshot_count AS "Screenshots", source_sha256 AS "SHA-256"\n'
        + 'FROM "00-Chaos"\n'
        + "WHERE source_id\n"
        + "SORT file.name ASC\n"
        + "```\n"
        + "\n\n## Final Artifacts\n\n"
        + "```dataview\n"
        + 'TABLE source_ids AS "Sources", workflow_run_id AS "Workflow", file.mtime AS "Updated"\n'
        + 'FROM "08-Evaluations" OR "09-Business-Plans" OR "10-Dossiers"\n'
        + "SORT file.mtime DESC\n"
        + "LIMIT 12\n"
        + "```\n"
        + "\n\n## Review Use\n\nThese query results are only a navigation aid. Proof still requires reading the source-equivalent note, Data extraction, Information classification, Knowledge connector, and final synthesis together.\n"
    )


def _topic_map(vault: Path, topics: dict[str, list[dict[str, Any]]], *, max_links_per_topic: int) -> str:
    parts = [
        _frontmatter("topic_clusters"),
        "",
        "# Topic Cluster Map",
        "",
        "This page groups the strongest semantic topics found in generated notes without adding artificial graph edges. It avoids a one-file-per-tag explosion and gives reviewers a compact query surface for content neighborhoods. Actual Obsidian graph connections must be created by substantive links inside the notes themselves.",
    ]
    for topic, topic_notes in topics.items():
        safe_topic = topic.replace('"', '\\"')
        parts.append(
            f"## {topic}\n\n"
            f"This cluster is useful because {len(topic_notes)} notes explicitly use `{topic}` as a semantic topic or durable tag. "
            "Review the query results together before accepting any higher-level claim that depends on this neighborhood.\n\n"
            "```dataview\n"
            'TABLE top_dir AS "Layer", source_id AS "Source", file.mtime AS "Updated"\n'
            "FROM \"00-Chaos\" OR \"01-Data\" OR \"02-Information\" OR \"03-Knowledge\" OR \"04-Insight\" OR \"05-Wisdom\" OR \"06-Impact\" OR \"08-Evaluations\" OR \"09-Business-Plans\"\n"
            f'WHERE contains(semantic_topics, "{safe_topic}") OR contains(tags, "{safe_topic}")\n'
            "SORT file.mtime DESC\n"
            f"LIMIT {max_links_per_topic}\n"
            "```\n"
        )
    return "\n\n".join(parts) + "\n"


def _topic_index(notes: list[dict[str, Any]], *, max_topics: int) -> dict[str, list[dict[str, Any]]]:
    counter: Counter[str] = Counter()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        for raw in note["topics"]:
            topic = _clean_topic(raw)
            if not topic:
                continue
            counter[topic] += 1
            grouped[topic].append(note)
    selected = [topic for topic, count in counter.most_common(max_topics) if count >= 2]
    return {topic: grouped[topic] for topic in selected}


def _write_moc(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _frontmatter(kind: str) -> str:
    return (
        "---\n"
        "note_role: \"moc\"\n"
        f"moc_kind: \"{kind}\"\n"
        "grounded_in: \"generated_vault_notes\"\n"
        "origin_creator: \"application\"\n"
        "origin_modified_by_lead_agent: false\n"
        f"created_at: \"{datetime.now(timezone.utc).isoformat()}\"\n"
        "---"
    )


def _stage_explanation(stage: str, count: int) -> str:
    labels = {
        "00-Chaos": "source-equivalent intake notes",
        "01-Data": "atomic evidence cards",
        "02-Information": "classified reusable concepts",
        "03-Knowledge": "typed relationship connectors",
        "04-Insight": "emergent graph interpretations",
        "05-Wisdom": "durable principles",
        "06-Impact": "bounded action hypotheses",
        "08-Evaluations": "specialist review records",
        "09-Business-Plans": "final business synthesis",
    }
    return f"This stage contains {count} {labels.get(stage, 'notes')}. The selected links are representative anchors for review, not an exhaustive index."


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end < 0:
        return "", text
    return text[4:end].strip(), text[end + 4 :].lstrip()


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _frontmatter_list(frontmatter: str, key: str) -> list[str]:
    values: list[str] = []
    in_list = False
    prefix = f"{key}:"
    for line in frontmatter.splitlines():
        if line.strip() == prefix:
            in_list = True
            continue
        if in_list:
            if line.startswith("  - "):
                values.append(line.split("- ", 1)[1].strip().strip('"').strip("'"))
                continue
            if line and not line.startswith(" "):
                in_list = False
    return values


def _frontmatter_scalar(frontmatter: str, key: str) -> str:
    prefix = f"{key}:"
    for line in frontmatter.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def _clean_topic(value: str) -> str:
    topic = " ".join(str(value).replace("_", " ").split()).strip(" -#")
    if len(topic) < 4 or len(topic.split()) > 7:
        return ""
    if re.fullmatch(r"[a-f0-9-]{8,}", topic, flags=re.I):
        return ""
    return topic
