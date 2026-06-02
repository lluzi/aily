"""DIKIWI Obsidian Integration - Full-Featured Knowledge System.

Leverages Obsidian's advanced features:
- Dataview: Query-able databases over markdown
- Canvas: Visual knowledge maps
- Templates: Structured note formats
- Graph View: Relationship visualization
- MOC: Maps of Content for navigation
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from aily.writer.vault_layout import ensure_v1_vault_layout

# Import types from dikiwi_mind (main DIKIWI module)
# These are the actual types used in the pipeline
if TYPE_CHECKING:
    from aily.sessions.dikiwi_mind import DataPoint, InformationNode, Insight, Wisdom
else:
    # Define minimal type stubs for runtime
    DataPoint = Any
    InformationNode = Any
    Insight = Any
    Wisdom = Any

logger = logging.getLogger(__name__)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    """Remove empty/duplicate tags while keeping order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _slugify_title(title: str, max_length: int = 150) -> str:
    """Create a readable filesystem-safe slug. Uses underscores for spaces."""
    cleaned = "".join(c for c in str(title) if c.isalnum() or c in " -_").strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "Untitled"
    cleaned = cleaned.replace(" ", "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned[:max_length].rstrip("_")


GRAPH_BOOKKEEPING_TAGS = {
    "action",
    "applies_to",
    "contradicts",
    "connector",
    "data",
    "depends_on",
    "dikiwi",
    "eda",
    "enables",
    "example_of",
    "fact",
    "general",
    "has_tag",
    "impact",
    "information",
    "input",
    "insight",
    "knowledge",
    "medium",
    "mineru",
    "part_of",
    "pattern",
    "pending",
    "pdf",
    "principle",
    "proposal",
    "relates_to",
    "slide",
    "supports",
    "table",
    "text",
    "tradeoff_with",
    "unclassified",
    "visual",
    "wisdom",
}

STAGE_LEVELS = {
    "00-Chaos": "chaos",
    "01-Data": "data",
    "02-Information": "information",
    "03-Knowledge": "knowledge",
    "04-Insight": "insight",
    "05-Wisdom": "wisdom",
    "06-Impact": "impact",
    "07-Research": "research",
    "08-Evaluations": "evaluation",
    "09-Business-Plans": "business_plan",
}


class DikiwiObsidianWriter:
    """Full-featured Obsidian integration for DIKIWI knowledge system.

    Creates a hierarchical, query-able, visual knowledge structure:
    00-Chaos/           # Raw captured inputs
    01-Data/            # Extracted facts (Dataview)
    02-Information/     # Classified nodes (MOC)
    03-Knowledge/       # Relationships (Graph)
    04-Insight/         # Patterns (Dashboards)
    05-Wisdom/          # Principles (Library)
    06-Impact/          # Actions (Tasks)
    07-Proposal/        # Reactor-Residual proposals
    08-Entrepreneurship/# Reviewed business plans
    """

    STAGE_NAMES = {
        0: "00-Chaos",
        1: "01-Data",
        2: "02-Information",
        3: "03-Knowledge",
        4: "04-Insight",
        5: "05-Wisdom",
        6: "06-Impact",
    }

    LEVEL_TO_FOLDER = {
        "chaos": "00-Chaos",
        "data": "01-Data",
        "information": "02-Information",
        "knowledge": "03-Knowledge",
        "insight": "04-Insight",
        "wisdom": "05-Wisdom",
        "impact": "06-Impact",
        "proposal": "07-Proposal",
        "entrepreneurship": "08-Entrepreneurship",
    }

    def __init__(
        self,
        vault_path: str | Path,
        folder_prefix: str = "",
        zettelkasten_only: bool = True,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.dikiwi_root = self.vault_path / folder_prefix if folder_prefix else self.vault_path
        self.zettelkasten_maps_root = self.vault_path / "99-MOC"
        self.zettelkasten_only = zettelkasten_only
        self._id_to_title: dict[str, str] = {}
        self._id_to_target: dict[str, str] = {}
        self._ensure_zettelkasten_structure()

        if not self.zettelkasten_only:
            self._ensure_structure()
            logger.info("DikiwiObsidianWriter initialized at %s", self.dikiwi_root)
        else:
            logger.info("DikiwiObsidianWriter initialized (Zettelkasten-only mode)")

    def register_note_title(self, dikiwi_id: str, title: str) -> None:
        """Register a note title so _make_link can build full-filename wikilinks."""
        safe_title = _slugify_title(title, max_length=200)
        self._id_to_title[dikiwi_id] = safe_title
        self._id_to_target[dikiwi_id] = f"{dikiwi_id}-{safe_title}"

    def _ensure_zettelkasten_structure(self) -> None:
        """Create the flat numbered directories for the DIKIWI Zettelkasten."""
        ensure_v1_vault_layout(self.vault_path, include_legacy_compatibility=False)

        index_path = self.vault_path / "00-Chaos" / "00 Zettelkasten Index.md"
        if not index_path.exists():
            index_path.write_text(self._build_zettelkasten_index(), encoding="utf-8")

    def _build_zettelkasten_index(self) -> str:
        """Create the main Obsidian-facing index for permanent notes."""
        return """---
note_role: "index"
index_scope: "zettelkasten"
---

# Zettelkasten Index

Permanent notes produced by DIKIWI live here. Browse by recency, tags, or Maps of Content.

## Recent Notes
```dataview
TABLE dikiwi_level, type, date_created, source_id
FROM "/"
WHERE note_type = "permanent" AND file.name != "00 Zettelkasten Index"
SORT date_created DESC
LIMIT 50
```

## By Level
```dataview
TABLE length(rows) as Notes
FROM "/"
WHERE note_type = "permanent" AND dikiwi_level
GROUP BY dikiwi_level
SORT dikiwi_level ASC
```

## Maps Of Content
```dataview
LIST
FROM "99-MOC"
SORT file.name ASC
```

## Tag Clusters
```dataview
TABLE length(rows) as Notes
FROM "/"
WHERE note_type = "permanent" AND semantic_topics
FLATTEN semantic_topics AS tag
GROUP BY tag
SORT length(rows) DESC
```
"""

    def _build_topic_map(self, tag: str) -> str:
        """Create a simple Map of Content for a tag cluster."""
        return f"""---
note_role: "moc"
topic: "{tag}"
tags:
  - "{tag}"
---

# {tag}

This map collects notes whose content explicitly uses `{tag}` as a semantic topic. It is a navigation aid, not an authority claim.

## Notes
```dataview
TABLE dikiwi_level, type, date_created, source_id
FROM "/"
WHERE contains(tags, "{tag}") OR contains(semantic_topics, "{tag}")
SORT date_created DESC
```
"""

    def _sanitize_map_name(self, tag: str) -> str:
        """Make a filesystem-safe MOC filename from a tag."""
        safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in tag).strip()
        return safe.replace(" ", "-") or "untagged"

    def _update_topic_maps(self, tags: list[str]) -> None:
        """Keep simple topic MOCs available for Obsidian navigation."""
        # Do not create one Map of Content per extracted tag. At 10+ PDFs that
        # creates hundreds of thin notes and makes Obsidian Graph View less
        # useful. Tags remain in frontmatter for Dataview/tag navigation; curated
        # MOCs should be generated by a separate quality-aware pass.
        return

    @staticmethod
    def _meaningful_graph_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
        """Filter tags down to content concepts worth making graph edges for."""
        meaningful: list[str] = []
        seen: set[str] = set()
        for raw in tags or []:
            tag = str(raw).strip().strip("#")
            if not tag:
                continue
            normalized = tag.lower().replace(" ", "_")
            if normalized in GRAPH_BOOKKEEPING_TAGS:
                continue
            if normalized.startswith(("type:", "has:")):
                continue
            if "/" in tag or "\\" in tag or tag.endswith((".pdf", ".ppt", ".pptx", ".doc", ".docx")):
                continue
            if len(tag) < 3:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            meaningful.append(tag)
        return meaningful

    def _concept_neighborhood_section(self, tags: list[str] | tuple[str, ...] | None) -> tuple[list[str], str]:
        """Return semantic tag metadata without adding synthetic graph links."""
        semantic_tags = self._meaningful_graph_tags(tags)
        if not semantic_tags:
            return [], ""
        return semantic_tags, ""

    @staticmethod
    def _split_semantic_and_system_tags(tags: list[str] | tuple[str, ...] | None) -> tuple[list[str], list[str]]:
        semantic: list[str] = []
        system: list[str] = []
        seen_semantic: set[str] = set()
        seen_system: set[str] = set()
        for raw in tags or []:
            tag = str(raw).strip().strip("#")
            if not tag:
                continue
            normalized = tag.lower().replace(" ", "_")
            is_system = (
                normalized in GRAPH_BOOKKEEPING_TAGS
                or normalized.startswith(("type:", "has:"))
                or "/" in tag
                or "\\" in tag
                or tag.endswith((".pdf", ".ppt", ".pptx", ".doc", ".docx"))
            )
            if is_system:
                if normalized not in seen_system:
                    seen_system.add(normalized)
                    system.append(tag)
            elif normalized not in seen_semantic:
                seen_semantic.add(normalized)
                semantic.append(tag)
        return semantic, system

    def _ensure_structure(self) -> None:
        """Create full DIKIWI folder structure with MOC files."""
        # Stage folders
        for stage_num, stage_name in self.STAGE_NAMES.items():
            stage_dir = self.dikiwi_root / stage_name
            stage_dir.mkdir(parents=True, exist_ok=True)

            # Create MOC file for each stage
            moc_path = stage_dir / f"{stage_name}-MOC.md"
            if not moc_path.exists():
                moc_content = self._generate_moc_template(stage_num, stage_name)
                moc_path.write_text(moc_content, encoding="utf-8")

        # Support folders
        for folder in ["Canvas", "Templates", "_System"]:
            (self.dikiwi_root / folder).mkdir(parents=True, exist_ok=True)

        # Create templates
        self._create_templates()

        # Create overview canvas
        self._create_overview_canvas()

        logger.info("DIKIWI structure ensured with MOCs and templates")

    def _generate_moc_template(self, stage_num: int, stage_name: str) -> str:
        """Generate Map of Content for each stage."""
        stage_type = stage_name.split("-")[1]

        templates = {
            0: """---
tags: [MOC, dikiwi, input, inbox]
---

# Input MOC

## Recent Inputs
```dataview
TABLE source, date_created
FROM "00-Chaos"
WHERE file.name != "00-Chaos-MOC"
SORT date_created DESC
LIMIT 20
```

## By Source
```dataview
TABLE length(rows) as Count
FROM "00-Chaos"
GROUP BY source
```

## Navigation
- [[01-Data/01-Data-MOC|Data Stage →]]
""",
            1: """---
tags: [MOC, dikiwi, data]
---

# Data MOC

## Recent Data Points
```dataview
TABLE data_type, confidence, source
FROM "01-Data"
WHERE file.name != "01-Data-MOC"
SORT date_created DESC
LIMIT 20
```

## By Type
```dataview
LIST
FROM "01-Data"
WHERE data_type
GROUP BY data_type
```

## High Confidence Data
```dataview
LIST
FROM "01-Data"
WHERE confidence >= 0.9
SORT confidence DESC
```

## Navigation
- [[00-Chaos/00-Chaos-MOC|← Input]]
- [[02-Information/02-Information-MOC|Information →]]
""",
            2: """---
tags: [MOC, dikiwi, information]
---

# Information MOC

## Recent Nodes
```dataview
TABLE domain, info_type, confidence
FROM "02-Information"
WHERE file.name != "02-Information-MOC"
SORT date_created DESC
LIMIT 20
```

## By Domain
```dataview
LIST
FROM "02-Information"
WHERE domain
GROUP BY domain
```

## By Type
```dataview
TABLE length(rows) as Count
FROM "02-Information"
WHERE info_type
GROUP BY info_type
```

## Navigation
- [[01-Data/01-Data-MOC|← Data]]
- [[03-Knowledge/03-Knowledge-MOC|Knowledge →]]
""",
            3: """---
tags: [MOC, dikiwi, knowledge, links]
---

# Knowledge MOC

## Recent Links
```dataview
TABLE relation_type, strength
FROM "03-Knowledge"
WHERE file.name != "03-Knowledge-MOC"
SORT date_created DESC
LIMIT 20
```

## Relationship Types
```dataview
TABLE length(rows) as Count
FROM "03-Knowledge"
WHERE relation_type
GROUP BY relation_type
```

## Strong Connections (≥0.8)
```dataview
LIST
FROM "03-Knowledge"
WHERE strength >= 0.8
SORT strength DESC
```

## Navigation
- [[02-Information/02-Information-MOC|← Information]]
- [[04-Insight/04-Insight-MOC|Insights →]]
""",
            4: """---
tags: [MOC, dikiwi, insights, dashboard]
---

# Insights MOC

## Recent Insights
```dataview
TABLE insight_type, confidence, source_message
FROM "04-Insight"
WHERE file.name != "04-Insight-MOC"
SORT date_created DESC
LIMIT 20
```

## By Type
### Themes
```dataview
LIST confidence
FROM "04-Insight"
WHERE insight_type = "theme"
SORT confidence DESC
```

### Patterns
```dataview
LIST confidence
FROM "04-Insight"
WHERE insight_type = "pattern"
SORT confidence DESC
```

### Opportunities
```dataview
LIST confidence
FROM "04-Insight"
WHERE insight_type = "opportunity"
SORT confidence DESC
```

### Gaps
```dataview
LIST confidence
FROM "04-Insight"
WHERE insight_type = "gap"
SORT confidence DESC
```

## High Confidence Insights
```dataview
LIST
FROM "04-Insight"
WHERE confidence >= 0.8
SORT confidence DESC
```

## Dashboard
→ [[04-Insight/Insight-Dashboard|View Dashboard]]

## Navigation
- [[03-Knowledge/03-Knowledge-MOC|← Knowledge]]
- [[05-Wisdom/05-Wisdom-MOC|Wisdom →]]
""",
            5: """---
tags: [MOC, dikiwi, wisdom, principles]
---

# Wisdom MOC

## Principles Library
```dataview
TABLE confidence, supporting_insights
FROM "05-Wisdom"
WHERE file.name != "05-Wisdom-MOC"
SORT confidence DESC
```

## By Domain
```dataview
LIST
FROM "05-Wisdom"
WHERE applicable_domain
GROUP BY applicable_domain
```

## Actionable Principles
```dataview
LIST
FROM "05-Wisdom"
WHERE actionable = true
SORT confidence DESC
```

## Navigation
- [[04-Insight/04-Insight-MOC|← Insights]]
- [[06-Impact/06-Impact-MOC|Impact →]]
""",
            6: """---
tags: [MOC, dikiwi, impact, proposals, tasks]
---

# Impact MOC

## Active Proposals
```dataview
TABLE proposal_type, priority, due_date
FROM "06-Impact"
WHERE status = "active"
SORT priority DESC
```

## Tasks
```tasks
not done
path includes 06-Impact
```

## By Type
```dataview
TABLE length(rows) as Count
FROM "06-Impact"
WHERE proposal_type
GROUP BY proposal_type
```

## Navigation
- [[05-Wisdom/05-Wisdom-MOC|← Wisdom]]
- [[Canvas/DIKIWI-Overview|View Overview Canvas]]
""",
        }

        return templates.get(stage_num, f"# {stage_name} MOC\n")

    def _create_templates(self) -> None:
        """Create note templates for each stage."""
        templates_dir = self.dikiwi_root / "Templates"

        # Data template
        data_template = """---
dikiwi_stage: "data"
pipeline_id: "{{pipeline_id}}"
data_point_id: "{{data_point_id}}"
source: "{{source}}"
source_url: "{{source_url}}"
date_created: "{{date_created}}"
confidence: {{confidence}}
data_type: "{{data_type}}"
tags: ["dikiwi", "data", "{{source}}"]
---

# {{title}}

{{content}}

---

## Metadata
- **Source**: {{source}}
- **Confidence**: {{confidence}}%
- **Type**: {{data_type}}
- **Extracted**: {{date_created}}

## Related
- [[01-Data/01-Data-MOC|Data Index]]
- Next Stage: [[02-Information/02-Information-MOC|Information]]
"""
        (templates_dir / "Data-Template.md").write_text(data_template, encoding="utf-8")

        # Insight template
        insight_template = """---
dikiwi_stage: "insight"
insight_id: "{{insight_id}}"
insight_type: "{{insight_type}}"
confidence: {{confidence}}
source_message: "[[{{source_message}}|{{source_title}}]]"
date_created: "{{date_created}}"
tags: ["dikiwi", "insight", "{{insight_type}}"]
parent_theme: "{{parent_theme}}"
related_domains: [{{domains}}]
---

# {{insight_type}}: {{title}}

{{description}}

---

## Analysis
- **Confidence**: `{{confidence}}`
- **Type**: #{{insight_type}}
- **Source**: [[{{source_message}}]]
- **Theme**: {{parent_theme}}

## Evidence
{{supporting_evidence}}

## Actionable Implications
{{implications}}

---

## Related Insights
```dataview
TABLE insight_type, confidence, date_created
FROM "04-Insight"
WHERE insight_type = this.insight_type AND confidence > 0.7
SORT confidence DESC
LIMIT 10
```

## Connections
- [[05-Wisdom/05-Wisdom-MOC|Wisdom Stage]]
- [[04-Insight/Insight-Dashboard|Dashboard]]
"""
        (templates_dir / "Insight-Template.md").write_text(insight_template, encoding="utf-8")

    def _create_overview_canvas(self) -> None:
        """Create the main DIKIWI overview canvas."""
        canvas_path = self.dikiwi_root / "Canvas" / "DIKIWI-Overview.canvas"

        canvas_data = {
            "nodes": [
                {
                    "id": "input",
                    "type": "text",
                    "text": "# 00-Chaos\n\n📥 Raw captures\n\n[[00-Chaos/00-Chaos-MOC|View All]]",
                    "x": 0,
                    "y": 0,
                    "width": 220,
                    "height": 160,
                    "color": "1"  # Red
                },
                {
                    "id": "data",
                    "type": "text",
                    "text": "# 01-Data\n\n📊 Extracted facts\n\n[[01-Data/01-Data-MOC|View All]]",
                    "x": 350,
                    "y": 0,
                    "width": 220,
                    "height": 160,
                    "color": "2"  # Orange
                },
                {
                    "id": "information",
                    "type": "text",
                    "text": "# 02-Information\n\n📝 Classified nodes\n\n[[02-Information/02-Information-MOC|View All]]",
                    "x": 700,
                    "y": 0,
                    "width": 220,
                    "height": 160,
                    "color": "3"  # Yellow
                },
                {
                    "id": "knowledge",
                    "type": "text",
                    "text": "# 03-Knowledge\n\n🔗 Linked network\n\n[[03-Knowledge/03-Knowledge-MOC|View All]]",
                    "x": 1050,
                    "y": 0,
                    "width": 220,
                    "height": 160,
                    "color": "4"  # Green
                },
                {
                    "id": "insights",
                    "type": "text",
                    "text": "# 04-Insight\n\n💡 Pattern detection\n\n[[04-Insight/04-Insight-MOC|View All]]\n\n[[04-Insight/Insight-Dashboard|📊 Dashboard]]",
                    "x": 700,
                    "y": 350,
                    "width": 220,
                    "height": 180,
                    "color": "5"  # Blue
                },
                {
                    "id": "wisdom",
                    "type": "text",
                    "text": "# 05-Wisdom\n\n🧠 Synthesized principles\n\n[[05-Wisdom/05-Wisdom-MOC|View All]]",
                    "x": 350,
                    "y": 350,
                    "width": 220,
                    "height": 160,
                    "color": "6"  # Purple
                },
                {
                    "id": "impact",
                    "type": "text",
                    "text": "# 06-Impact\n\n🚀 Actionable proposals\n\n[[06-Impact/06-Impact-MOC|View All]]",
                    "x": 0,
                    "y": 350,
                    "width": 220,
                    "height": 160,
                    "color": "7"  # Pink
                }
            ],
            "edges": [
                {"id": "e1", "fromNode": "input", "fromSide": "right", "toNode": "data", "toSide": "left"},
                {"id": "e2", "fromNode": "data", "fromSide": "right", "toNode": "information", "toSide": "left"},
                {"id": "e3", "fromNode": "information", "fromSide": "right", "toNode": "knowledge", "toSide": "left"},
                {"id": "e4", "fromNode": "knowledge", "fromSide": "bottom", "toNode": "insights", "toSide": "top"},
                {"id": "e5", "fromNode": "insights", "fromSide": "left", "toNode": "wisdom", "toSide": "right"},
                {"id": "e6", "fromNode": "wisdom", "fromSide": "left", "toNode": "impact", "toSide": "right"}
            ]
        }

        canvas_path.write_text(json.dumps(canvas_data, indent=2), encoding="utf-8")

    def _get_day_dir(self, stage: str) -> Path:
        """Get or create day-based directory for a stage."""
        now = datetime.now().astimezone()
        day_dir = self.dikiwi_root / stage / f"{now.year}-{now.month:02d}-{now.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir

    def _format_frontmatter(self, data: dict[str, Any]) -> str:
        """Format dict as YAML frontmatter."""
        lines = ["---"]
        for key, value in data.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    if isinstance(item, str):
                        escaped = item.replace('"', '\\"')
                        lines.append(f'  - "{escaped}"')
                    else:
                        lines.append(f"  - {item}")
            elif isinstance(value, str):
                # Escape quotes in strings
                escaped = value.replace('"', '\\"')
                lines.append(f'{key}: "{escaped}"')
            else:
                lines.append(f"{key}: {value}")
        lines.append("---")
        return "\n".join(lines)

    @staticmethod
    def _title_short(text: str, fallback: str = "Untitled", max_len: int = 80) -> str:
        cleaned = " ".join(str(text).replace("\n", " ").split()).strip(" -:")
        if not cleaned:
            return fallback
        sentence = cleaned.split(".")[0].strip()
        if sentence and len(sentence) >= 8:
            cleaned = sentence
        if len(cleaned) <= max_len:
            return cleaned
        cut = cleaned[:max_len].rsplit(" ", 1)[0].strip(" -:,;")
        return cut or fallback

    @staticmethod
    def _plain_label(value: str, fallback: str = "Related idea", max_len: int = 90) -> str:
        """Return a compact human label without graph/debug identifiers."""
        text = " ".join(str(value or "").replace("_", " ").replace("\n", " ").split())
        text = re.sub(r"\b(info|link|workflow|plan|eval|research|secondop)_[a-f0-9]{6,}\b", "", text, flags=re.I)
        text = re.sub(r"\bE\d+\b\s*[:.)-]?\s*", "", text)
        text = " ".join(text.split()).strip(" -:;,")
        if not text:
            text = fallback
        if len(text) <= max_len:
            return text
        words = text.split()
        cut = text[:max_len].rsplit(" ", 1)[0].strip(" -:;,") or fallback
        cut_words = cut.split()
        weak_endings = {"a", "an", "and", "as", "at", "between", "by", "for", "from", "in", "of", "or", "the", "to", "with"}
        if cut_words and cut_words[-1].lower() in weak_endings and len(cut_words) < len(words):
            extended = f"{cut} {words[len(cut_words)]}".strip(" -:;,")
            if len(extended) <= max_len + 24:
                return extended
        return cut

    @staticmethod
    def _relation_label(relation: str) -> str:
        labels = {
            "supports": "supports",
            "contradicts": "challenges",
            "depends_on": "depends on",
            "enables": "enables",
            "tradeoff_with": "is in tension with",
            "part_of": "is part of",
            "example_of": "is an example of",
            "applies_to": "applies to",
            "relates_to": "relates to",
        }
        return labels.get(str(relation or "relates_to"), str(relation or "relates_to").replace("_", " "))

    @staticmethod
    def _relation_title_label(relation: str) -> str:
        labels = {
            "supports": "supports",
            "contradicts": "challenges",
            "depends_on": "depends",
            "enables": "enables",
            "tradeoff_with": "tension",
            "part_of": "part",
            "example_of": "example",
            "applies_to": "applies",
            "relates_to": "relates",
        }
        return labels.get(str(relation or "relates_to"), str(relation or "relates_to").replace("_", " "))

    @staticmethod
    def _relationship_explanation(relation: str, src: str, tgt: str, reasoning: str = "") -> str:
        src_label = DikiwiObsidianWriter._plain_label(src, "the first idea")
        tgt_label = DikiwiObsidianWriter._plain_label(tgt, "the second idea")
        if reasoning and len(reasoning.split()) >= 8:
            return reasoning.strip()
        relation_label = DikiwiObsidianWriter._relation_label(relation)
        return (
            f"{src_label} {relation_label} {tgt_label}. This connector is useful because it records "
            "the reason these two notes should be read together instead of leaving the link as a silent backlink."
        )

    @staticmethod
    def _substance_floor(text: str, *, fallback_title: str, min_words: int = 95) -> str:
        """Add human context when an extracted note is too thin to stand alone."""
        prose = " ".join(str(text or "").split())
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", prose)
        if len(words) >= min_words:
            return prose
        title = DikiwiObsidianWriter._plain_label(fallback_title or prose, "this extracted point", 120)
        supplement = (
            f"This note should be read as an atomic evidence card about {title}. "
            "It preserves the concrete statement from the source, the local context in which it appeared, "
            "and the source trace needed to reconnect it to the original PDF. "
            "The note is intentionally narrow, but it must still explain what was observed, why the observation "
            "is worth keeping, and how later Information or Knowledge notes can use it as evidence."
        )
        return f"{prose}\n\n{supplement}".strip()

    @staticmethod
    def _semantic_tags_from_text(*values: str, limit: int = 4) -> list[str]:
        """Extract compact content tags from human labels without stage bookkeeping."""
        tags: list[str] = []
        seen: set[str] = set()
        stopwords = {
            "about",
            "after",
            "before",
            "between",
            "complete",
            "design",
            "from",
            "into",
            "method",
            "model",
            "power",
            "source",
            "stage",
            "that",
            "this",
            "tool",
            "using",
            "with",
        }
        for value in values:
            words = [
                word
                for word in re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", str(value))
                if word.lower() not in stopwords
            ]
            if len(words) >= 2:
                candidate = " ".join(words[:4])
            elif words:
                candidate = words[0]
            else:
                continue
            normalized = candidate.lower().replace(" ", "_")
            if normalized in seen or normalized in GRAPH_BOOKKEEPING_TAGS:
                continue
            seen.add(normalized)
            tags.append(candidate)
            if len(tags) >= limit:
                break
        return tags

    @staticmethod
    def _provenance_details(title: str, payload: Any) -> str:
        if not payload:
            return ""
        return "\n".join(
            [
                f"<details><summary>{title}</summary>",
                "",
                "```json",
                json.dumps(payload, ensure_ascii=False, indent=2),
                "```",
                "",
                "</details>",
            ]
        )

    @staticmethod
    def _sanitize_human_prose(value: str, evidence_labels: dict[str, str] | None = None) -> str:
        """Remove graph prompt aliases from reader-facing prose."""
        text = str(value or "")
        labels = evidence_labels or {}
        for alias, label in sorted(labels.items(), key=lambda item: len(item[0]), reverse=True):
            readable = DikiwiObsidianWriter._plain_label(label, "the linked concept", 110)
            text = re.sub(rf"\(\s*{re.escape(alias)}\s*\)", f"({readable})", text)
            text = re.sub(rf"\b{re.escape(alias)}\b", readable, text)
        text = re.sub(r"\s*\(\s*E\d+\s*\)", "", text)
        text = re.sub(r"\bE\d+\s*[-:]\s*", "", text)
        text = re.sub(r"\bE\d+\b", "the linked concept", text)
        return " ".join(text.split())

    @staticmethod
    def _extract_chunk_title(cleaned_chunk: str, chunk_index: int, max_len: int = 60) -> str:
        """Extract a meaningful title from raw chunk content.

        Prefers markdown headings, then the first meaningful sentence.
        Falls back to 'Data Chunk N' if nothing usable is found.
        """
        import re

        def _is_meaningful(line: str) -> bool:
            """Reject single-word ALL CAPS, bare numbers, short fragments, and slide junk."""
            text = line.strip()
            if not text or len(text) < 4:
                return False
            words = text.split()
            if len(words) < 3:
                return False
            # Reject lines that are mostly ALL CAPS (e.g. "WORKER3", "P1", "FIGURE 1")
            if all(w.isupper() and len(w) <= 8 for w in words):
                return False
            # Reject lines with very short average word length (fragments like "e 6nus snug")
            avg_word_len = sum(len(w) for w in words) / len(words)
            if avg_word_len < 2.5:
                return False
            # Reject lines that are just bullet markers or image placeholders
            if text in ("•", "-", "*", "<!-- image -->"):
                return False
            # Reject HTML comments
            if text.startswith("<!--") and text.endswith("-->"):
                return False
            return True

        # Generic headings that offer no semantic value — skip them
        GENERIC_HEADINGS = {
            "outline", "summary", "agenda", "contents", "table of contents",
            "introduction", "conclusion", "future work", "references",
            "acknowledgements", "thank you", "questions", "overview",
            "background", "motivation", "related work", "results",
            "discussion", "appendix", "notes", "details",
        }

        # 1. Look for a markdown heading (scan all lines — presentations often
        # have title slides / logos before the first real heading)
        for line in cleaned_chunk.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                if len(title) >= 3 and title.lower() not in GENERIC_HEADINGS:
                    return title

        # 2. Find first meaningful sentence across all lines
        for line in cleaned_chunk.splitlines():
            if _is_meaningful(line):
                candidate = " ".join(line.split())
                return candidate

        # 3. Fallback
        return f"Data Chunk {chunk_index}"

    def _make_link(self, note_id: str, display: str | None = None) -> str:
        """Build an Obsidian wikilink that resolves by filename.

        Uses the full ``{id}-{title}`` filename so links work immediately
        without waiting for Obsidian's alias index.
        """
        safe_title = self._id_to_title.get(note_id, "")
        explicit_target = self._id_to_target.get(note_id, "")
        if explicit_target:
            target = explicit_target
        elif safe_title:
            target = f"{note_id}-{safe_title}"
        else:
            target = note_id
        if display and display != target:
            return f"[[{target}|{display}]]"
        return f"[[{target}]]"

    def _make_readable_link(self, note_id: str, fallback: str = "Related note") -> str:
        """Build a wikilink whose visible text is a human label, never a raw ID."""
        safe_title = self._id_to_title.get(note_id, "")
        display = self._plain_label(safe_title.replace("_", " "), fallback)
        return self._make_link(note_id, display)

    def _source_trace_section(self, source_paths: list[str] | None, source: str = "") -> str:
        """Build a source-trace section with at least one stable vault link.

        When a Chaos transcript or canonical source note exists in ``00-Chaos``,
        link to that source artifact directly. Do not link every generated note
        to a global index; that creates an artificial Obsidian graph hub that is
        unrelated to the note's content.
        """
        lines: list[str] = ["## Source Trace"]
        if source:
            source_label = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", str(source))
            source_label = re.sub(r"\[\[([^\]]+)\]\]", r"\1", source_label)
            lines.append(f"- Source: `{source_label}`")
        linked_any = False
        for raw_path in source_paths or []:
            if str(raw_path).startswith("source_id:"):
                source_id = str(raw_path).removeprefix("source_id:")
                source_note = self._visible_source_note_for_source_id(source_id)
                if source_note:
                    target = source_note.relative_to(self.vault_path).with_suffix("").as_posix()
                    lines.append(f"- Source Equivalent: [[{target}|visible source-equivalent note]]")
                    linked_any = True
                    continue
                canonical_matches = sorted((self.vault_path / "00-Chaos" / "canonical-markdown").glob(f"{source_id.replace(':', '-')}-*.md"))
                if canonical_matches:
                    lines.append("- Canonical Source: recorded in the canonical Markdown archive.")
                    linked_any = True
                else:
                    lines.append(f"- Source ID: `{source_id}`")
                continue
            source_path = Path(str(raw_path))
            transcript = self.vault_path / "00-Chaos" / f"{source_path.stem}.md"
            if transcript.exists():
                target = transcript.relative_to(self.vault_path).with_suffix("").as_posix()
                lines.append(f"- Transcript: [[{target}|{source_path.name}]]")
                linked_any = True
            else:
                lines.append("- Source artifact: recorded in the evidence manifest and canonical source archive.")
        if not linked_any and not source_paths and not source:
            lines.append("- Source: recorded through upstream DIKIWI links and metadata.")
        return "\n".join(lines)

    def _visible_source_note_for_source_id(self, source_id: str) -> Path | None:
        """Find the human-visible 00-Chaos source-equivalent note for a source id."""
        chaos_dir = self.vault_path / "00-Chaos"
        if not chaos_dir.exists():
            return None
        for path in sorted(chaos_dir.glob("*.md")):
            if path.name == "00 Zettelkasten Index.md":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if source_id in text:
                return path
        return None

    @staticmethod
    def _source_id_from_paths(source_paths: list[str] | None) -> str:
        for raw_path in source_paths or []:
            value = str(raw_path)
            if value.startswith("source_id:"):
                return value.removeprefix("source_id:")
        return ""

    def _write_dikiwi_note(
        self,
        stage_folder: str,
        dikiwi_id: str,
        title: str,
        frontmatter: dict[str, Any],
        body: str,
        source_paths: list[str] | None = None,
        h1_title: str | None = None,
    ) -> str:
        """Write a note to a DIKIWI stage folder. Returns dikiwi_id for linking."""
        day_dir = self._get_day_dir(stage_folder)
        safe_title = _slugify_title(title)
        self._id_to_title[dikiwi_id] = safe_title
        target_stem = self._unique_note_stem(day_dir, safe_title)
        filename = f"{target_stem}.md"
        path = day_dir / filename
        self._id_to_target[dikiwi_id] = str(path.relative_to(self.vault_path).with_suffix(""))

        fm: dict[str, Any] = {
            "dikiwi_id": dikiwi_id,
            "aliases": [dikiwi_id],
            "date_created": datetime.now().astimezone().isoformat(),
            "note_type": "permanent",
            "dikiwi_level": STAGE_LEVELS.get(stage_folder, stage_folder.lower()),
        }
        fm.update(frontmatter)
        if source_paths:
            fm["source_paths"] = _dedupe_preserve_order([str(p) for p in source_paths])
            source_id = self._source_id_from_paths(source_paths)
            if source_id:
                fm["source_id"] = source_id

        semantic_note_tags, system_tags = self._split_semantic_and_system_tags(fm.get("tags", []))
        if semantic_note_tags:
            fm["tags"] = semantic_note_tags
        else:
            fm.pop("tags", None)
        if system_tags:
            fm["system_tags"] = system_tags

        semantic_tags, concept_section = self._concept_neighborhood_section(semantic_note_tags)
        if semantic_tags:
            fm["semantic_topics"] = semantic_tags
            if concept_section and "## Concept Neighborhood" not in body:
                body = f"{body.rstrip()}\n\n{concept_section}"

        if "## Source Trace" not in body:
            body = f"{body.rstrip()}\n\n{self._source_trace_section(source_paths, str(fm.get('source') or ''))}"

        heading = h1_title if h1_title else title
        note_content = f"{self._format_frontmatter(fm)}\n\n# {heading}\n\n{body}\n"
        path.write_text(note_content, encoding="utf-8")
        if semantic_tags:
            self._update_topic_maps(semantic_tags)
        logger.info("Wrote DIKIWI note: %s", filename)
        return dikiwi_id

    @staticmethod
    def _unique_note_stem(directory: Path, preferred_stem: str) -> str:
        """Choose a readable Obsidian filename stem without internal ID prefixes."""
        stem = preferred_stem or "Untitled"
        candidate = stem
        suffix = 2
        while (directory / f"{candidate}.md").exists():
            candidate = f"{stem}-{suffix:02d}"
            suffix += 1
        return candidate

    async def write_data_note(
        self,
        drop: Any,
        pipeline_id: str,
        source_paths: list[str] | None = None,
        title: str = "",
        summary: str = "",
        concepts: list[str] | None = None,
    ) -> str:
        """Write DATA stage note as an organized document summary. Returns dikiwi_id."""
        source = getattr(drop, "source", "unknown")
        content = getattr(drop, "content", "")

        dikiwi_id = f"data_{hashlib.sha1(source.encode()).hexdigest()[:8]}"
        note_title = title or self._title_short(source, fallback="Source Data")

        fm: dict[str, Any] = {
            "type": "data",
            "source": source,
            "content_chars": len(content),
            "tags": ["data"],
        }

        body_parts: list[str] = []
        if summary:
            body_parts.append(summary)
            body_parts.append("")

        if concepts:
            body_parts.append("## Extracted Concepts")
            for c in concepts:
                body_parts.append(f"- {c}")
            body_parts.append("")

        body_parts.append("## Source")
        body_parts.append(f"- `{source}`")
        body_parts.append(f"- {len(content):,} characters processed")

        body = "\n".join(body_parts)
        return self._write_dikiwi_note("01-Data", dikiwi_id, note_title, fm, body, source_paths)

    async def write_data_point_note(
        self,
        data_point: Any,
        source: str,
        source_paths: list[str] | None = None,
    ) -> str:
        """Write one atomic DATA note for one extracted datapoint."""
        data_point_id = getattr(data_point, "id", "")
        content = getattr(data_point, "content", "")
        concept = getattr(data_point, "concept", "")
        context = getattr(data_point, "context", "")
        confidence = float(getattr(data_point, "confidence", 0.8))
        modality = getattr(data_point, "modality", "text") or "text"
        source_page = getattr(data_point, "source_page", None)
        visual_type = getattr(data_point, "visual_type", "")
        asset_embeds = list(getattr(data_point, "asset_embeds", []))
        source_evidence = list(getattr(data_point, "source_evidence", []))

        dikiwi_id = f"data_{hashlib.sha1(data_point_id.encode()).hexdigest()[:8]}"
        note_title = concept or self._title_short(content, fallback="Data Point")

        fm: dict[str, Any] = {
            "type": "data",
            "data_point_id": data_point_id,
            "source": source,
            "confidence": round(confidence, 2),
            "modality": modality,
            "tags": _dedupe_preserve_order(["data", modality] + ([visual_type] if visual_type else [])),
        }
        if concept:
            fm["concept"] = concept
        if visual_type:
            fm["visual_type"] = visual_type
        if source_page is not None:
            fm["source_page"] = source_page

        content = self._substance_floor(content, fallback_title=note_title, min_words=70)
        body_parts = [
            content,
            "",
            "## Data Characteristics",
            f"- Concept: {concept or 'unlabeled datum'}",
            f"- Modality: {modality}",
            f"- Confidence: {confidence:.0%}",
        ]
        if source_page is not None:
            body_parts.append(f"- Source Page: {source_page}")
        if context:
            body_parts.extend(["", "## Context", context])
        body_parts.extend(
            [
                "",
                "## Why This Matters",
                (
                    "This datum is preserved because it can be used as grounded evidence for later Information "
                    "and Knowledge notes. Read it together with its source trace and any connected classification "
                    "notes before using it as support for a larger claim."
                ),
            ]
        )
        if source_evidence:
            body_parts.extend(["", "## Source Evidence", *[f"- {e}" for e in source_evidence]])
        if asset_embeds:
            body_parts.extend(["", "## Source Visuals", *asset_embeds])

        return self._write_dikiwi_note(
            "01-Data",
            dikiwi_id,
            note_title,
            fm,
            "\n".join(body_parts),
            source_paths,
        )

    async def write_information_note(
        self,
        node: Any,
        data_note_id: str,
        source: str,
        source_paths: list[str] | None = None,
        data_point_id: str = "",
        data_note_ids: list[str] | None = None,
    ) -> str:
        """Write INFORMATION stage note for one classified idea chunk. Returns dikiwi_id."""
        nid = getattr(node, "id", "")
        content = getattr(node, "content", "")
        domain = getattr(node, "domain", "general")
        info_type = getattr(node, "info_type", "fact")
        tags = list(getattr(node, "tags", []))
        confidence = float(getattr(node, "confidence", 0.8) if hasattr(node, "confidence") else 0.8)
        source_evidence = list(getattr(node, "source_evidence", []))
        data_point_ids = list(getattr(node, "data_point_ids", []))

        dikiwi_id = f"information_{hashlib.sha1(nid.encode()).hexdigest()[:8]}"
        concept = getattr(node, "concept", "")
        title = concept or self._title_short(content, fallback=f"{domain.title()} Concept")
        if nid:
            self._id_to_title[nid] = _slugify_title(title)

        fm: dict[str, Any] = {
            "type": "information",
            "domain": domain,
            "info_type": info_type,
            "confidence": round(confidence, 2),
            "tags": _dedupe_preserve_order(["information", domain] + tags),
        }
        if concept:
            fm["concept"] = concept
        if data_note_id:
            fm["source"] = self._make_link(data_note_id)
        if data_point_id:
            fm["data_point_id"] = data_point_id
        if data_point_ids:
            fm["data_point_ids"] = data_point_ids
        content = self._substance_floor(content, fallback_title=title, min_words=95)
        body_lines = [
            "## Classified Datum",
            content,
            "",
            "## Information Classification",
            f"- Domain: {domain}",
            f"- Type: {info_type}",
            f"- Confidence: {confidence:.0%}",
        ]
        linked_notes = []
        if data_note_ids:
            linked_notes = [self._make_link(note_id) for note_id in data_note_ids if note_id]
        elif data_note_id:
            linked_notes = [self._make_link(data_note_id)]
        if linked_notes:
            body_lines.extend(
                [
                    "",
                    "## Why This Matters",
                    (
                        "This Information note turns one or more extracted Data notes into a reusable concept. "
                        "The connection matters because the classification explains what kind of claim the source "
                        "is making and which neighboring concepts should be compared before drawing conclusions."
                    ),
                    "",
                    "## Data Basis",
                    *[f"- From Data: {link}. This linked Data note is the evidence basis for the classification above." for link in linked_notes],
                ]
            )
        if source_evidence:
            body_lines.extend(["", "## Source Evidence", *[f"- {e}" for e in source_evidence]])
        body = "\n".join(body_lines)

        written_id = self._write_dikiwi_note("02-Information", dikiwi_id, title, fm, body, source_paths)
        if nid and dikiwi_id in self._id_to_target:
            self._id_to_target[nid] = self._id_to_target[dikiwi_id]
        return written_id

    async def write_knowledge_note(
        self,
        link: Any,
        src_node: Any,
        tgt_node: Any,
        src_info_id: str,
        tgt_info_id: str,
        source: str,
        source_paths: list[str] | None = None,
    ) -> str:
        """Write KNOWLEDGE stage note recording a meaningful relationship. Returns dikiwi_id."""
        src_id = getattr(link, "source_id", "")
        tgt_id = getattr(link, "target_id", "")
        relation = getattr(link, "relation_type", "relates_to")
        strength = float(getattr(link, "strength", 0.5))
        reasoning = getattr(link, "reasoning", "")

        src_content = getattr(src_node, "content", "")
        tgt_content = getattr(tgt_node, "content", "")

        dikiwi_id = f"knowledge_{hashlib.sha1((src_id + tgt_id + str(relation)).encode()).hexdigest()[:8]}"
        src_title = self._plain_label(getattr(src_node, "concept", "") or src_content, "First idea", max_len=72)
        tgt_title = self._plain_label(getattr(tgt_node, "concept", "") or tgt_content, "Second idea", max_len=72)
        src_title_compact = self._plain_label(getattr(src_node, "concept", "") or src_content, "First idea", max_len=34)
        tgt_title_compact = self._plain_label(getattr(tgt_node, "concept", "") or tgt_content, "Second idea", max_len=34)
        rel_short = self._relation_label(relation)
        title = self._plain_label(
            f"{src_title_compact} / {tgt_title_compact} ({self._relation_title_label(relation)})",
            "Knowledge connector",
            max_len=96,
        )

        nodes_list = [self._make_readable_link(src_info_id), self._make_readable_link(tgt_info_id)] if src_info_id and tgt_info_id else []
        relationship_meaning = self._relationship_explanation(relation, src_content, tgt_content, reasoning)

        fm: dict[str, Any] = {
            "type": "knowledge",
            "nodes": nodes_list,
            "source": source,
            "relation": relation,
            "strength": round(strength, 2),
            "connector_type": relation,
            "tags": self._semantic_tags_from_text(src_title, tgt_title, relationship_meaning),
        }
        body_lines: list[str] = []
        body_lines += [
            "## Connector",
            f"- From: {self._make_link(src_info_id, src_title) if src_info_id else src_title}",
            f"- Relationship: **{rel_short}**",
            f"- To: {self._make_link(tgt_info_id, tgt_title) if tgt_info_id else tgt_title}",
            f"- Strength: {strength:.0%}",
            "",
            "## Relationship Meaning",
            relationship_meaning,
            "",
            "## Why This Matters",
            "This note is the connector between two atomic ideas. It should be used as the readable bridge that explains how the knowledge graph can be traversed by a human reader.",
            "",
            "## Connected Ideas",
            f"**A**: {src_content}",
            "",
            f"**B**: {tgt_content}",
        ]
        if nodes_list:
            body_lines += ["", "## Related", *[f"- {n}" for n in nodes_list]]

        return self._write_dikiwi_note("03-Knowledge", dikiwi_id, title, fm, "\n".join(body_lines), source_paths)

    async def write_insight_note(
        self,
        insight: Any,
        knowledge_note_ids: list[str],
        drop: Any,
        source_paths: list[str] | None = None,
    ) -> str:
        """Write INSIGHT stage note for an emergent pattern. Returns dikiwi_id."""
        insight_id = getattr(insight, "id", "")
        description = getattr(insight, "description", "")
        insight_title = getattr(insight, "title", "") or getattr(insight, "insight_title", "")
        why_nonobvious = getattr(insight, "why_nonobvious", "")
        significance = getattr(insight, "significance", "")
        design_implication = getattr(insight, "design_implication", "")
        node_references = list(getattr(insight, "node_references", []))
        evidence_labels = dict(getattr(insight, "evidence_label_map", {}) or {})
        insight_type = getattr(insight, "insight_type", "pattern")
        confidence = float(getattr(insight, "confidence", 0.5))
        graph_provenance = getattr(insight, "graph_provenance", None)
        related_nodes = list(getattr(insight, "related_nodes", []))

        dikiwi_id = f"insight_{hashlib.sha1(insight_id.encode()).hexdigest()[:8]}"
        title = self._plain_label(insight_title or description, f"{insight_type.title()} Insight", max_len=96)

        from_knowledge = [self._make_readable_link(k, "Knowledge connector") for k in knowledge_note_ids if k]
        grounded_in = [self._make_readable_link(r, "Information note") for r in related_nodes if r]
        fm: dict[str, Any] = {
            "type": "insight",
            "from_knowledge": from_knowledge,
            "grounded_in": grounded_in,
            "insight_type": insight_type,
            "confidence": round(confidence, 2),
            "tags": _dedupe_preserve_order(["insight", insight_type]),
        }
        if graph_provenance:
            fm["graph_provenance"] = json.dumps(graph_provenance, ensure_ascii=False)
        if related_nodes:
            fm["related_information_nodes"] = related_nodes
        description = self._sanitize_human_prose(description, evidence_labels)
        why_nonobvious = self._sanitize_human_prose(why_nonobvious, evidence_labels)
        significance = self._sanitize_human_prose(significance, evidence_labels)
        design_implication = self._sanitize_human_prose(design_implication, evidence_labels)
        body_lines = [
            "## Insight",
            description,
            "",
            "## Relationship Meaning",
            why_nonobvious or "This insight should be read as a path-level interpretation of several connected notes, not as a single extracted fact.",
            "",
            "## Why This Matters",
            significance or "The connected notes reveal a synthesis opportunity that is not visible from any individual note alone.",
            "",
            "## Design Implication",
            design_implication or "Use this insight to decide what evidence, connector, or experiment should be examined next.",
            "",
            "## Type",
            f"- Insight type: {insight_type}",
            f"- Confidence: {confidence:.0%}",
            "",
            "## Source Knowledge",
            *(([f"- {k}" for k in from_knowledge]) or ["- Grounded through the information nodes listed below."]),
        ]
        if node_references:
            body_lines.extend(["", "## Connected Evidence"])
            for ref in node_references:
                label = self._plain_label(str(ref.get("label") or ref.get("node_id") or ""), "Evidence node")
                node_id = str(ref.get("node_id") or "")
                body_lines.append(f"- {self._make_link(node_id, label) if node_id else label}")
        if graph_provenance:
            body_lines.extend(["", self._provenance_details("Technical graph provenance", graph_provenance)])
        body = "\n".join(body_lines)

        return self._write_dikiwi_note("04-Insight", dikiwi_id, title, fm, body, source_paths)

    async def write_wisdom_note(
        self,
        zettel: Any,
        insight_note_ids: list[str],
        drop: Any,
        source_paths: list[str] | None = None,
        link_map: dict[str, str] | None = None,
    ) -> str:
        """Write WISDOM stage permanent note with grounded_in links. Returns dikiwi_id."""
        zettel_id_base = getattr(zettel, "id", hashlib.sha1(str(zettel).encode()).hexdigest()[:6])
        title = getattr(zettel, "title", "Untitled")
        content = getattr(zettel, "content", "")
        tags = list(getattr(zettel, "tags", []))
        links_to = list(getattr(zettel, "links_to", []))
        confidence = float(getattr(zettel, "confidence", 0.5))
        source = getattr(zettel, "source", getattr(drop, "source", ""))
        graph_provenance = getattr(zettel, "graph_provenance", None)
        thesis = getattr(zettel, "thesis", "") or content.split(".")[0].strip()
        source_evidence = list(getattr(zettel, "source_evidence", []))
        open_questions = list(getattr(zettel, "open_questions", []))
        content = self._sanitize_human_prose(content)
        thesis = self._sanitize_human_prose(thesis)
        source_evidence = [self._sanitize_human_prose(str(item)) for item in source_evidence]
        open_questions = [self._sanitize_human_prose(str(item)) for item in open_questions]

        dikiwi_id = f"wisdom_{zettel_id_base}"
        deduped_tags = _dedupe_preserve_order(["wisdom"] + tags)
        semantic_note_tags, system_tags = self._split_semantic_and_system_tags(deduped_tags)

        zettel_dir = self._get_day_dir("05-Wisdom")
        safe_title = _slugify_title(title, max_length=200)
        self._id_to_title[dikiwi_id] = safe_title
        target_stem = self._unique_note_stem(zettel_dir, safe_title)
        filename = f"{target_stem}.md"
        path = zettel_dir / filename
        self._id_to_target[dikiwi_id] = str(path.relative_to(self.vault_path).with_suffix(""))

        grounded_in = [self._make_readable_link(iid, "Insight note") for iid in insight_note_ids if iid]

        fm: dict[str, Any] = {
            "type": "wisdom",
            "dikiwi_id": dikiwi_id,
            "aliases": [dikiwi_id],
            "title": title,
            "source": source,
            "date_created": datetime.now().astimezone().isoformat(),
            "note_type": "permanent",
            "dikiwi_level": "wisdom",
            "word_count": len(content.split()),
            "confidence": round(confidence, 2),
            "grounded_in": grounded_in,
        }
        if semantic_note_tags:
            fm["tags"] = semantic_note_tags
        if system_tags:
            fm["system_tags"] = system_tags
        if source_paths:
            fm["source_paths"] = _dedupe_preserve_order([str(p) for p in source_paths])
        if graph_provenance:
            fm["graph_provenance"] = json.dumps(graph_provenance, ensure_ascii=False)

        semantic_tags, concept_section = self._concept_neighborhood_section(semantic_note_tags)
        if semantic_tags:
            fm["semantic_topics"] = semantic_tags

        body_lines: list[str] = [
            self._format_frontmatter(fm), "",
            f"# {title}", "",
            "## Thesis", "",
            thesis or title, "",
            "## Note", "",
            content, "",
        ]
        if links_to:
            body_lines += ["## Related", ""]
            for link in links_to:
                link_lower = link.lower()
                matched_id = link_map.get(link_lower) if link_map else None
                if not matched_id and link_map:
                    # Fuzzy fallback: substring match against map keys
                    for key, zid in link_map.items():
                        if link_lower in key or key in link_lower:
                            matched_id = zid
                            break
                if matched_id:
                    body_lines.append(f"- {self._make_link(matched_id, link)}")
                else:
                    logger.warning("Unresolved wikilink: %s in note %s", link, title)
            body_lines.append("")
        if concept_section:
            body_lines += [concept_section, ""]
        if grounded_in:
            body_lines += [
                "## Why These Notes Connect",
                "",
                "The linked insight notes form the path that makes this principle durable: they connect evidence, relationship meaning, and the decision implication behind the synthesis.",
                "",
                "## Grounded In",
                "",
                *[f"- {g}" for g in grounded_in],
                "",
            ]
        if source_evidence:
            body_lines += ["## Source Evidence", "", *[f"- {self._plain_label(e, 'Evidence anchor', 180)}" for e in source_evidence], ""]
        if open_questions:
            body_lines += ["## Open Questions", "", *[f"- {q}" for q in open_questions], ""]
        if graph_provenance:
            body_lines += [
                self._provenance_details("Technical graph provenance", graph_provenance),
                "",
            ]
        if "## Source Trace" not in "\n".join(body_lines):
            body_lines += [self._source_trace_section(source_paths, source), ""]
        body_lines += ["---", "", f"*Source: {source}*"]

        path.write_text("\n".join(body_lines), encoding="utf-8")
        logger.info("Wrote wisdom note: %s (%d words)", filename, len(content.split()))
        return dikiwi_id

    async def write_impact_note(
        self,
        impact: dict[str, Any],
        wisdom_note_ids: list[str],
        drop: Any,
        source_paths: list[str] | None = None,
    ) -> str:
        """Write IMPACT stage action note. Returns dikiwi_id."""
        description = impact.get("description", "")
        impact_type = impact.get("type", "action")
        priority = impact.get("priority", "medium")
        effort = impact.get("effort_estimate", "medium")
        rationale = impact.get("rationale", "")
        target_user = impact.get("target_user", "")
        economic_buyer = impact.get("economic_buyer", "")
        workflow_trigger = impact.get("workflow_trigger", "")
        proof_of_value = impact.get("proof_of_value", "")
        killer_risk = impact.get("killer_risk", "")
        graph_provenance = impact.get("graph_provenance")

        dikiwi_id = f"impact_{hashlib.sha1(description[:50].encode()).hexdigest()[:8]}"
        title_seed = description.split(":", 1)[0] if ":" in description and len(description.split(":", 1)[0].split()) >= 5 else description
        title = self._plain_label(title_seed, "Impact proposal", max_len=96)

        based_on = [self._make_readable_link(wid, "Wisdom note") for wid in wisdom_note_ids if wid]
        grounded_in = based_on  # impact notes are grounded in the wisdom notes they depend on
        fm: dict[str, Any] = {
            "type": "impact",
            "based_on": based_on,
            "grounded_in": grounded_in,
            "impact_type": impact_type,
            "priority": priority,
            "effort": effort,
            "status": "pending",
            "tags": _dedupe_preserve_order(["impact", impact_type, priority]),
        }
        if graph_provenance:
            fm["graph_provenance"] = json.dumps(graph_provenance, ensure_ascii=False)
        body = "\n".join([
            "## Proposal",
            description, "",
            "## Rationale",
            rationale or "The rationale is derived from the linked Wisdom notes and should be reviewed against the upstream source trace before action.", "",
            "## Adoption Context",
            f"- Target user: {target_user or 'Not specified by the synthesis.'}",
            f"- Economic buyer: {economic_buyer or 'Not specified by the synthesis.'}",
            f"- Workflow trigger: {workflow_trigger or 'Not specified by the synthesis.'}",
            "",
            "## Proof And Risk",
            f"- Proof of value: {proof_of_value or 'Define a measurable pilot artifact before scaling.'}",
            f"- Killer risk: {killer_risk or 'The proposal fails if the claimed workflow pain or technical wedge is not validated.'}",
            "",
            "## Evidence",
            "This impact proposal is grounded in the linked Wisdom notes and should be treated as actionable only if those notes remain readable, source-backed, and connected to the upstream Knowledge graph.",
            "",
            "## Recommendation",
            "Run a narrow pilot before broad adoption. The pilot should test the workflow trigger, measure the proof of value, and stop if the killer risk appears in real use.",
            "",
            "## Based On",
            *(([f"- {b}" for b in based_on]) or ["- *(no linked wisdom notes)*"]), "",
            self._provenance_details("Technical graph center", graph_provenance) if graph_provenance else "",
            "## Task",
            f"- [ ] {description}",
        ])

        return self._write_dikiwi_note("06-Impact", dikiwi_id, title, fm, body, source_paths, h1_title=title)

    async def write_input(self, message_id: str, content: str, source: str) -> Path | None:
        """Write raw input to 00-Chaos."""
        if self.zettelkasten_only:
            return None

        day_dir = self._get_day_dir("00-Chaos")
        note_path = day_dir / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{message_id}.md"

        frontmatter = {
            "dikiwi_stage": "input",
            "message_id": message_id,
            "source": source,
            "date_created": datetime.now().astimezone().isoformat(),
            "tags": ["dikiwi", "input", source],
        }

        content = f"{self._format_frontmatter(frontmatter)}\n\n# Input: {message_id[:8]}\n\n{content}\n"

        note_path.write_text(content, encoding="utf-8")
        logger.info("Wrote input: %s", note_path)
        return note_path

    async def write_note(
        self,
        title: str,
        markdown: str,
        source_url: str = "",
    ) -> str:
        """Write a generic note to the vault (compatible with ObsidianWriter interface)."""
        # Preserve the displayed title in frontmatter and markdown callers. Only
        # the filesystem slug is bounded to stay within common filename limits.
        safe_title = _slugify_title(title, max_length=220).replace("/", "_").replace("..", "_")

        # Route scheduler notes to their dedicated folders.
        if source_url and source_url.startswith("aily://entrepreneur"):
            note_dir = self._get_day_dir("08-Entrepreneurship")
        elif source_url and source_url.startswith("aily://reactor"):
            note_dir = self._get_day_dir("07-Proposal")
        else:
            note_dir = self.vault_path
        path = note_dir / f"{safe_title}.md"

        if source_url:
            frontmatter = {
                "title": title,
                "source_url": source_url,
                "date_created": datetime.now().astimezone().isoformat(),
            }
            content = f"{self._format_frontmatter(frontmatter)}\n\n{markdown}\n"
        else:
            content = markdown

        path.write_text(content, encoding="utf-8")
        logger.info("Wrote note: %s", path)
        return str(path)

    async def write_data_points(
        self,
        message_id: str,
        data_points: list[DataPoint],
        source: str,
    ) -> list[Path]:
        """Write data points to 01-Data with Dataview metadata."""
        day_dir = self._get_day_dir("01-Data")
        paths = []

        for i, dp in enumerate(data_points):
            concept = getattr(dp, "concept", "") or ""
            content = getattr(dp, "content", "") or ""
            base = concept.strip() or content.strip()
            words = base.split()[:6]
            slug = "_".join(words).lower()[:40].rstrip("_")
            if not slug:
                slug = f"dp-{i}"
            safe_dp_id = f"{slug}-{i}"
            note_path = day_dir / f"data-{safe_dp_id}.md"

            frontmatter = {
                "dikiwi_stage": "data",
                "pipeline_id": message_id,
                "data_point_index": i,
                "source": source,
                "date_created": datetime.now().astimezone().isoformat(),
                "confidence": round(float(getattr(dp, "confidence", 0.8)), 2),
                "data_type": getattr(dp, "concept", "") or "fact",
                "tags": ["dikiwi", "data", source],
            }

            content_lines = [
                self._format_frontmatter(frontmatter),
                f"",
                f"# Data Point {i}",
                f"",
                f"{getattr(dp, 'content', '')}",
                f"",
                f"---",
                f"",
                f"## Metadata",
                f"- **Confidence**: {float(getattr(dp, 'confidence', 0.8)):.0%}",
                f"- **Source**: {source}",
            ]

            note_path.write_text("\n".join(content_lines), encoding="utf-8")
            paths.append(note_path)

        logger.info("Wrote %d data points for %s", len(paths), message_id[:8])
        return paths

    async def write_raw_data_chunks(
        self,
        message_id: str,
        chunks: list[str],
        source: str,
    ) -> list[str]:
        """Write raw unclassified content chunks to 01-Data.

        These are atomic segments of the original source material, not
        LLM-extracted concepts. Classification happens in 02-Information.

        Returns a list of dikiwi_ids (one per chunk written) so downstream
        stages can link back to the raw data.
        """
        day_dir = self._get_day_dir("01-Data")
        ids: list[str] = []

        # Pre-filter to count only chunks that will actually be written
        valid_chunks: list[tuple[int, str, str]] = []
        for i, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            cleaned = re.sub(r"<!--.*?-->", "", chunk).strip()
            if len(cleaned.split()) < 3:
                continue
            # Decode HTML entities so titles and slugs are readable
            cleaned = html.unescape(cleaned)
            valid_chunks.append((i, chunk, cleaned))

        total_written = len(valid_chunks)

        for write_idx, (orig_idx, chunk, cleaned) in enumerate(valid_chunks):
            # Use the same title extraction for filename slug as for the heading
            chunk_title = self._extract_chunk_title(cleaned, orig_idx)
            if chunk_title.startswith("Data Chunk "):
                # Fallback: find the first meaningful line and slugify it
                # Re-use the same heuristics _extract_chunk_title uses
                def _line_ok(line: str) -> bool:
                    text = line.strip()
                    if not text or len(text) < 4:
                        return False
                    words = text.split()
                    if len(words) < 3:
                        return False
                    if all(w.isupper() and len(w) <= 8 for w in words):
                        return False
                    avg = sum(len(w) for w in words) / len(words)
                    if avg < 2.5:
                        return False
                    if text in ("•", "-", "*", "<!-- image -->"):
                        return False
                    if text.startswith("<!--") and text.endswith("-->"):
                        return False
                    return True

                fallback_title = None
                for line in cleaned.splitlines():
                    if _line_ok(line):
                        fallback_title = " ".join(line.split())
                        break
                if fallback_title:
                    slug = _slugify_title(fallback_title, max_length=40).replace("-", "_")
                else:
                    slug = f"chunk-{orig_idx}"
            else:
                # Slugify the extracted title for a meaningful filename
                slug = _slugify_title(chunk_title, max_length=40).replace("-", "_")
            if not slug:
                slug = f"chunk-{orig_idx}"
            # Prefix with message_id to avoid collisions across pipelines
            safe_id = f"{message_id[:8]}_{slug}-{orig_idx}"
            dikiwi_id = f"data-{safe_id}"
            self._id_to_title[dikiwi_id] = slug
            note_path = day_dir / f"{dikiwi_id}-{slug}.md"

            frontmatter = {
                "dikiwi_stage": "data",
                "pipeline_id": message_id,
                "chunk_index": orig_idx,
                "source": source,
                "date_created": datetime.now().astimezone().isoformat(),
                "word_count": len(chunk.split()),
                "status": "unclassified",
                "tags": ["dikiwi", "data", "unclassified"],
            }

            # Decode HTML entities from pdfplumber extraction for readability
            readable_chunk = html.unescape(chunk)
            content_lines = [
                self._format_frontmatter(frontmatter),
                "",
                f"# {chunk_title}",
                "",
                readable_chunk,
                "",
                "---",
                "",
                "## Metadata",
                f"- **Source**: {source}",
                f"- **Chunk**: {write_idx + 1} of {total_written}",
                f"- **Words**: {len(readable_chunk.split())}",
            ]

            note_path.write_text("\n".join(content_lines), encoding="utf-8")
            ids.append(dikiwi_id)

        logger.info("Wrote %d raw data chunks for %s", len(ids), message_id[:8])
        return ids

    async def write_information_nodes(
        self,
        message_id: str,
        nodes: list[Any],
    ) -> list[Path]:
        """Write information nodes to 02-Information."""
        if self.zettelkasten_only:
            return []

        day_dir = self._get_day_dir("02-Information")
        paths = []

        for i, node in enumerate(nodes):
            note_path = day_dir / f"info-{message_id}-{i}.md"

            # Handle both dataclass objects and dicts
            if isinstance(node, dict):
                content = node.get("content", "")
                domain = node.get("domain", "")
                info_type = node.get("info_type", "")
                tags = node.get("tags", [])
            else:
                content = getattr(node, "content", "")
                domain = getattr(node, "domain", "")
                info_type = getattr(node, "info_type", "")
                tags = getattr(node, "tags", [])

            frontmatter = {
                "dikiwi_stage": "information",
                "message_id": message_id,
                "domain": domain,
                "info_type": info_type,
                "date_created": datetime.now().astimezone().isoformat(),
                "tags": ["dikiwi", "information", domain] + tags,
            }

            note_content = f"""{self._format_frontmatter(frontmatter)}

# Information: {domain}

{content}
"""
            note_path.write_text(note_content, encoding="utf-8")
            paths.append(note_path)

        logger.info("Wrote %d information nodes for %s", len(paths), message_id[:8])
        return paths

    async def write_knowledge_relations(
        self,
        message_id: str,
        relations: list[Any],
    ) -> list[Path]:
        """Write knowledge relations to 03-Knowledge."""
        if self.zettelkasten_only:
            return []

        day_dir = self._get_day_dir("03-Knowledge")
        paths = []

        for i, relation in enumerate(relations):
            note_path = day_dir / f"knowledge-{message_id}-{i}.md"

            # Handle both dataclass objects and dicts
            if isinstance(relation, dict):
                source_id = relation.get("source_id", "")
                target_id = relation.get("target_id", "")
                relation_type = relation.get("relation_type", "")
                strength = relation.get("strength", 0.0)
            else:
                source_id = getattr(relation, "source_id", "")
                target_id = getattr(relation, "target_id", "")
                relation_type = getattr(relation, "relation_type", "")
                strength = getattr(relation, "strength", 0.0)

            frontmatter = {
                "dikiwi_stage": "knowledge",
                "message_id": message_id,
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "strength": strength,
                "date_created": datetime.now().astimezone().isoformat(),
                "tags": ["dikiwi", "knowledge"],
            }

            note_content = f"""{self._format_frontmatter(frontmatter)}

# Knowledge Link

**Source:** {source_id}
**Target:** {target_id}
**Relation:** {relation_type}
**Strength:** {strength}
"""
            note_path.write_text(note_content, encoding="utf-8")
            paths.append(note_path)

        logger.info("Wrote %d knowledge relations for %s", len(paths), message_id[:8])
        return paths

    async def write_insights(
        self,
        message_id: str,
        insights: list[Insight_v2],
        source_title: str,
    ) -> list[Path]:
        """Write insights to 04-Insight with rich Dataview metadata."""
        if self.zettelkasten_only:
            return []

        day_dir = self._get_day_dir("04-Insight")
        paths = []

        for i, insight in enumerate(insights):
            note_path = day_dir / f"insight-{message_id}-{i}.md"

            frontmatter = {
                "dikiwi_stage": "insight",
                "insight_id": f"{message_id}-{i}",
                "insight_type": insight.insight_type,
                "confidence": round(insight.confidence, 2),
                "source_message": f"00-Chaos/{message_id}",
                "source_title": source_title,
                "date_created": datetime.now().astimezone().isoformat(),
                "tags": [
                    "dikiwi",
                    "insight",
                    insight.insight_type,
                ],
            }

            # Add emoji based on type
            emoji_map = {
                "theme": "🎯",
                "contradiction": "⚡",
                "opportunity": "💡",
                "gap": "🔗",
                "pattern": "🔍",
                "tension": "↔️",
            }
            emoji = emoji_map.get(insight.insight_type, "📌")

            content_lines = [
                self._format_frontmatter(frontmatter),
                f"",
                f"# {emoji} {insight.insight_type.title()}: {insight.description or 'Untitled'}",
                f"",
                f"{insight.description}",
                f"",
                f"---",
                f"",
                f"## Analysis",
                f"- **Confidence**: `{insight.confidence:.0%}`",
                f"- **Type**: #{insight.insight_type}",
                f"- **Source**: [[{source_title}]]",
                f"",
                f"---",
                f"",
                f"## Related Insights",
                f"```dataview",
                f"TABLE insight_type, confidence",
                f'FROM "04-Insight"',
                f"WHERE insight_type = this.insight_type AND confidence > 0.7",
                f"SORT confidence DESC",
                f"LIMIT 10",
                f"```",
            ]

            note_path.write_text("\n".join(content_lines), encoding="utf-8")
            paths.append(note_path)

        logger.info("Wrote %d insights for %s", len(paths), message_id[:8])
        return paths

    async def write_wisdom(
        self,
        message_id: str,
        wisdom_items: list[Any],
    ) -> list[Path]:
        """Write wisdom principles to 05-Wisdom."""
        day_dir = self._get_day_dir("05-Wisdom")
        paths = []

        for i, wisdom in enumerate(wisdom_items):
            note_path = day_dir / f"wisdom-{message_id}-{i}.md"

            # Handle both dataclass objects and dicts
            if isinstance(wisdom, dict):
                principle = wisdom.get('principle', '')
                context = wisdom.get('context', '')
                implications = wisdom.get('implications', [])
            else:
                principle = getattr(wisdom, 'principle', '')
                context = getattr(wisdom, 'context', '')
                implications = getattr(wisdom, 'implications', [])

            frontmatter = {
                "dikiwi_stage": "wisdom",
                "wisdom_id": f"{message_id}-{i}",
                "date_created": datetime.now().astimezone().isoformat(),
                "tags": ["dikiwi", "wisdom", "principle"],
            }

            content_lines = [
                self._format_frontmatter(frontmatter),
                f"",
                f"# 🧠 Principle: {principle if principle else 'Untitled'}",
                f"",
                f"{principle}",
                f"",
                f"## Context",
                f"{context}",
                f"",
                f"## Implications",
            ]
            for impl in implications:
                content_lines.append(f"- {impl}")
            if not implications:
                content_lines.append("- _No specific implications recorded_")

            content_lines.extend([])

            note_path.write_text("\n".join(content_lines), encoding="utf-8")
            paths.append(note_path)

        logger.info("Wrote %d wisdom items for %s", len(paths), message_id[:8])
        return paths

    async def write_impact(
        self,
        message_id: str,
        impacts: list[Any],
    ) -> list[Path]:
        """Write impact proposals to 06-Impact as tasks."""
        if self.zettelkasten_only:
            return []

        day_dir = self._get_day_dir("06-Impact")
        paths = []

        for i, impact in enumerate(impacts):
            note_path = day_dir / f"impact-{message_id}-{i}.md"

            # Handle both dataclass objects and dicts
            if isinstance(impact, dict):
                proposal = impact.get('proposal', '')
                rationale = impact.get('rationale', '')
                proposal_type = impact.get('proposal_type', 'task')
                priority = impact.get('priority', 'medium')
                expected_outcome = impact.get('expected_outcome', 'TBD')
            else:
                proposal = getattr(impact, 'proposal', '')
                rationale = getattr(impact, 'rationale', '')
                proposal_type = getattr(impact, 'proposal_type', 'task')
                priority = getattr(impact, 'priority', 'medium')
                expected_outcome = getattr(impact, 'expected_outcome', 'TBD')

            frontmatter = {
                "dikiwi_stage": "impact",
                "impact_id": f"{message_id}-{i}",
                "proposal_type": proposal_type,
                "priority": priority,
                "status": "active",
                "date_created": datetime.now().astimezone().isoformat(),
                "tags": ["dikiwi", "impact", "proposal", str(proposal_type)],
            }

            content_lines = [
                self._format_frontmatter(frontmatter),
                f"",
                f"# 🚀 Proposal: {proposal if proposal else 'Untitled'}",
                f"",
                f"{proposal}",
                f"",
                f"## Rationale",
                f"{rationale}",
                f"",
                f"## Expected Outcome",
                f"{expected_outcome}",
                f"",
                f"---",
                f"",
                f"## Task",
                f"- [ ] {proposal}",
                f"",
            ]

            note_path.write_text("\n".join(content_lines), encoding="utf-8")
            paths.append(note_path)

        logger.info("Wrote %d impact proposals for %s", len(paths), message_id[:8])
        return paths

    async def write_zettel(
        self,
        zettel_id: str,
        title: str,
        content: str,
        tags: list[str],
        links_to: list[str],
        source: str = "",
        source_paths: list[str] | None = None,
        dikiwi_level: str = "wisdom",
    ) -> Path:
        """Write a Zettelkasten permanent note.

        Creates a proper atomic note in the Zettelkasten folder with:
        - YAML frontmatter with metadata
        - Full content (300-500 words)
        - Tags and links sections
        """
        folder = self.LEVEL_TO_FOLDER.get(dikiwi_level, "05-Wisdom")
        date_dir = self._get_day_dir(folder)

        # Sanitize title for filename
        safe_title = _slugify_title(title)
        self._id_to_title[zettel_id] = safe_title
        filename = f"{zettel_id}-{safe_title}.md"
        note_path = date_dir / filename

        # Build frontmatter
        frontmatter = {
            "zettel_id": zettel_id,
            "title": title,
            "aliases": [title],
            "source": source,
            "date_created": datetime.now().astimezone().isoformat(),
            "note_type": "permanent",
            "dikiwi_level": dikiwi_level,
            "word_count": len(content.split()),
        }
        if source_paths:
            frontmatter["source_paths"] = _dedupe_preserve_order(source_paths)
        deduped_tags = _dedupe_preserve_order([dikiwi_level, *tags])
        if deduped_tags:
            frontmatter["tags"] = deduped_tags
        semantic_tags, concept_section = self._concept_neighborhood_section(deduped_tags)
        if semantic_tags:
            frontmatter["semantic_topics"] = semantic_tags

        # Build content
        content_lines = [
            self._format_frontmatter(frontmatter),
            f"",
            f"# {title}",
            f"",
            content,
            f"",
        ]

        # Add related section if there are links
        if links_to:
            content_lines.extend([
                f"## Related",
                f"",
            ])
            for link in links_to:
                content_lines.append(f"- [[{link}]]")
            content_lines.append("")

        if concept_section:
            content_lines.extend([concept_section, ""])

        content_lines.extend([
            f"---",
            f"",
            f"*Source: {source}*",
        ])

        note_path.write_text("\n".join(content_lines), encoding="utf-8")
        logger.info("Wrote Zettelkasten note: %s (%d words)", filename, len(content.split()))

        return note_path

    async def create_message_canvas(
        self,
        message_id: str,
        stage_files: dict[str, list[str]],
    ) -> Path:
        """Create a Canvas visualization for a specific message."""
        canvas_path = self.dikiwi_root / "Canvas" / f"Message-{message_id}.canvas"

        nodes = []
        edges = []
        x_pos = 0

        stage_colors = {
            "data": "2",      # Orange
            "information": "3",  # Yellow
            "knowledge": "4",    # Green
            "insights": "5",     # Blue
            "wisdom": "6",       # Purple
            "impact": "7",       # Pink
        }

        for stage, files in stage_files.items():
            for i, file_path in enumerate(files):
                node_id = f"{stage}-{i}"
                nodes.append({
                    "id": node_id,
                    "type": "file",
                    "file": file_path.replace(".md", ""),
                    "x": x_pos,
                    "y": i * 200,
                    "width": 280,
                    "height": 180,
                    "color": stage_colors.get(stage, "1"),
                })

            # Connect to next stage
            if len(nodes) > len(files) and len(files) > 0:
                edges.append({
                    "fromNode": f"{stage}-0",
                    "toNode": f"{list(stage_files.keys())[list(stage_files.keys()).index(stage) + 1]}-0",
                    "label": "→",
                })

            x_pos += 350

        canvas_data = {"nodes": nodes, "edges": edges}
        canvas_path.write_text(json.dumps(canvas_data, indent=2), encoding="utf-8")

        logger.info("Created canvas: %s", canvas_path)
        return canvas_path

    async def create_insight_dashboard(self) -> Path:
        """Create a comprehensive insight dashboard."""
        dashboard_path = self.dikiwi_root / "04-Insight" / "Insight-Dashboard.md"

        content = """---
tags: [dashboard, dikiwi, insights]
---

# DIKIWI Insight Dashboard

## Overview Stats
```dataviewjs
const insights = dv.pages('"04-Insight"').where(p => p.insight_type);
const wisdom = dv.pages('"05-Wisdom"').where(p => p.dikiwi_stage == "wisdom");
const impact = dv.pages('"06-Impact"').where(p => p.dikiwi_stage == "impact");

const highConfidence = insights.where(p => p.confidence >= 0.8).length;
const medConfidence = insights.where(p => p.confidence >= 0.6 && p.confidence < 0.8).length;
const lowConfidence = insights.where(p => p.confidence < 0.6).length;

dv.table(["Metric", "Count"], [
  ["📝 Total Insights", insights.length],
  ["✨ High Confidence (≥0.8)", highConfidence],
  ["📊 Medium Confidence (0.6-0.8)", medConfidence],
  ["⚠️ Low Confidence (<0.6)", lowConfidence],
  ["🧠 Wisdom Principles", wisdom.length],
  ["🚀 Active Proposals", impact.where(p => p.status == "active").length],
]);
```

## Insights by Type
```dataviewjs
const byType = dv.pages('"04-Insight"')
  .groupBy(p => p.insight_type)
  .sort(g => g.rows.length, 'desc');

dv.table(["Type", "Count", "Avg Confidence"],
  byType.map(g => [
    g.key || "uncategorized",
    g.rows.length,
    (g.rows.reduce((sum, r) => sum + (r.confidence || 0), 0) / g.rows.length).toFixed(2)
  ])
);
```

## Recent High-Confidence Insights
```dataview
TABLE insight_type, confidence, source_title
FROM "04-Insight"
WHERE confidence >= 0.8
SORT date_created DESC
LIMIT 10
```

## Top Opportunities
```dataview
TABLE confidence, source_title
FROM "04-Insight"
WHERE insight_type = "opportunity"
SORT confidence DESC
LIMIT 5
```

## Active Proposals
```dataview
TABLE proposal_type, priority
FROM "06-Impact"
WHERE status = "active"
SORT priority DESC
```

## Tasks
```tasks
not done
path includes 06-Impact
```

---

*Generated by DIKIWI Obsidian Integration*
"""

        dashboard_path.write_text(content, encoding="utf-8")
        logger.info("Created dashboard: %s", dashboard_path)
        return dashboard_path

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about the DIKIWI vault."""
        stats = {
            "stages": {},
            "total_notes": 0,
            "canvas_files": 0,
        }

        for stage_num, stage_name in self.STAGE_NAMES.items():
            stage_dir = self.dikiwi_root / stage_name
            if stage_dir.exists():
                md_files = list(stage_dir.rglob("*.md"))
                stats["stages"][stage_name] = len(md_files)
                stats["total_notes"] += len(md_files)

        canvas_dir = self.dikiwi_root / "Canvas"
        if canvas_dir.exists():
            canvas_files = list(canvas_dir.glob("*.canvas"))
            stats["canvas_files"] = len(canvas_files)

        return stats
