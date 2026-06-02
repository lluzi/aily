"""Helpers for locating human-visible Obsidian vaults used by evidence runs."""

from __future__ import annotations

from pathlib import Path

from aily.config import SETTINGS


ICLOUD_DOCUMENTS_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Documents"
DEFAULT_SHARED_VAULT_PATH = ICLOUD_DOCUMENTS_DIR / "aily"


def resolve_test_vault_path(run_id: str, requested: Path | None = None) -> Path:
    """Return a human-visible Obsidian vault path for evidence/test runs."""
    del run_id
    default = Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path or DEFAULT_SHARED_VAULT_PATH)
    path = (requested if requested is not None else default).expanduser().resolve()
    _reject_hidden_vault_path(path)
    return path


def _reject_hidden_vault_path(path: Path) -> None:
    home = Path.home().resolve()
    documents = home / "Documents"
    icloud_documents = ICLOUD_DOCUMENTS_DIR.resolve()
    parts = path.parts
    hidden_parts = [part for part in parts if part.startswith(".") and part not in {".", ".."}]
    if hidden_parts:
        raise ValueError(f"Test vault path must not be in a hidden directory: {path}")
    allowed_roots = (documents, icloud_documents)
    if any(_is_relative_to(path, root) for root in allowed_roots):
        return
    roots = ", ".join(str(root) for root in allowed_roots)
    raise ValueError(f"Test vault path must be under one of: {roots}. Got: {path}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
