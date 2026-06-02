"""Preview-first write proposals for Aily-Copilot."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CopilotProposalStore:
    """Store write proposals outside the vault until the user approves them."""

    def __init__(self, *, vault_path: Path, store_path: Path) -> None:
        self.vault_path = vault_path.expanduser().resolve()
        self.store_path = store_path.expanduser().resolve()

    def create_proposal(
        self,
        *,
        target_path: str,
        title: str,
        content: str,
        mode: str = "create",
        rationale: str = "",
        source_citations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        clean_target = _clean_target_path(target_path, title)
        if mode not in {"create", "replace", "append"}:
            raise ValueError("mode must be one of: create, replace, append")
        if not str(content or "").strip():
            raise ValueError("proposal content is required")
        target = self._resolve_target(clean_target)
        before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        after = _build_after_text(before=before, content=content, title=title, mode=mode)
        proposal_id = _proposal_id(clean_target, after)
        now = datetime.now(timezone.utc).isoformat()
        proposal = {
            "id": proposal_id,
            "status": "pending",
            "target_path": clean_target,
            "title": str(title or Path(clean_target).stem).strip(),
            "mode": mode,
            "rationale": str(rationale or "").strip(),
            "source_citations": source_citations or [],
            "created_at": now,
            "updated_at": now,
            "target_exists": target.exists(),
            "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
            "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
            "diff": _diff(before, after, fromfile=f"a/{clean_target}", tofile=f"b/{clean_target}"),
            "preview": after,
        }
        payload = self._read()
        proposals = payload.setdefault("proposals", [])
        proposals[:] = [item for item in proposals if item.get("id") != proposal_id]
        proposals.append(proposal)
        self._write(payload)
        return proposal

    def list_proposals(self, status: str = "") -> list[dict[str, Any]]:
        proposals = self._read().get("proposals", [])
        clean_status = str(status or "").strip().lower()
        if clean_status:
            proposals = [item for item in proposals if str(item.get("status") or "").lower() == clean_status]
        return sorted(proposals, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        clean_id = str(proposal_id or "").strip()
        for proposal in self._read().get("proposals", []):
            if proposal.get("id") == clean_id:
                return proposal
        return None

    def apply_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            raise FileNotFoundError(f"proposal not found: {proposal_id}")
        if proposal.get("status") != "pending":
            raise ValueError(f"proposal is not pending: {proposal_id}")
        target = self._resolve_target(str(proposal["target_path"]))
        before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        before_sha = hashlib.sha256(before.encode("utf-8")).hexdigest()
        if before_sha != proposal.get("before_sha256"):
            raise ValueError("target changed after proposal creation; create a fresh preview before applying")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(proposal.get("preview") or ""), encoding="utf-8")
        return self._update_status(proposal_id, "applied")

    def reject_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            raise FileNotFoundError(f"proposal not found: {proposal_id}")
        return self._update_status(proposal_id, "rejected")

    def _update_status(self, proposal_id: str, status: str) -> dict[str, Any]:
        payload = self._read()
        for proposal in payload.get("proposals", []):
            if proposal.get("id") == proposal_id:
                proposal["status"] = status
                proposal["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._write(payload)
                return proposal
        raise FileNotFoundError(f"proposal not found: {proposal_id}")

    def _resolve_target(self, relative_path: str) -> Path:
        raw = str(relative_path or "").strip().lstrip("/")
        if ".." in Path(raw).parts:
            raise ValueError("target path must not contain parent directory segments")
        target = (self.vault_path / raw).resolve()
        try:
            target.relative_to(self.vault_path)
        except ValueError as exc:
            raise ValueError("target path must stay inside vault") from exc
        if target.suffix.lower() != ".md":
            raise ValueError("target path must be a Markdown file")
        return target

    def _read(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {"proposals": []}
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"proposals": []}
        return data if isinstance(data, dict) else {"proposals": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.store_path)


def _build_after_text(*, before: str, content: str, title: str, mode: str) -> str:
    clean_content = str(content or "").strip() + "\n"
    if mode == "append" and before:
        return before.rstrip() + "\n\n" + clean_content
    if clean_content.lstrip().startswith("#"):
        return clean_content
    clean_title = str(title or "Aily Copilot Note").strip()
    return f"# {clean_title}\n\n{clean_content}"


def _clean_target_path(target_path: str, title: str) -> str:
    raw = str(target_path or "").strip().strip("/")
    if not raw:
        raw = f"10-Dossiers/{_slug(title or 'Aily Copilot Note')}.md"
    if not raw.endswith(".md"):
        raw += ".md"
    return raw


def _diff(before: str, after: str, *, fromfile: str, tofile: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def _proposal_id(target_path: str, after: str) -> str:
    seed = f"{target_path}\n{after}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "aily-copilot-note"
