from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BackupManifest:
    created_at: str
    vault_files: int
    source_files: int
    graph_db_sha256: str | None
    source_db_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "vault_files": self.vault_files,
            "source_files": self.source_files,
            "graph_db_sha256": self.graph_db_sha256,
            "source_db_sha256": self.source_db_sha256,
        }


def _write_tree(zip_file: zipfile.ZipFile, root: Path, prefix: str) -> int:
    if not root.exists():
        return 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file():
            zip_file.write(path, f"{prefix}/{path.relative_to(root)}")
            count += 1
    return count


def create_backup(
    *,
    vault_path: Path,
    graph_db_path: Path,
    source_store_db_path: Path,
    source_object_dir: Path,
    output_path: Path,
) -> BackupManifest:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph_hash = _sha256(graph_db_path) if graph_db_path.exists() else None
    source_db_hash = _sha256(source_store_db_path) if source_store_db_path.exists() else None
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        vault_files = _write_tree(archive, vault_path, "vault")
        source_files = _write_tree(archive, source_object_dir, "sources")
        if graph_db_path.exists():
            archive.write(graph_db_path, "graph/graph.db")
        if source_store_db_path.exists():
            archive.write(source_store_db_path, "source_store/source_store.db")
        manifest = BackupManifest(
            created_at=datetime.now(timezone.utc).isoformat(),
            vault_files=vault_files,
            source_files=source_files,
            graph_db_sha256=graph_hash,
            source_db_sha256=source_db_hash,
        )
        archive.writestr("manifest.json", json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
    return manifest


def _safe_zip_members(archive: zipfile.ZipFile, restore_dir: Path) -> list[zipfile.ZipInfo]:
    restore_root = restore_dir.resolve()
    safe_members: list[zipfile.ZipInfo] = []
    for member in archive.infolist():
        member_path = Path(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"Unsafe backup member path: {member.filename}")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"Unsafe backup member symlink: {member.filename}")
        target = (restore_root / member.filename).resolve()
        if target != restore_root and restore_root not in target.parents:
            raise ValueError(f"Backup member escapes restore dir: {member.filename}")
        safe_members.append(member)
    return safe_members


def restore_backup(*, backup_path: Path, restore_dir: Path) -> dict[str, Any]:
    restore_parent = restore_dir.parent
    restore_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{restore_dir.name}-", dir=restore_parent) as temp_name:
        temp_restore = Path(temp_name)
        with zipfile.ZipFile(backup_path, "r") as archive:
            safe_members = _safe_zip_members(archive, temp_restore)
            for member in safe_members:
                archive.extract(member, temp_restore)
        if restore_dir.exists():
            shutil.rmtree(restore_dir)
        shutil.move(str(temp_restore), str(restore_dir))
    manifest_path = restore_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return {
        "restore_dir": str(restore_dir),
        "manifest": manifest,
        "vault_files": len(list((restore_dir / "vault").rglob("*"))) if (restore_dir / "vault").exists() else 0,
        "source_files": len(list((restore_dir / "sources").rglob("*"))) if (restore_dir / "sources").exists() else 0,
    }
