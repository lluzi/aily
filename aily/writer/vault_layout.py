"""Aily V1 Obsidian vault layout helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


V1_VAULT_DIRECTORIES: tuple[str, ...] = (
    "00-Chaos",
    "00-Chaos/_assets",
    "00-Chaos/sources",
    "00-Chaos/canonical-markdown",
    "01-Data",
    "02-Information",
    "03-Knowledge",
    "04-Insight",
    "05-Wisdom",
    "06-Impact",
    "07-Research",
    "07-Research/Second-Opinions",
    "08-Evaluations",
    "09-Business-Plans",
    "10-Dossiers",
    "99-MOC",
    "99-System",
)


LEGACY_COMPATIBILITY_DIRECTORIES: tuple[str, ...] = (
    "07-Proposal",
    "08-Entrepreneurship",
)


def inspect_v1_vault_layout(vault_path: Path) -> dict[str, Any]:
    vault = vault_path.expanduser().resolve()
    required = {path: (vault / path).is_dir() for path in V1_VAULT_DIRECTORIES}
    legacy = {path: (vault / path).is_dir() for path in LEGACY_COMPATIBILITY_DIRECTORIES}
    return {
        "vault_path": str(vault),
        "exists": vault.exists(),
        "required_directories": required,
        "missing_required_directories": [path for path, exists in required.items() if not exists],
        "legacy_compatibility_directories": legacy,
        "missing_legacy_compatibility_directories": [path for path, exists in legacy.items() if not exists],
    }


def ensure_v1_vault_layout(
    vault_path: Path,
    *,
    include_legacy_compatibility: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    vault = vault_path.expanduser().resolve()
    directories = list(V1_VAULT_DIRECTORIES)
    if include_legacy_compatibility:
        directories.extend(LEGACY_COMPATIBILITY_DIRECTORIES)

    created: list[str] = []
    existing: list[str] = []
    for relative_path in directories:
        target = vault / relative_path
        if target.is_dir():
            existing.append(relative_path)
            continue
        created.append(relative_path)
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)

    graph_hygiene = None if dry_run else ensure_obsidian_graph_hygiene(vault)

    return {
        "vault_path": str(vault),
        "dry_run": dry_run,
        "include_legacy_compatibility": include_legacy_compatibility,
        "created_directories": created,
        "existing_directories": existing,
        "graph_hygiene": graph_hygiene,
        "layout_after": inspect_v1_vault_layout(vault) if not dry_run else None,
    }


def ensure_obsidian_graph_hygiene(vault_path: Path) -> dict[str, Any]:
    """Hide technical and review-query directories from Obsidian's visible graph."""
    vault = vault_path.expanduser().resolve()
    obsidian_dir = vault / ".obsidian"
    obsidian_dir.mkdir(parents=True, exist_ok=True)

    ignored = [
        "00-Chaos/canonical-markdown/",
        "00-Chaos/_assets/",
        "99-MOC/",
        "99-System/",
    ]
    app_path = obsidian_dir / "app.json"
    app_data = _read_json_object(app_path)
    filters = app_data.get("userIgnoreFilters")
    if not isinstance(filters, list):
        filters = []
    for item in ignored:
        if item not in filters:
            filters.append(item)
    app_data["userIgnoreFilters"] = filters
    app_path.write_text(json.dumps(app_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    graph_path = obsidian_dir / "graph.json"
    graph_data = _read_json_object(graph_path)
    search = str(graph_data.get("search") or "").strip()
    exclusions = ['-path:"00-Chaos/canonical-markdown"', '-path:"00-Chaos/_assets"', '-path:"99-MOC"', '-path:"99-System"']
    for exclusion in exclusions:
        if exclusion not in search:
            search = f"{search} {exclusion}".strip()
    graph_data["search"] = search
    graph_data["showTags"] = False
    graph_data["showAttachments"] = False
    graph_path.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "app_json": str(app_path),
        "graph_json": str(graph_path),
        "ignored_filters": ignored,
        "graph_search": search,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_artifact_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value.strip())
    return safe.strip("-")[:180] or "source"


def canonical_markdown_vault_path(
    vault_path: Path,
    *,
    source_id: str,
    markdown_sha256: str,
) -> Path:
    digest = markdown_sha256.strip()[:16] or "unknown"
    return vault_path.expanduser().resolve() / "00-Chaos" / "canonical-markdown" / f"{_safe_artifact_name(source_id)}-{digest}.md"


def write_canonical_markdown_vault_artifact(
    vault_path: Path,
    *,
    source_id: str,
    package_id: str,
    markdown_sha256: str,
    title: str,
    source_type: str,
    markdown: str,
    source_url: str = "",
    origin_path: str = "",
    storage_path: str = "",
) -> dict[str, Any]:
    """Project canonical source Markdown into the V1 Obsidian source namespace."""
    ensure_v1_vault_layout(vault_path, include_legacy_compatibility=False)
    target = canonical_markdown_vault_path(vault_path, source_id=source_id, markdown_sha256=markdown_sha256)
    body = (
        "---\n"
        "origin_creator: application\n"
        "origin_generation_method: SourceFoundationGraph canonical markdown projection\n"
        "origin_evidence_class: product-artifact\n"
        "origin_modified_by_lead_agent: false\n"
        f"source_id: {source_id!r}\n"
        f"canonical_markdown_package_id: {package_id!r}\n"
        f"canonical_markdown_sha256: {markdown_sha256!r}\n"
        f"source_type: {source_type!r}\n"
        f"source_url: {source_url!r}\n"
        f"origin_path: {origin_path!r}\n"
        f"storage_path: {storage_path!r}\n"
        "---\n\n"
        f"# {title or source_id}\n\n"
        f"{markdown.strip()}\n"
    )
    previous = target.read_text(encoding="utf-8") if target.exists() else None
    changed = previous != body
    if changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return {
        "path": str(target),
        "relative_path": str(target.relative_to(vault_path.expanduser().resolve())),
        "created": previous is None,
        "changed": changed,
        "source_id": source_id,
        "package_id": package_id,
        "markdown_sha256": markdown_sha256,
    }
