"""ObsidianCLI helper - filesystem-based vault reader.

Replaces obsidian-cli subprocess calls with direct filesystem operations
for vault search, note reading, tag listing, and backlink extraction.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ObsidianCLI:
    """Filesystem-based vault inspector.

    Reads vault markdown files directly from disk, parsing frontmatter
    and content without requiring obsidian-cli or the Obsidian app.
    """

    def __init__(self, cli_path: str | None = None, vault_name: str | None = None, vault_path: str | Path | None = None) -> None:
        self._cli_path = cli_path or "obsidian-cli"
        self._vault_name = vault_name
        self._vault_path = Path(vault_path) if vault_path else None

    def _resolve_vault_path(self) -> Path | None:
        """Resolve vault path from explicit setting or vault name heuristic."""
        if self._vault_path:
            return self._vault_path
        if self._vault_name:
            # Common locations
            candidates = [
                Path.home() / "obsidian" / self._vault_name,
                Path.home() / "Documents" / "Obsidian Vault" / self._vault_name,
                Path.home() / "Documents" / self._vault_name,
                Path.home() / self._vault_name,
            ]
            for c in candidates:
                if c.exists():
                    return c
        return None

    def _all_markdown_files(self) -> list[Path]:
        """Yield all .md files in the vault recursively."""
        vault = self._resolve_vault_path()
        if not vault or not vault.exists():
            return []
        return sorted(vault.rglob("*.md"))

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter from markdown content."""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml
                    fm = yaml.safe_load(parts[1]) or {}
                    return (fm, parts[2].strip())
                except Exception as exc:
                    logger.warning("Failed to parse YAML frontmatter: %s", exc)
        return ({}, content)

    @staticmethod
    def _sanitize_path(path: str) -> str:
        """Validate and normalize a vault path."""
        if not path or not isinstance(path, str):
            raise ValueError("Path must be a non-empty string")
        if "\x00" in path:
            raise ValueError("Path contains null bytes")
        if ".." in path:
            raise ValueError("Path contains directory traversal")
        if path.startswith("/"):
            # Allow absolute paths if they resolve inside the vault
            pass
        if "\\" in path:
            raise ValueError("Path contains backslashes")
        return path

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search vault notes matching *query* by scanning markdown files."""
        files = self._all_markdown_files()
        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        for md_path in files:
            if len(results) >= limit:
                break
            try:
                content = md_path.read_text(encoding="utf-8")
                frontmatter, body = self._parse_frontmatter(content)

                # Frontmatter key search (e.g. "dikiwi_level:")
                matched = False
                if query.endswith(":"):
                    key = query.rstrip(":")
                    if frontmatter.get(key) is not None:
                        matched = True
                elif query_lower in content.lower():
                    matched = True

                if matched:
                    rel = md_path.relative_to(self._resolve_vault_path())
                    results.append({"path": str(rel), "label": rel.stem})
            except Exception as exc:
                logger.debug("[ObsidianCLI] Failed to read %s: %s", md_path, exc)

        return results

    def read_note(self, path: str) -> str:
        """Read a note by vault path directly from disk."""
        try:
            self._sanitize_path(path)
        except ValueError as exc:
            logger.warning("[ObsidianCLI] Invalid path: %s", exc)
            return ""

        vault = self._resolve_vault_path()
        if not vault:
            logger.warning("[ObsidianCLI] No vault path available for reading")
            return ""

        file_path = vault / path
        # Security: ensure the resolved path stays inside the vault
        try:
            file_path.resolve().relative_to(vault.resolve())
        except ValueError:
            logger.warning("[ObsidianCLI] Path escapes vault: %s", path)
            return ""

        if not file_path.exists():
            return ""

        try:
            return file_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("[ObsidianCLI] Failed to read %s: %s", file_path, exc)
            return ""

    def eval_javascript(self, script: str) -> Any:
        """JavaScript evaluation is not supported in filesystem mode."""
        logger.debug("[ObsidianCLI] eval_javascript is not available without Obsidian runtime")
        return None

    def list_tags(self) -> list[str]:
        """List all tags in the vault by scanning markdown files."""
        files = self._all_markdown_files()
        tags: set[str] = set()
        inline_tag_pattern = re.compile(r"#([a-zA-Z0-9_\-\/]+)")

        for md_path in files:
            try:
                content = md_path.read_text(encoding="utf-8")
                frontmatter, body = self._parse_frontmatter(content)

                # Frontmatter tags
                fm_tags = frontmatter.get("tags", [])
                if isinstance(fm_tags, str):
                    fm_tags = [fm_tags]
                for t in fm_tags:
                    tags.add(str(t).strip("#"))

                # Inline tags
                for match in inline_tag_pattern.findall(body):
                    tags.add(match)

            except Exception as exc:
                logger.debug("[ObsidianCLI] Failed to scan tags in %s: %s", md_path, exc)

        return sorted(tags)

    def search_by_frontmatter(self, key: str, value: str) -> list[dict[str, Any]]:
        """Find all notes where frontmatter[key] contains value.

        For list values (e.g. grounded_in), checks if value is in any item.
        For scalar values, does exact or substring match.

        Returns list of {path, frontmatter, dikiwi_id, stage} dicts.
        Used for staleness detection: find higher-stage notes that depend
        on a changed information node.
        """
        files = self._all_markdown_files()
        results: list[dict[str, Any]] = []
        for md_path in files:
            try:
                content = md_path.read_text(encoding="utf-8")
                fm, _ = self._parse_frontmatter(content)
                fm_value = fm.get(key)
                if fm_value is None:
                    continue
                matched = False
                if isinstance(fm_value, list):
                    if any(value in str(item) for item in fm_value):
                        matched = True
                elif value in str(fm_value):
                    matched = True
                if matched:
                    results.append({
                        "path": str(md_path),
                        "frontmatter": fm,
                        "dikiwi_id": fm.get("dikiwi_id", ""),
                        "stage": fm.get("dikiwi_level", fm.get("type", "")),
                    })
            except Exception as exc:
                logger.debug("[ObsidianCLI] Failed to read %s during frontmatter search: %s", md_path, exc)
        return results

    def get_backlinks(self, path: str) -> list[str]:
        """Get backlink paths for a given note by scanning all markdown files."""
        try:
            self._sanitize_path(path)
        except ValueError as exc:
            logger.warning("[ObsidianCLI] Invalid path: %s", exc)
            return []

        vault = self._resolve_vault_path()
        if not vault:
            return []

        target_name = Path(path).stem
        # Match [[target_name]], [[target_name|alias]], [text](target_name.md)
        wiki_pattern = re.compile(r"\[\[" + re.escape(target_name) + r"(?:\|[^\]]+)?\]\]")
        md_link_pattern = re.compile(r"\[([^\]]*)\]\(" + re.escape(target_name) + r"\.md\)")

        backlinks: list[str] = []
        for md_path in self._all_markdown_files():
            try:
                content = md_path.read_text(encoding="utf-8")
                if wiki_pattern.search(content) or md_link_pattern.search(content):
                    rel = md_path.relative_to(vault)
                    backlinks.append(str(rel))
            except Exception as exc:
                logger.debug("[ObsidianCLI] Failed to scan backlinks in %s: %s", md_path, exc)

        return backlinks
