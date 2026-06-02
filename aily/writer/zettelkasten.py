"""Zettelkasten Writer - Organic knowledge base from DIKIWI pipeline.

Converts DIKIWI's stage-based output into Luhmann-style Zettelkasten:
- Luhmann IDs (1, 1a, 2, 2a1) for emergent structure
- Flat folder structure
- Content-derived tags
- Bidirectional links between related notes
- Organic MOCs that emerge from content
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Zettel:
    """A single atomic note in the Zettelkasten."""

    id: str  # Luhmann ID: "1", "1a", "2a1"
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # IDs this note links to
    backlinks: list[str] = field(default_factory=list)  # IDs that link here
    source: str = ""  # Original DIKIWI source
    stage: str = ""  # Which DIKIWI stage produced this
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_markdown(self) -> str:
        """Render as Obsidian markdown."""
        tags_str = " ".join(f"#{t}" for t in self.tags) if self.tags else ""

        lines = [
            "---",
            f"id: {self.id}",
            f"source: {self.source}",
            f"stage: {self.stage}",
            f"created: {self.created_at}",
            "---",
            "",
            f"# {self.title}",
            "",
            self.content,
            "",
        ]

        if self.links:
            lines.extend(["## Links", ""])
            for link_id in self.links:
                lines.append(f"- [[{link_id}]]")
            lines.append("")

        if self.backlinks:
            lines.extend(["## Backlinks", ""])
            for backlink_id in self.backlinks:
                lines.append(f"- [[{backlink_id}]]")
            lines.append("")

        if tags_str:
            lines.extend([f"Tags: {tags_str}", ""])

        return "\n".join(lines)


class LuhmannIDGenerator:
    """Generate Luhmann-style IDs (1, 1a, 2, 2a1, etc.)."""

    def __init__(self):
        self.counter = 0
        self.sub_counters: dict[str, int] = {}
        self.used_ids: set[str] = set()

    def next_main(self) -> str:
        """Get next main ID (1, 2, 3...)."""
        self.counter += 1
        id_str = str(self.counter)
        self.used_ids.add(id_str)
        return id_str

    def next_sub(self, parent_id: str) -> str:
        """Get next sub-ID (1a, 1b, 1a1...)."""
        key = parent_id
        if key not in self.sub_counters:
            self.sub_counters[key] = 0
        self.sub_counters[key] += 1

        # Convert number to letter (1->a, 2->b) or number (1->1)
        sub_num = self.sub_counters[key]
        if len(parent_id) == 1:  # Main note -> use letter
            sub_char = chr(ord("a") + sub_num - 1)
            id_str = f"{parent_id}{sub_char}"
        else:  # Already has sub -> use number
            id_str = f"{parent_id}{sub_num}"

        self.used_ids.add(id_str)
        return id_str

    def insert_between(self, id1: str, id2: str) -> str:
        """Generate ID between two existing IDs (for threading)."""
        # Simplified: just add a suffix
        return f"{id1}z"


class ZettelkastenWriter:
    """Convert DIKIWI pipeline output to Zettelkasten format."""

    def __init__(self, vault_path: str | Path, folder_name: str = "Zettelkasten"):
        self.vault_path = Path(vault_path)
        self.zk_root = self.vault_path / folder_name
        self.zk_root.mkdir(parents=True, exist_ok=True)

        self.id_gen = LuhmannIDGenerator()
        self.zettels: dict[str, Zettel] = {}
        self.tag_index: dict[str, list[str]] = {}  # tag -> list of zettel IDs

        # Create index/MOC file
        self._create_index()

        logger.info("Zettelkasten writer initialized at %s", self.zk_root)

    def _create_index(self) -> None:
        """Create main index note."""
        index_path = self.zk_root / "0 Index.md"
        if not index_path.exists():
            content = """---
tags: [MOC, index]
---

# Zettelkasten Index

## By Domain
```dataview
TABLE length(rows) as Count
FROM "Zettelkasten"
WHERE stage
GROUP BY stage
```

## Recent Notes
```dataview
TABLE title, stage, created
FROM "Zettelkasten"
SORT created DESC
LIMIT 20
```

