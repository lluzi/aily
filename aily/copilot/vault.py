"""Vault search and note-reading primitives for Aily-Copilot."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EXCLUDED_PREFIXES = (
    ".obsidian/",
    "99-MOC/",
    "99-System/",
)


@dataclass(frozen=True)
class VaultNote:
    path: Path
    relative_path: str
    title: str
    body: str
    frontmatter: dict[str, Any]
    tags: list[str]
    wikilinks: list[str]
    sha256: str
    modified_at: str


class VaultSearchService:
    """Deterministic lexical retrieval over an Obsidian Markdown vault."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path.expanduser().resolve()

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        include_dirs: list[str] | None = None,
        exclude_dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_query = str(query or "").strip()
        terms = _tokenize(clean_query)
        include_prefixes = tuple(_clean_prefix(item) for item in include_dirs or [] if _clean_prefix(item))
        exclude_prefixes = tuple(_clean_prefix(item) for item in exclude_dirs or [] if _clean_prefix(item))
        scored: list[tuple[float, VaultNote, dict[str, Any]]] = []

        for note in self.iter_notes(include_prefixes=include_prefixes, exclude_prefixes=exclude_prefixes):
            score, reasons = _score_note(note, clean_query, terms)
            if score <= 0:
                continue
            scored.append((score, note, reasons))

        scored.sort(key=lambda item: (-item[0], item[1].relative_path.lower()))
        results = [
            _note_search_payload(
                note,
                score=round(score, 4),
                rank=index,
                reasons=reasons,
                query=clean_query,
                terms=terms,
            )
            for index, (score, note, reasons) in enumerate(scored[: max(1, min(limit, 50))], 1)
        ]
        return {
            "vault_path": str(self.vault_path),
            "query": clean_query,
            "terms": terms,
            "total": len(scored),
            "returned": len(results),
            "results": results,
        }

    def read_note(self, relative_path: str, *, chunk_index: int = 0, chunk_lines: int = 180) -> dict[str, Any]:
        note_path = self.resolve_note_path(relative_path)
        note = self.load_note(note_path)
        lines = note.body.splitlines()
        safe_chunk_lines = max(25, min(int(chunk_lines), 500))
        total_chunks = max(1, (len(lines) + safe_chunk_lines - 1) // safe_chunk_lines)
        safe_chunk_index = max(0, min(int(chunk_index), total_chunks - 1))
        start = safe_chunk_index * safe_chunk_lines
        end = start + safe_chunk_lines
        return {
            "vault_path": str(self.vault_path),
            "relative_path": note.relative_path,
            "title": note.title,
            "frontmatter": note.frontmatter,
            "tags": note.tags,
            "wikilinks": note.wikilinks,
            "backlinks": self.backlinks_for(note),
            "sha256": note.sha256,
            "modified_at": note.modified_at,
            "chunk_index": safe_chunk_index,
            "chunk_lines": safe_chunk_lines,
            "total_chunks": total_chunks,
            "content": "\n".join(lines[start:end]),
        }

    def neighborhood(self, relative_path: str, *, limit: int = 20) -> dict[str, Any]:
        note_path = self.resolve_note_path(relative_path)
        note = self.load_note(note_path)
        outgoing = [
            {"target": link, "relationship": "wikilink_out", "reason": f"{note.title} links to {link}."}
            for link in note.wikilinks[:limit]
        ]
        backlinks = [
            {"source": item["relative_path"], "relationship": "backlink", "reason": item["title"] + " links here."}
            for item in self.backlinks_for(note)[:limit]
        ]
        shared_tags: list[dict[str, Any]] = []
        note_tags = set(note.tags)
        if note_tags:
            for other in self.iter_notes():
                if other.relative_path == note.relative_path:
                    continue
                overlap = sorted(note_tags.intersection(other.tags))
                if overlap:
                    shared_tags.append(
                        {
                            "source": other.relative_path,
                            "relationship": "shared_tags",
                            "tags": overlap,
                            "reason": f"Shares tag(s): {', '.join(overlap)}.",
                        }
                    )
                if len(shared_tags) >= limit:
                    break
        return {
            "relative_path": note.relative_path,
            "title": note.title,
            "outgoing": outgoing,
            "backlinks": backlinks,
            "shared_tags": shared_tags,
        }

    def relevant_notes(
        self,
        *,
        query: str = "",
        seed_paths: list[str] | None = None,
        include_dirs: list[str] | None = None,
        exclude_dirs: list[str] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        """Return content-based note recommendations with relationship reasons."""
        seeds: list[VaultNote] = []
        for seed_path in seed_paths or []:
            try:
                seeds.append(self.load_note(self.resolve_note_path(seed_path)))
            except (FileNotFoundError, ValueError):
                continue
        seed_terms = set(_tokenize(query))
        seed_tags: set[str] = set()
        seed_links: set[str] = set()
        seed_titles: set[str] = set()
        for seed in seeds:
            seed_terms.update(_tokenize(seed.title))
            seed_terms.update(_tokenize(_excerpt_for_terms(seed.body, list(seed_terms) or [seed.title], radius=900)))
            seed_tags.update(seed.tags)
            seed_links.update(_normalize_link(link) for link in seed.wikilinks)
            seed_titles.add(_normalize_link(seed.title))
            seed_titles.add(_normalize_link(seed.path.stem))

        include_prefixes = tuple(_clean_prefix(item) for item in include_dirs or [] if _clean_prefix(item))
        exclude_prefixes = tuple(_clean_prefix(item) for item in exclude_dirs or [] if _clean_prefix(item))
        seed_paths_normalized = {seed.relative_path for seed in seeds}
        scored: list[tuple[float, VaultNote, list[dict[str, Any]]]] = []
        for note in self.iter_notes(include_prefixes=include_prefixes, exclude_prefixes=exclude_prefixes):
            if note.relative_path in seed_paths_normalized:
                continue
            score, reasons = _relevance_score(note, seed_terms=seed_terms, seed_tags=seed_tags, seed_links=seed_links, seed_titles=seed_titles)
            if score > 0 and reasons:
                scored.append((score, note, reasons))
        scored.sort(key=lambda item: (-item[0], item[1].relative_path.lower()))
        recommendations = [
            {
                "rank": index,
                "relative_path": note.relative_path,
                "title": note.title,
                "score": round(score, 4),
                "relationship_explanations": reasons,
                "excerpt": _excerpt_for_terms(note.body, sorted(seed_terms)[:8] or [query]),
                "tags": note.tags,
                "wikilinks": note.wikilinks[:20],
                "sha256": note.sha256,
            }
            for index, (score, note, reasons) in enumerate(scored[: max(1, min(limit, 50))], 1)
        ]
        return {
            "vault_path": str(self.vault_path),
            "query": query,
            "seed_paths": [seed.relative_path for seed in seeds],
            "seed_terms": sorted(seed_terms)[:80],
            "returned": len(recommendations),
            "recommendations": recommendations,
        }

    def iter_notes(
        self,
        *,
        include_prefixes: tuple[str, ...] = (),
        exclude_prefixes: tuple[str, ...] = (),
    ) -> list[VaultNote]:
        if not self.vault_path.exists():
            return []
        excluded = (*DEFAULT_EXCLUDED_PREFIXES, *exclude_prefixes)
        notes: list[VaultNote] = []
        for path in sorted(self.vault_path.rglob("*.md")):
            rel = path.relative_to(self.vault_path).as_posix()
            if _is_excluded(rel, excluded):
                continue
            if include_prefixes and not any(rel.startswith(prefix) for prefix in include_prefixes):
                continue
            notes.append(self.load_note(path))
        return notes

    def resolve_note_path(self, relative_path: str) -> Path:
        raw = str(relative_path or "").strip().lstrip("/")
        if not raw:
            raise ValueError("note path is required")
        if ".." in Path(raw).parts:
            raise ValueError("note path must not contain parent directory segments")

        candidates = [self.vault_path / raw]
        if not raw.endswith(".md"):
            candidates.append(self.vault_path / f"{raw}.md")
        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            _ensure_inside_vault(self.vault_path, resolved)
            if resolved.is_file() and resolved.suffix.lower() == ".md":
                return resolved

        if "/" not in raw:
            normalized = _slugish(Path(raw).stem)
            matches = [
                path
                for path in self.vault_path.rglob("*.md")
                if _slugish(path.stem) == normalized
                and not _is_excluded(path.relative_to(self.vault_path).as_posix(), DEFAULT_EXCLUDED_PREFIXES)
            ]
            if len(matches) == 1:
                return matches[0].resolve()
        raise FileNotFoundError(f"Vault note not found: {relative_path}")

    def load_note(self, path: Path) -> VaultNote:
        resolved = path.expanduser().resolve()
        _ensure_inside_vault(self.vault_path, resolved)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        rel = resolved.relative_to(self.vault_path).as_posix()
        return VaultNote(
            path=resolved,
            relative_path=rel,
            title=_first_heading(body) or resolved.stem.replace("_", " "),
            body=body,
            frontmatter=frontmatter,
            tags=_extract_tags(text, frontmatter),
            wikilinks=_extract_wikilinks(text),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            modified_at=datetime.fromtimestamp(resolved.stat().st_mtime, timezone.utc).isoformat(),
        )

    def backlinks_for(self, target: VaultNote) -> list[dict[str, Any]]:
        target_stem = target.path.stem
        target_rel_no_suffix = target.relative_path[:-3] if target.relative_path.endswith(".md") else target.relative_path
        backlinks: list[dict[str, Any]] = []
        for note in self.iter_notes():
            if note.relative_path == target.relative_path:
                continue
            normalized_links = {_normalize_link(link) for link in note.wikilinks}
            if _normalize_link(target_stem) in normalized_links or _normalize_link(target_rel_no_suffix) in normalized_links:
                backlinks.append(
                    {
                        "relative_path": note.relative_path,
                        "title": note.title,
                        "excerpt": _excerpt_for_terms(note.body, [target_stem]),
                    }
                )
        return backlinks


def _note_search_payload(
    note: VaultNote,
    *,
    score: float,
    rank: int,
    reasons: dict[str, Any],
    query: str,
    terms: list[str],
) -> dict[str, Any]:
    return {
        "citation_id": f"V{rank:03d}",
        "relative_path": note.relative_path,
        "title": note.title,
        "top_dir": note.relative_path.split("/", 1)[0],
        "score": score,
        "match_reasons": reasons,
        "excerpt": _excerpt_for_terms(note.body, terms or [query]),
        "tags": note.tags,
        "wikilinks": note.wikilinks[:20],
        "sha256": note.sha256,
        "modified_at": note.modified_at,
    }


def _score_note(note: VaultNote, query: str, terms: list[str]) -> tuple[float, dict[str, Any]]:
    if not query and not terms:
        return 0.0, {}
    title_text = note.title.lower()
    path_text = note.relative_path.lower()
    body_text = note.body.lower()
    tag_text = " ".join(note.tags).lower()
    query_lower = query.lower()

    reasons: dict[str, Any] = {
        "title_matches": [],
        "path_matches": [],
        "tag_matches": [],
        "body_term_hits": {},
        "exact_phrase": False,
    }
    score = 0.0

    if query_lower and query_lower in title_text:
        score += 12.0
        reasons["exact_phrase"] = True
        reasons["title_matches"].append(query)
    if query_lower and query_lower in body_text:
        score += 4.0
        reasons["exact_phrase"] = True

    for term in terms:
        term_lower = term.lower()
        if not term_lower:
            continue
        if term_lower in title_text:
            score += 8.0
            reasons["title_matches"].append(term)
        if term_lower in path_text:
            score += 4.0
            reasons["path_matches"].append(term)
        if term_lower in tag_text:
            score += 5.0
            reasons["tag_matches"].append(term)
        count = body_text.count(term_lower)
        if count:
            score += min(10.0, 1.2 * count)
            reasons["body_term_hits"][term] = count

    link_boost = min(2.0, len(note.wikilinks) * 0.05)
    score += link_boost
    reasons["link_boost"] = round(link_boost, 4)
    compact_reasons = {key: value for key, value in reasons.items() if value not in ({}, [], False, 0)}
    return score, compact_reasons


def _relevance_score(
    note: VaultNote,
    *,
    seed_terms: set[str],
    seed_tags: set[str],
    seed_links: set[str],
    seed_titles: set[str],
) -> tuple[float, list[dict[str, Any]]]:
    title_text = note.title.lower()
    body_text = note.body.lower()
    path_text = note.relative_path.lower()
    note_tags = set(note.tags)
    normalized_links = {_normalize_link(link) for link in note.wikilinks}
    reasons: list[dict[str, Any]] = []
    score = 0.0

    matched_terms = sorted(
        term for term in seed_terms if len(term) > 2 and (term.lower() in title_text or term.lower() in body_text or term.lower() in path_text)
    )[:12]
    if matched_terms:
        score += min(14.0, len(matched_terms) * 1.4)
        reasons.append(
            {
                "relationship": "shared_content_terms",
                "evidence": matched_terms,
                "explanation": "Shares substantive terms with the question or seed notes.",
            }
        )

    matched_tags = sorted(seed_tags.intersection(note_tags))
    if matched_tags:
        score += min(8.0, len(matched_tags) * 2.0)
        reasons.append(
            {
                "relationship": "shared_tags",
                "evidence": matched_tags,
                "explanation": "Uses overlapping semantic tags.",
            }
        )

    if normalized_links.intersection(seed_titles):
        linked = sorted(normalized_links.intersection(seed_titles))
        score += 9.0
        reasons.append(
            {
                "relationship": "links_to_seed",
                "evidence": linked,
                "explanation": "Directly links to one of the selected seed notes.",
            }
        )

    if seed_links.intersection({_normalize_link(note.path.stem), _normalize_link(note.title)}):
        linked_by_seed = sorted(seed_links.intersection({_normalize_link(note.path.stem), _normalize_link(note.title)}))
        score += 9.0
        reasons.append(
            {
                "relationship": "linked_from_seed",
                "evidence": linked_by_seed,
                "explanation": "A selected seed note directly links to this note.",
            }
        )

    link_count = len(normalized_links)
    if link_count:
        score += min(2.0, link_count * 0.08)
    return score, reasons


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    frontmatter_text = text[4:end]
    body = text[end + 4 :].lstrip()
    data: dict[str, Any] = {}
    current_key = ""
    for line in frontmatter_text.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "-")) and current_key:
            value = line.strip().lstrip("-").strip()
            if value:
                existing = data.setdefault(current_key, [])
                if isinstance(existing, list):
                    existing.append(value)
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current_key = key.strip()
            data[current_key] = _parse_frontmatter_value(value.strip())
    return data, body


