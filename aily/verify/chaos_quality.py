"""Quality checks for source-equivalent 00-Chaos Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SLIDE_HEADING_RE = re.compile(r"^### Slide \d{3}\s*$", re.MULTILINE)
SLIDE_EMBED_RE = re.compile(r"!\[\[(00-Chaos/_assets/[^|\]\n]+?slide-\d{3}\.png)(?:\|[^\]\n]*)?\]\]")
ORIGIN_FIELD_RE = re.compile(r"^\s{2}([a-zA-Z0-9_]+):\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ChaosNoteScore:
    relative_path: str
    passed: bool
    page_count: int
    slide_sections: int
    screenshot_count: int
    screenshot_assets_present: int
    has_source_equivalent_header: bool
    has_parser_markdown: bool
    has_local_paths: bool
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "passed": self.passed,
            "page_count": self.page_count,
            "slide_sections": self.slide_sections,
            "screenshot_count": self.screenshot_count,
            "screenshot_assets_present": self.screenshot_assets_present,
            "has_source_equivalent_header": self.has_source_equivalent_header,
            "has_parser_markdown": self.has_parser_markdown,
            "has_local_paths": self.has_local_paths,
            "failures": self.failures,
        }


def score_chaos_note(path: Path, *, vault_path: Path) -> ChaosNoteScore:
    vault = vault_path.expanduser().resolve()
    note = path.expanduser().resolve()
    markdown = note.read_text(encoding="utf-8")
    relative_path = str(note.relative_to(vault))
    origin = _origin_fields(markdown)

    page_count = _int_field(origin.get("page_count"))
    declared_screenshots = _int_field(origin.get("screenshot_count"))
    slide_sections = len(SLIDE_HEADING_RE.findall(markdown))
    screenshot_links = SLIDE_EMBED_RE.findall(markdown)
    assets_present = sum(1 for link in screenshot_links if (vault / link).is_file())
    screenshot_count = max(declared_screenshots, len(screenshot_links))
    has_source_equivalent_header = "## Source Equivalent" in markdown and origin.get("source_equivalent") == "true"
    has_parser_markdown = "## Parser Markdown" in markdown
    has_local_paths = "/Users/" in markdown or str(vault) in markdown

    failures: list[str] = []
    if page_count <= 0:
        failures.append("missing_page_count")
    if slide_sections != page_count:
        failures.append("slide_sections_do_not_match_page_count")
    if screenshot_count != page_count:
        failures.append("screenshot_count_does_not_match_page_count")
    if assets_present != page_count:
        failures.append("screenshot_assets_missing")
    if not has_source_equivalent_header:
        failures.append("missing_source_equivalent_header")
    if not has_parser_markdown:
        failures.append("missing_parser_markdown")
    if has_local_paths:
        failures.append("human_note_contains_local_paths")

    return ChaosNoteScore(
        relative_path=relative_path,
        passed=not failures,
        page_count=page_count,
        slide_sections=slide_sections,
        screenshot_count=screenshot_count,
        screenshot_assets_present=assets_present,
        has_source_equivalent_header=has_source_equivalent_header,
        has_parser_markdown=has_parser_markdown,
        has_local_paths=has_local_paths,
        failures=failures,
    )


def score_chaos_vault(vault_path: Path, *, paths: list[Path] | None = None) -> dict[str, Any]:
    vault = vault_path.expanduser().resolve()
    notes = paths or [
        path
        for path in sorted((vault / "00-Chaos").glob("*.md"))
        if "generation_method: source_equivalent_chaos_markdown" in path.read_text(encoding="utf-8", errors="replace")[:1000]
    ]
    scores = [score_chaos_note(path, vault_path=vault).to_dict() for path in notes if path.is_file()]
    failed = [score for score in scores if not score["passed"]]
    return {
        "origin": {
            "creator": "aily.verify.chaos_quality",
            "generation_method": "source_equivalent_chaos_markdown_quality_score",
            "modified_by_lead_agent": False,
        },
        "vault_path": str(vault),
        "notes_scored": len(scores),
        "passed": not failed and bool(scores),
        "failed_count": len(failed),
        "scores": scores,
    }


def _origin_fields(markdown: str) -> dict[str, str]:
    if not markdown.startswith("---"):
        return {}
    end = markdown.find("\n---", 3)
    if end < 0:
        return {}
    frontmatter = markdown[:end]
    return {match.group(1): match.group(2).strip().strip('"') for match in ORIGIN_FIELD_RE.finditer(frontmatter)}


def _int_field(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(value.strip().strip('"'))
    except ValueError:
        return 0