## Tags
```dataview
LIST
FROM "Zettelkasten"
GROUP BY tags
```
"""
            index_path.write_text(content, encoding="utf-8")

    def add_from_dikiwi_result(self, result: dict[str, Any], source: str = "") -> list[str]:
        """Convert DIKIWI result into Zettels.

        Returns list of created zettel IDs.
        """
        created_ids: list[str] = []

        # Process insights -> main zettels
        if "INSIGHT" in result.get("stages", {}):
            insight_stage = result["stages"]["INSIGHT"]
            if "insights" in insight_stage:
                for insight in insight_stage["insights"]:
                    zettel_id = self._create_insight_zettel(insight, source)
                    created_ids.append(zettel_id)

        # Process wisdom -> principle zettels
        if "WISDOM" in result.get("stages", {}):
            wisdom_stage = result["stages"]["WISDOM"]
            if "wisdom_items" in wisdom_stage:
                for wisdom in wisdom_stage["wisdom_items"]:
                    zettel_id = self._create_wisdom_zettel(wisdom, source, created_ids)
                    created_ids.append(zettel_id)

        # Process data points -> atomic notes
        if "DATA" in result.get("stages", {}):
            data_stage = result["stages"]["DATA"]
            if "data_points" in data_stage:
                for dp in data_stage["data_points"]:
                    zettel_id = self._create_atomic_zettel(dp, source)
                    if zettel_id:
                        created_ids.append(zettel_id)

        # Build links between related zettels
        self._build_links(created_ids)

        return created_ids

    def _create_insight_zettel(self, insight: dict, source: str) -> str:
        """Create a zettel from an insight."""
        zettel_id = self.id_gen.next_main()

        title = insight.get("description", "Untitled")
        content = insight.get("description", "")

        # Extract content-based tags
        tags = self._extract_tags(content)
        if insight.get("insight_type"):
            tags.append(insight["insight_type"])

        zettel = Zettel(
            id=zettel_id,
            title=title,
            content=content,
            tags=tags,
            source=source,
            stage="insight",
        )

        self.zettels[zettel_id] = zettel
        self._index_tags(zettel)
        self._write_zettel(zettel)

        return zettel_id

    def _create_wisdom_zettel(self, wisdom: dict, source: str, related_ids: list[str]) -> str:
        """Create a zettel from wisdom (principle)."""
        # Wisdom is subordinate to related insights
        parent_id = related_ids[-1] if related_ids else self.id_gen.next_main()
        zettel_id = self.id_gen.next_sub(parent_id)

        principle = wisdom.get("principle", "")
        title = principle if principle else "Principle"

        content_parts = [principle]
        if wisdom.get("context"):
            content_parts.extend(["", "## Context", wisdom["context"]])
        if wisdom.get("implications"):
            content_parts.extend(["", "## Implications", wisdom["implications"]])

        content = "\n".join(content_parts)
        tags = self._extract_tags(content) + ["principle", "wisdom"]

        zettel = Zettel(
            id=zettel_id,
            title=title,
            content=content,
            tags=tags,
            links=related_ids[:3],  # Link to related insights
            source=source,
            stage="wisdom",
        )

        self.zettels[zettel_id] = zettel
        self._index_tags(zettel)
        self._write_zettel(zettel)

        return zettel_id

    def _create_atomic_zettel(self, data_point: dict, source: str) -> str | None:
        """Create atomic zettel from data point."""
        content = data_point.get("content", "")
        if not content or len(content) < 10:
            return None

        # Find a parent insight with similar content
        parent_id = self._find_related_zettel(content)
        if parent_id:
            zettel_id = self.id_gen.next_sub(parent_id)
        else:
            zettel_id = self.id_gen.next_main()

        title = content
        tags = self._extract_tags(content)

        zettel = Zettel(
            id=zettel_id,
            title=title,
            content=content,
            tags=tags,
            source=source,
            stage="atomic",
        )

        self.zettels[zettel_id] = zettel
        self._index_tags(zettel)
        self._write_zettel(zettel)

        return zettel_id

    def _extract_tags(self, content: str) -> list[str]:
        """Extract content-based tags from text."""
        tags = []
        content_lower = content.lower()

        # Domain keywords -> tags
        tag_patterns = {
            "AI芯片": ["ai芯片", "芯片架构", "ai chip"],
            "架构": ["架构", "architecture"],
            "量化": ["量化", "quantization", "8bit", "4bit"],
            "EDA": ["eda", "mcp", "tcl"],
            "模型": ["模型", "model", "llm"],
            "推理": ["推理", "inference"],
            "训练": ["训练", "training"],
        }

        for tag, patterns in tag_patterns.items():
            if any(p in content_lower for p in patterns):
                tags.append(tag)

        return tags[:5]  # Limit tags

    def _find_related_zettel(self, content: str) -> str | None:
        """Find a zettel related to this content (simple similarity)."""
        content_words = set(content.lower().split())

        best_match = None
        best_score = 0

        for zid, zettel in self.zettels.items():
            z_words = set(zettel.content.lower().split())
            score = len(content_words & z_words)
            if score > best_score and score > 3:  # Threshold
                best_score = score
                best_match = zid

        return best_match

    def _build_links(self, ids: list[str]) -> None:
        """Build bidirectional links between zettels."""
        for zid in ids:
            if zid not in self.zettels:
                continue
            zettel = self.zettels[zid]

            # Update backlinks on linked zettels
            for linked_id in zettel.links:
                if linked_id in self.zettels:
                    if zid not in self.zettels[linked_id].backlinks:
                        self.zettels[linked_id].backlinks.append(zid)
                        self._write_zettel(self.zettels[linked_id])

    def _index_tags(self, zettel: Zettel) -> None:
        """Add zettel to tag index."""
        for tag in zettel.tags:
            if tag not in self.tag_index:
                self.tag_index[tag] = []
            self.tag_index[tag].append(zettel.id)

    def _write_zettel(self, zettel: Zettel) -> None:
        """Write zettel to disk."""
        filepath = self.zk_root / f"{zettel.id}.md"
        filepath.write_text(zettel.to_markdown(), encoding="utf-8")

    def create_topic_mocs(self) -> None:
        """Create MOCs for each tag cluster."""
        for tag, zettel_ids in self.tag_index.items():
            if len(zettel_ids) < 2:
                continue

            # Sanitize tag for filename
            safe_tag = re.sub(r'[^\w\u4e00-\u9fff-]', "_", tag)
            moc_path = self.zk_root / f"MOC {safe_tag}.md"

            content = f"""---
tags: [MOC, {tag}]
---

# {tag}

## Notes
"""
            for zid in zettel_ids:
                if zid in self.zettels:
                    zettel = self.zettels[zid]
                    content += f"- [[{zid}]] {zettel.title}\n"

            moc_path.write_text(content, encoding="utf-8")

    def get_stats(self) -> dict:
        """Get statistics about the Zettelkasten."""
        return {
            "total_zettels": len(self.zettels),
            "total_tags": len(self.tag_index),
            "tags": list(self.tag_index.keys()),
            "avg_tags_per_zettel": (
                sum(len(z.tags) for z in self.zettels.values()) / len(self.zettels)
                if self.zettels else 0
            ),
        }