def _parse_frontmatter_value(value: str) -> Any:
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    return value.strip("'\"")


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_tags(text: str, frontmatter: dict[str, Any]) -> list[str]:
    tags: set[str] = set()
    raw_tags = frontmatter.get("tags") or frontmatter.get("semantic_topics") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            clean = str(tag).strip().lstrip("#")
            if clean:
                tags.add(clean)
    for match in re.findall(r"(?<!\w)#([A-Za-z0-9_/-]+)", text):
        tags.add(match)
    return sorted(tags)


def _extract_wikilinks(text: str) -> list[str]:
    links: set[str] = set()
    for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
        target = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            links.add(target)
    return sorted(links)


def _tokenize(query: str) -> list[str]:
    terms = re.findall(r"#[\w/-]+|[A-Za-z0-9][A-Za-z0-9_./-]{1,}|[\u4e00-\u9fff]{2,}", query)
    stop = {"what", "where", "when", "how", "the", "and", "for", "with", "about", "this", "that"}
    return [term for term in terms if term.lower() not in stop]


def _excerpt_for_terms(text: str, terms: list[str], *, radius: int = 220) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return ""
    lowered = clean.lower()
    positions = [lowered.find(term.lower()) for term in terms if term and lowered.find(term.lower()) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - radius)
    end = min(len(clean), center + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(clean) else ""
    return f"{prefix}{clean[start:end].strip()}{suffix}"


def _clean_prefix(value: str) -> str:
    clean = value.strip().strip("/")
    return f"{clean}/" if clean else ""


def _is_excluded(relative_path: str, prefixes: tuple[str, ...]) -> bool:
    return any(relative_path == prefix.rstrip("/") or relative_path.startswith(prefix) for prefix in prefixes)


def _ensure_inside_vault(vault: Path, path: Path) -> None:
    try:
        path.relative_to(vault)
    except ValueError as exc:
        raise ValueError(f"Path must stay inside vault: {path}") from exc


def _normalize_link(value: str) -> str:
    return value.strip().removesuffix(".md").lower()


def _slugish(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
