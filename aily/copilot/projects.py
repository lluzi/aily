"""Persistent project scopes for Aily-Copilot."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CopilotProject:
    id: str
    name: str
    description: str = ""
    include_dirs: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=list)
    source_terms: list[str] = field(default_factory=list)
    system_prompt: str = ""
    preferred_model: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CopilotProjectStore:
    """Small JSON-backed project registry for local Obsidian use."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    def list_projects(self) -> list[dict[str, Any]]:
        payload = self._read()
        return sorted(payload.get("projects", []), key=lambda item: str(item.get("name") or "").lower())

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        clean_id = str(project_id or "").strip()
        if not clean_id:
            return None
        for project in self.list_projects():
            if project.get("id") == clean_id:
                return project
        return None

    def upsert_project(
        self,
        *,
        name: str,
        project_id: str = "",
        description: str = "",
        include_dirs: list[str] | None = None,
        exclude_dirs: list[str] | None = None,
        source_terms: list[str] | None = None,
        system_prompt: str = "",
        preferred_model: str = "",
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("project name is required")
        now = datetime.now(timezone.utc).isoformat()
        clean_id = _slug(project_id or clean_name)
        payload = self._read()
        projects = payload.setdefault("projects", [])
        existing = next((item for item in projects if item.get("id") == clean_id), None)
        created_at = str(existing.get("created_at")) if existing else now
        project = CopilotProject(
            id=clean_id,
            name=clean_name,
            description=str(description or "").strip(),
            include_dirs=_clean_dirs(include_dirs or []),
            exclude_dirs=_clean_dirs(exclude_dirs or []),
            source_terms=_clean_terms(source_terms or []),
            system_prompt=str(system_prompt or "").strip(),
            preferred_model=str(preferred_model or "").strip(),
            created_at=created_at,
            updated_at=now,
        ).to_dict()
        if existing:
            projects[projects.index(existing)] = project
        else:
            projects.append(project)
        self._write(payload)
        return project

    def delete_project(self, project_id: str) -> bool:
        clean_id = str(project_id or "").strip()
        payload = self._read()
        projects = payload.setdefault("projects", [])
        kept = [item for item in projects if item.get("id") != clean_id]
        if len(kept) == len(projects):
            return False
        payload["projects"] = kept
        self._write(payload)
        return True

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"projects": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"projects": []}
        return data if isinstance(data, dict) else {"projects": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _clean_dirs(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = str(value or "").strip().strip("/")
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _clean_terms(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "project"
