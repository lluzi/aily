"""Deterministic quality scoring for generated Obsidian vault output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any


HIGH_VALUE_DIRS = {"04-Insight", "05-Wisdom", "06-Impact", "08-Evaluations", "09-Business-Plans"}
REPORT_DIRS = {"06-Impact", "08-Evaluations", "09-Business-Plans"}
ALLOWED_ORPHAN_DIRS = {"00-Chaos"}

DEBUG_PATTERNS = (
    re.compile(r"\bwf_[a-f0-9]{12,}\b", re.IGNORECASE),
    re.compile(r"\bplan_[a-f0-9]{12,}\b", re.IGNORECASE),
    re.compile(r"\beval_[a-f0-9]{12,}\b", re.IGNORECASE),
    re.compile(r"\bresearch_[a-f0-9]{12,}\b", re.IGNORECASE),
    re.compile(r"\bsecondop_[a-f0-9]{12,}\b", re.IGNORECASE),
    re.compile(r"\bsource_[a-f0-9]{8,}\b", re.IGNORECASE),
    re.compile(r"\binformation_[a-f0-9]{8,}\b", re.IGNORECASE),
    re.compile(r"\binsight_[a-f0-9]{8,}\b", re.IGNORECASE),
    re.compile(r"\b[a-f0-9]{64}\b", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
    re.compile(r"\bE\d+\b"),
    re.compile(r"\b(langgraph_thread_id|graph_db_path|checkpoint_db_path)\b", re.IGNORECASE),
    re.compile(r"/Users/[^\s)`]+"),
)

PLACEHOLDER_PATTERNS = (
    "untitled",
    "not recorded",
    "no rationale provided",
    "no graph provenance recorded",
    "no linked knowledge notes",
    "see linked",
    "tbd",
    "todo",
    "estimate only after",
    "must be validated",
    "requires better evidence-backed synthesis",
    "this evidence node",
)


@dataclass(frozen=True)
class QualityThresholds:
    overall_score: float = 85.0
    dimension_floor: float = 75.0
    source_clarity: float = 80.0
    content_substance: float = 78.0
    report_substance: float = 80.0
    note_pass_rate: float = 0.95
    high_value_note_floor: float = 80.0
    max_index_link_count: int = 0
    max_index_link_note_ratio: float = 0.05
    max_generic_tag_share: float = 0.45
    max_unresolved_link_count: int = 0
    min_valid_connector_ratio: float = 0.95
    min_info_connector_coverage: float = 0.75
    min_info_pair_density: float = 0.20


def score_note(path: Path, vault_root: Path) -> dict[str, Any]:
    """Score a single markdown note for human-facing vault quality."""
    vault_root = vault_root.expanduser().resolve()
    path = path.expanduser().resolve()
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _split_frontmatter(text)
    prose = _strip_non_prose(body)
    rel = str(path.relative_to(vault_root)) if path.is_relative_to(vault_root) else str(path)
    top_dir = rel.split("/", 1)[0]
    title = _first_h1(body) or path.stem
    words = _words(prose)
    links = _body_wikilinks(body)

    dimensions = {
        "human_readability": _score_human_readability(body, prose, words),
        "source_clarity": _score_source_clarity(frontmatter, body, top_dir),
        "content_substance": _score_content_substance(frontmatter, body, prose, words, top_dir),
        "link_context_quality": _score_link_context(body, prose, links, top_dir),
        "zettelkasten_connector_quality": _score_connector_quality(frontmatter, body, top_dir, links),
        "report_substance": _score_report_substance(body, prose, words, top_dir),
        "title_quality": _score_title(title, path.stem, top_dir),
        "debug_id_absence": _score_debug_absence(prose),
    }
    score = _weighted_score(dimensions)
    debug_hits = _debug_hits(prose)
    failures = _note_failures(dimensions, score, debug_hits, top_dir)
    return {
        "path": rel,
        "top_dir": top_dir,
        "title": title,
        "word_count": len(words),
        "wikilink_count": len(links),
        "score": round(score, 2),
        "dimensions": {key: round(value, 2) for key, value in dimensions.items()},
        "debug_id_human_prose_hits": debug_hits,
        "passed": not failures,
        "failures": failures,
    }


def score_vault_output(
    vault_path: Path,
    *,
    generated_paths: list[Path] | None = None,
    thresholds: QualityThresholds | None = None,
) -> dict[str, Any]:
    """Score generated vault output or, if omitted, all markdown in the vault."""
    vault = vault_path.expanduser().resolve()
    thresholds = thresholds or QualityThresholds()
    if generated_paths is None:
        paths = [p for p in sorted(vault.rglob("*.md")) if _is_quality_visible_note(p, vault)]
    else:
        paths = list(generated_paths)
    paths = [p.expanduser().resolve() for p in paths]
    paths = [p for p in paths if p.exists() and p.suffix.lower() == ".md"]
    notes = [score_note(path, vault) for path in paths]
    if not notes:
        return {
            "vault_path": str(vault),
            "note_count": 0,
            "overall_score": 0.0,
            "dimension_scores": {},
            "passed": False,
            "failures": [{"check": "notes_present", "message": "No markdown notes were available to score."}],
            "notes": [],
        }

    dimension_scores = {
        key: mean(note["dimensions"][key] for note in notes)
        for key in notes[0]["dimensions"]
    }
    graph_metrics = _graph_metrics(paths, vault)
    overall = _weighted_score(dimension_scores)
    pass_rate = sum(1 for note in notes if note["passed"]) / len(notes)
    high_value_notes = [note for note in notes if note["top_dir"] in HIGH_VALUE_DIRS]
    failures = quality_failures(
        {
            "overall_score": overall,
            "dimension_scores": dimension_scores,
            "note_pass_rate": pass_rate,
            "high_value_notes": high_value_notes,
            "notes": notes,
            "graph_metrics": graph_metrics,
        },
        thresholds=thresholds,
    )
    return {
        "vault_path": str(vault),
        "note_count": len(notes),
        "overall_score": round(overall, 2),
        "dimension_scores": {key: round(value, 2) for key, value in dimension_scores.items()},
        "note_pass_rate": round(pass_rate, 3),
        "high_value_note_count": len(high_value_notes),
        "graph_metrics": graph_metrics,
        "passed": not failures,
        "failures": failures,
        "notes": notes,
    }


def quality_failures(score: dict[str, Any], *, thresholds: QualityThresholds | None = None) -> list[dict[str, Any]]:
    thresholds = thresholds or QualityThresholds()
    failures: list[dict[str, Any]] = []
    overall = float(score.get("overall_score", 0.0))
    if overall < thresholds.overall_score:
        failures.append({"check": "overall_score", "actual": round(overall, 2), "minimum": thresholds.overall_score})
    dimensions = dict(score.get("dimension_scores", {}))
    for name, value in dimensions.items():
        minimum = thresholds.dimension_floor
        if name == "source_clarity":
            minimum = thresholds.source_clarity
        if name == "content_substance":
            minimum = thresholds.content_substance
        if name == "report_substance":
            minimum = thresholds.report_substance
        if float(value) < minimum:
            failures.append({"check": f"dimension:{name}", "actual": round(float(value), 2), "minimum": minimum})
    pass_rate = float(score.get("note_pass_rate", 0.0))
    if pass_rate < thresholds.note_pass_rate:
        failures.append({"check": "note_pass_rate", "actual": round(pass_rate, 3), "minimum": thresholds.note_pass_rate})
    for note in score.get("notes", []):
        if note.get("debug_id_human_prose_hits"):
            failures.append({"check": "raw_debug_id_human_prose", "path": note.get("path"), "hits": note.get("debug_id_human_prose_hits")})
    for note in score.get("high_value_notes", []):
        if float(note.get("score", 0.0)) < thresholds.high_value_note_floor:
            failures.append({"check": "high_value_note_score", "path": note.get("path"), "actual": note.get("score"), "minimum": thresholds.high_value_note_floor})
    graph_metrics = dict(score.get("graph_metrics", {}))
    index_link_count = int(graph_metrics.get("index_link_count", 0))
    if index_link_count > thresholds.max_index_link_count:
        failures.append({"check": "graph:index_link_count", "actual": index_link_count, "maximum": thresholds.max_index_link_count})
    index_note_ratio = float(graph_metrics.get("index_link_note_ratio", 0.0))
    if index_note_ratio > thresholds.max_index_link_note_ratio:
        failures.append({"check": "graph:index_link_note_ratio", "actual": round(index_note_ratio, 3), "maximum": thresholds.max_index_link_note_ratio})
    generic_tag_share = float(graph_metrics.get("generic_tag_share", 0.0))
    if generic_tag_share > thresholds.max_generic_tag_share:
        failures.append({"check": "graph:generic_tag_share", "actual": round(generic_tag_share, 3), "maximum": thresholds.max_generic_tag_share})
    unresolved = int(graph_metrics.get("unresolved_link_count", 0))
    if unresolved > thresholds.max_unresolved_link_count:
        failures.append({"check": "graph:unresolved_link_count", "actual": unresolved, "maximum": thresholds.max_unresolved_link_count})
    connector_note_count = int(graph_metrics.get("connector_note_count", 0))
    valid_connector_ratio = float(graph_metrics.get("valid_connector_ratio", 1.0))
    if connector_note_count > 0 and valid_connector_ratio < thresholds.min_valid_connector_ratio:
        failures.append({"check": "graph:valid_connector_ratio", "actual": round(valid_connector_ratio, 3), "minimum": thresholds.min_valid_connector_ratio})
    information_note_count = int(graph_metrics.get("information_note_count", 0))
    info_connector_coverage = float(graph_metrics.get("info_connector_coverage", 1.0))
    if information_note_count > 0 and info_connector_coverage < thresholds.min_info_connector_coverage:
        failures.append({"check": "graph:info_connector_coverage", "actual": round(info_connector_coverage, 3), "minimum": thresholds.min_info_connector_coverage})
    info_pair_density = float(graph_metrics.get("info_pair_density", 1.0))
    if information_note_count > 1 and info_pair_density < thresholds.min_info_pair_density:
        failures.append({"check": "graph:info_pair_density", "actual": round(info_pair_density, 3), "minimum": thresholds.min_info_pair_density})
    return failures


GENERIC_GRAPH_TAGS = {
    "action",
    "applies_to",
    "connector",
    "contradicts",
    "data",
    "depends_on",
    "dikiwi",
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
    "page",
    "part_of",
    "pattern",
    "pdf",
    "pending",
    "proposal",
    "relates_to",
    "slide",
    "supports",
    "text",
    "tradeoff_with",
    "unclassified",
    "visual",
    "wisdom",
}


def _graph_metrics(paths: list[Path], vault_root: Path) -> dict[str, Any]:
    note_count = 0
    link_count = 0
    index_link_count = 0
    index_link_notes = 0
    target_counts: dict[str, int] = {}
    total_tags = 0
    generic_tags = 0
    notes_with_semantic_topics = 0
    notes: dict[str, dict[str, Any]] = {}
    alias_map: dict[str, set[str]] = {}

    for path in paths:
        if not path.exists() or path.suffix.lower() != ".md":
            continue
        note_count += 1
        rel = str(path.relative_to(vault_root)) if path.is_relative_to(vault_root) else str(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        title = _first_h1(body) or path.stem
        links = _body_wikilinks(body)
        rel_stem = str(Path(rel).with_suffix(""))
        aliases = {rel_stem, path.stem, title, *(_frontmatter_list(frontmatter, "aliases"))}
        notes[rel] = {
            "path": rel,
            "top_dir": rel.split("/", 1)[0],
            "frontmatter": frontmatter,
            "body": body,
            "links": links,
            "aliases": aliases,
        }
        for alias in aliases:
            alias_map.setdefault(_normalize_alias(alias), set()).add(rel)
            alias_map.setdefault(_normalize_basename_alias(alias), set()).add(rel)

        link_count += len(links)
        note_has_index = False
        for raw_link in links:
            target = _normalize_link_target(raw_link)
            target_counts[target] = target_counts.get(target, 0) + 1
            if target in {"00 zettelkasten index", "zettelkasten index"}:
                index_link_count += 1
                note_has_index = True
        if note_has_index:
            index_link_notes += 1

        tags = _frontmatter_list(frontmatter, "tags")
        semantic_topics = _frontmatter_list(frontmatter, "semantic_topics")
        if semantic_topics:
            notes_with_semantic_topics += 1
        for tag in tags:
            normalized = tag.strip().lower().replace(" ", "_")
            if not normalized:
                continue
            total_tags += 1
            if normalized in GENERIC_GRAPH_TAGS or normalized.startswith(("type:", "has:")):
                generic_tags += 1

    unresolved_links: list[dict[str, str]] = []
    resolved_links: dict[str, list[str]] = {}
    for note in notes.values():
        resolved: list[str] = []
        for raw_link in note["links"]:
            link_target = raw_link.split("|", 1)[0].split("#", 1)[0].strip()
            target = _normalize_alias(link_target) if "/" in link_target or "\\" in link_target else _normalize_basename_alias(link_target)
            matches = alias_map.get(target, set())
            if len(matches) == 1:
                resolved.append(next(iter(matches)))
            elif not _normalize_link_target(raw_link) in {"00 zettelkasten index", "zettelkasten index"}:
                unresolved_links.append({"source": note["path"], "target": raw_link})
        resolved_links[note["path"]] = resolved

    info_notes = {path for path, note in notes.items() if note["top_dir"] == "02-Information"}
    connector_notes = {path: note for path, note in notes.items() if note["top_dir"] == "03-Knowledge"}
    valid_connectors = 0
    info_pair_edges: set[tuple[str, str]] = set()
    info_connector_degree: dict[str, int] = {path: 0 for path in info_notes}
    for path, note in connector_notes.items():
        info_targets = sorted({target for target in resolved_links.get(path, []) if target in info_notes})
        has_relation = "relation:" in note["frontmatter"] or "connector_type:" in note["frontmatter"]
        has_meaning = "## Connector" in note["body"] and "## Relationship Meaning" in note["body"]
        if len(info_targets) >= 2 and has_relation and has_meaning:
            valid_connectors += 1
            pair = tuple(info_targets[:2])
            info_pair_edges.add(pair)
            for target in pair:
                info_connector_degree[target] = info_connector_degree.get(target, 0) + 1

    info_pair_possible = len(info_notes) * (len(info_notes) - 1) / 2
    # A complete graph is not a good Zettelkasten target. This ratio measures
    # useful connector abundance per information note instead of rewarding
    # indiscriminate all-to-all linking.
    info_pair_degree_ratio = len(info_pair_edges) / max(len(info_notes), 1)
    top_targets = sorted(target_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    return {
        "note_count": note_count,
        "wikilink_count": link_count,
        "index_link_count": index_link_count,
        "index_link_note_ratio": round(index_link_notes / max(note_count, 1), 3),
        "index_link_share": round(index_link_count / max(link_count, 1), 3),
        "top_targets": [{"target": target, "count": count} for target, count in top_targets],
        "top_target_share": round((top_targets[0][1] if top_targets else 0) / max(link_count, 1), 3),
        "total_tag_count": total_tags,
        "generic_tag_count": generic_tags,
        "generic_tag_share": round(generic_tags / max(total_tags, 1), 3),
        "semantic_topic_note_ratio": round(notes_with_semantic_topics / max(note_count, 1), 3),
        "unresolved_link_count": len(unresolved_links),
        "unresolved_links": unresolved_links[:20],
        "connector_note_count": len(connector_notes),
        "valid_connector_count": valid_connectors,
        "valid_connector_ratio": round(valid_connectors / max(len(connector_notes), 1), 3),
        "information_note_count": len(info_notes),
        "info_pair_count": len(info_pair_edges),
        "info_pair_density": round(info_pair_degree_ratio, 3),
        "info_pair_complete_graph_density": round(len(info_pair_edges) / max(info_pair_possible, 1), 3),
        "info_connector_coverage": round(
            sum(1 for degree in info_connector_degree.values() if degree >= 1) / max(len(info_notes), 1),
            3,
        ),
    }


def _is_quality_visible_note(path: Path, vault_root: Path) -> bool:
    try:
        rel = path.expanduser().resolve().relative_to(vault_root.expanduser().resolve()).as_posix()
    except ValueError:
        return True
    hidden_prefixes = (
        "00-Chaos/_assets/",
        "00-Chaos/canonical-markdown/",
        "99-MOC/",
        "99-System/",
    )
    if rel in {"00-Chaos/00 Zettelkasten Index.md"}:
        return False
    return not rel.startswith(hidden_prefixes)


def _normalize_link_target(raw_link: str) -> str:
    target = raw_link.split("|", 1)[0].split("#", 1)[0].strip()
    target = target.rsplit("/", 1)[-1]
    return target.replace("_", " ").replace("-", " ").lower()


def _normalize_alias(value: str) -> str:
    target = value.replace("\\", "/").strip()
    return target.replace("_", " ").replace("-", " ").strip().lower()


def _normalize_basename_alias(value: str) -> str:
    target = value.replace("\\", "/").rsplit("/", 1)[-1]
    return target.replace("_", " ").replace("-", " ").strip().lower()


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


def _weighted_score(dimensions: dict[str, float]) -> float:
    weights = {
        "human_readability": 20,
        "source_clarity": 16,
        "content_substance": 16,
        "link_context_quality": 15,
        "zettelkasten_connector_quality": 15,
        "report_substance": 12,
        "title_quality": 10,
        "debug_id_absence": 6,
    }
    total = sum(weights.values())
    return sum(float(dimensions.get(key, 0.0)) * weight for key, weight in weights.items()) / total


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end < 0:
        return "", text
    return text[4:end].strip(), text[end + 4 :].lstrip()


def _strip_non_prose(body: str) -> str:
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    body = re.sub(r"<details>.*?</details>", "", body, flags=re.S | re.I)
    body = re.sub(r"^## Technical Provenance.*?(?=^## |\Z)", "", body, flags=re.S | re.M)
    body = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", body)
    body = re.sub(r"\[\[([^\]]+)\]\]", r"\1", body)
    body = re.sub(r"`[^`]+`", "", body)
    return body


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", text)


def _score_human_readability(body: str, prose: str, words: list[str]) -> float:
    score = 100.0
    if len(words) < 80:
        score -= 20
    if len(words) < 40:
        score -= 20
    heading_count = len(re.findall(r"^##\s+", body, flags=re.M))
    if heading_count < 2:
        score -= 12
    jsonish_lines = sum(1 for line in prose.splitlines() if line.strip().startswith(("{", "}", '"')))
    if jsonish_lines > 3:
        score -= min(35, jsonish_lines * 4)
    placeholder_hits = sum(prose.lower().count(pattern) for pattern in PLACEHOLDER_PATTERNS)
    score -= min(40, placeholder_hits * 8)
    sentence_parts = re.split(r"(?:[.!?]\s+|\n+\s*[-*]\s+|\n{2,})", prose)
    sentence_lengths = [len(_words(sentence)) for sentence in sentence_parts if len(_words(sentence)) > 0]
    if sentence_lengths:
        awkward = sum(1 for count in sentence_lengths if count > 45)
        score -= min(20, awkward * 3)
    return max(0.0, score)


def _score_content_substance(frontmatter: str, body: str, prose: str, words: list[str], top_dir: str) -> float:
    """Score whether a note contains useful human content, not only structure."""
    score = 100.0
    word_count = len(words)
    minimums = {
        "00-Chaos": 60,
        "01-Data": 45,
        "02-Information": 70,
        "03-Knowledge": 90,
        "04-Insight": 130,
        "05-Wisdom": 130,
        "06-Impact": 160,
        "07-Research": 180,
        "08-Evaluations": 220,
        "09-Business-Plans": 500,
    }
    minimum = minimums.get(top_dir, 80)
    if word_count < minimum:
        score -= min(55, (minimum - word_count) / max(minimum, 1) * 55)

    placeholder_hits = sum(prose.lower().count(pattern) for pattern in PLACEHOLDER_PATTERNS)
    score -= min(45, placeholder_hits * 12)

    empty_sections = _empty_section_count(body)
    score -= min(35, empty_sections * 8)

    headings = len(re.findall(r"^#{1,4}\s+", body, flags=re.M))
    if headings >= 4 and word_count / max(headings, 1) < 18:
        score -= 18

    unique_words = {word.lower() for word in words if len(word) > 3}
    if word_count >= 40 and len(unique_words) / max(word_count, 1) < 0.22:
        score -= 12

    if top_dir in {"03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact"}:
        substance_markers = ("because", "therefore", "evidence", "means", "implies", "matters", "tradeoff", "risk", "constraint")
        if not any(marker in prose.lower() for marker in substance_markers):
            score -= 18

    if top_dir in REPORT_DIRS and not re.search(r"\b(recommend|risk|evidence|decision|next step|criteria|market|technical)\b", prose, re.I):
        score -= 22

    if top_dir == "00-Chaos" and "## Source Equivalent" in body:
        declared_pages = _frontmatter_int(frontmatter, "page_count")
        screenshots = _frontmatter_int(frontmatter, "screenshot_count")
        slide_sections = len(re.findall(r"^### Slide \d{3}\s*$", body, flags=re.M))
        if declared_pages and screenshots == declared_pages and slide_sections == declared_pages:
            score = max(score, 88.0)

    return max(0.0, score)


def _score_source_clarity(frontmatter: str, body: str, top_dir: str) -> float:
    score = 100.0
    source_markers = (
        "source_id",
        "source_ids",
        "source_paths",
        "grounded_in",
        "based_on",
        "source_file",
        "source_sha256",
        "Source Equivalent",
        "Source Trace",
        "Source Knowledge",
        "Source Knowledge Lineage",
    )
    if not any(marker in frontmatter or marker in body for marker in source_markers):
        score -= 45
    if top_dir in REPORT_DIRS and "Source Knowledge Lineage" not in body and "Internal Evidence" not in body and "Based On" not in body:
        score -= 30
    if "/Users/" in body:
        score -= 10
    if re.search(r"\b[a-f0-9]{64}\b", body, flags=re.I) and "Source Knowledge Lineage" not in body:
        score -= 10
    return max(0.0, score)


def _score_link_context(body: str, prose: str, links: list[str], top_dir: str) -> float:
    score = 100.0
    if top_dir not in ALLOWED_ORPHAN_DIRS and not links:
        score -= 35
    if links and not re.search(r"why|because|therefore|enables|depends|constrains|resolves|meaning|matters|grounded", prose, re.I):
        score -= 20
    raw_display = sum(1 for link in links if re.search(r"\b(data|information|knowledge|insight|wisdom|impact)_[a-f0-9]{6,}\b", link, re.I) and "|" not in link)
    score -= min(30, raw_display * 10)
    return max(0.0, score)


def _score_connector_quality(frontmatter: str, body: str, top_dir: str, links: list[str]) -> float:
    score = 100.0
    if top_dir in {"03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact"}:
        if not any(section in body for section in ("## Connector", "## Relationship Meaning", "## Why This Matters", "## Grounded In", "## Based On")):
            score -= 35
        if not links:
            score -= 25
    if top_dir in {"08-Evaluations", "09-Business-Plans"} and not any(marker in body for marker in ("## Evidence", "## Source Knowledge Lineage", "## Findings")):
        score -= 25
    if "dikiwi_id" not in frontmatter and top_dir in {"01-Data", "02-Information", "03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact"}:
        score -= 10
    return max(0.0, score)


def _score_report_substance(body: str, prose: str, words: list[str], top_dir: str) -> float:
    if top_dir not in REPORT_DIRS:
        return 100.0
    score = 100.0
    minimum = 500 if top_dir == "09-Business-Plans" else 180
    if len(words) < minimum:
        score -= min(45, (minimum - len(words)) / max(minimum, 1) * 45)
    required = ("Risk", "Recommendation", "Evidence")
    missing = sum(1 for marker in required if marker.lower() not in body.lower())
    score -= missing * 12
    placeholder_hits = sum(prose.lower().count(pattern) for pattern in PLACEHOLDER_PATTERNS)
    score -= min(35, placeholder_hits * 10)
    return max(0.0, score)


def _score_title(title: str, filename: str, top_dir: str) -> float:
    score = 100.0
    cleaned = " ".join(title.split())
    if len(cleaned) < 12 or len(cleaned) > 110:
        score -= 25
    word_count = len(_words(cleaned))
    if word_count < 3 or word_count > 16:
        score -= 20
    if re.search(r"\b(data|information|knowledge|insight|wisdom|impact)_[a-f0-9]{6,}\b", cleaned, re.I):
        score -= 40
    if re.search(r"\bE\d+\b|link_[a-f0-9]|info_[a-f0-9]", cleaned):
        score -= 30
    if re.search(r"\b(and|as|between|by|for|from|in|of|or|to|with)\s+(depends on|enables|is in tension with|applies to|relates to|supports|challenges)\b", cleaned, re.I):
        score -= 25
    if cleaned.lower() in {"untitled", "source data", "data chunk"}:
        score -= 50
    if top_dir in HIGH_VALUE_DIRS and len(filename) > 180:
        score -= 10
    return max(0.0, score)


def _empty_section_count(body: str) -> int:
    matches = list(re.finditer(r"^##+\s+.+$", body, flags=re.M))
    empty = 0
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section = _strip_non_prose(body[start:end])
        if len(_words(section)) < 8 and not re.search(r"!\[\[|\[\[", body[start:end]):
            empty += 1
    return empty


def _frontmatter_int(frontmatter: str, key: str) -> int:
    match = re.search(rf"^\s{{0,4}}{re.escape(key)}:\s*['\"]?(\d+)['\"]?\s*$", frontmatter, flags=re.M)
    if not match:
        return 0
    return int(match.group(1))


def _body_wikilinks(body: str) -> list[str]:
    links: list[str] = []
    searchable = re.sub(r"`[^`]*`", "", body)
    searchable = re.sub(r"```.*?```", "", searchable, flags=re.S)
    for match in re.finditer(r"(!)?\[\[([^\]]+)\]\]", searchable):
        raw_link = match.group(2)
        if match.group(1) == "!" and _is_asset_link(raw_link):
            continue
        links.append(raw_link)
    return links


def _is_asset_link(raw_link: str) -> bool:
    target = raw_link.split("|", 1)[0].split("#", 1)[0].strip().lower()
    return target.startswith("00-chaos/_assets/") or target.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _score_debug_absence(prose: str) -> float:
    return 0.0 if _debug_hits(prose) else 100.0


def _debug_hits(prose: str) -> list[str]:
    hits: list[str] = []
    for pattern in DEBUG_PATTERNS:
        for match in pattern.findall(prose):
            value = match if isinstance(match, str) else " ".join(match)
            if value not in hits:
                hits.append(value)
    return hits[:20]


def _note_failures(dimensions: dict[str, float], score: float, debug_hits: list[str], top_dir: str) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if score < 75:
        failures.append({"check": "note_score", "actual": round(score, 2), "minimum": 75})
    if dimensions.get("content_substance", 100.0) < 78:
        failures.append({"check": "content_substance", "actual": round(dimensions.get("content_substance", 0.0), 2), "minimum": 78})
    if top_dir in HIGH_VALUE_DIRS and score < 80:
        failures.append({"check": "high_value_note_score", "actual": round(score, 2), "minimum": 80})
    if top_dir in REPORT_DIRS:
        if dimensions.get("source_clarity", 100.0) < 80:
            failures.append({"check": "report_source_clarity", "actual": round(dimensions.get("source_clarity", 0.0), 2), "minimum": 80})
        if dimensions.get("link_context_quality", 100.0) < 75:
            failures.append({"check": "report_link_context_quality", "actual": round(dimensions.get("link_context_quality", 0.0), 2), "minimum": 75})
        if dimensions.get("report_substance", 100.0) < 80:
            failures.append({"check": "report_substance", "actual": round(dimensions.get("report_substance", 0.0), 2), "minimum": 80})
    if top_dir in HIGH_VALUE_DIRS.union({"03-Knowledge"}) and dimensions.get("title_quality", 100.0) < 80:
        failures.append({"check": "title_quality", "actual": round(dimensions.get("title_quality", 0.0), 2), "minimum": 80})
    if debug_hits:
        failures.append({"check": "raw_debug_id_human_prose", "hits": debug_hits})
    return failures
