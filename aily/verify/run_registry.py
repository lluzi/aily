from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUN_ID_ALLOWED_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


class RunRegistryError(Exception):
    """Base error for run registry failures."""


class RunNotFoundError(RunRegistryError):
    """Raised when a run id does not map to a stored evidence run."""


def _safe_run_id(run_id: str) -> str:
    candidate = run_id.strip()
    if not candidate or any(ch not in RUN_ID_ALLOWED_CHARS for ch in candidate):
        raise RunNotFoundError(f"Run not found: {run_id}")
    return candidate


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_payload(path: Path, default: Any) -> Any:
    payload = _read_json(path, default)
    if not isinstance(payload, dict) or "_origin" not in payload:
        return payload
    if "records" in payload and len(payload) == 2:
        return payload["records"]
    if "data" in payload and len(payload) == 2:
        return payload["data"]
    return {key: value for key, value in payload.items() if key != "_origin"}


def _read_jsonl(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if isinstance(record, dict) and "_origin" in record and len(record) == 1:
                continue
            records.append(record)
        except json.JSONDecodeError:
            records.append({"raw": line, "parse_error": True})
    return records[-max(1, limit):]


@dataclass(frozen=True)
class RunRegistry:
    root_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_dir", self.root_dir.expanduser().resolve())

    def list_runs(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """List evidence runs sorted by completion/start time descending."""
        runs = []
        if self.root_dir.exists():
            for path in self.root_dir.iterdir():
                if not path.is_dir():
                    continue
                manifest_path = path / "manifest.json"
                if not manifest_path.exists():
                    continue
                manifest = _read_json_payload(manifest_path, {})
                runs.append(self._summary(path.name, manifest))

        runs.sort(key=lambda item: item.get("completed_at") or item.get("started_at") or "", reverse=True)
        safe_offset = max(0, offset)
        safe_limit = min(max(1, limit), 200)
        return {
            "root_dir": str(self.root_dir),
            "total": len(runs),
            "runs": runs[safe_offset:safe_offset + safe_limit],
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = self._run_path(run_id)
        manifest = _read_json_payload(path / "manifest.json", {})
        if not manifest:
            raise RunNotFoundError(f"Run not found: {run_id}")

        return {
            "run": self._summary(path.name, manifest),
            "manifest": manifest,
            "command": (path / "command.txt").read_text(encoding="utf-8") if (path / "command.txt").exists() else "",
            "environment": _read_json_payload(path / "environment.json", {}),
            "source_manifest": _read_json_payload(path / "source-manifest.json", []),
            "vault_counts_before": _read_json_payload(path / "vault-counts-before.json", {}),
            "vault_counts_after": _read_json_payload(path / "vault-counts-after.json", {}),
            "graph_before": _read_json_payload(path / "graph-before.json", {}),
            "graph_after": _read_json_payload(path / "graph-after.json", {}),
            "failures": _read_json_payload(path / "failures.json", []),
            "ui_event_summary": _read_json_payload(path / "ui-event-summary.json", {}),
            "evidence_matrix": _read_json_payload(path / "evidence-matrix.json", {}),
            "obsidian_vault_review": _read_json_payload(path / "obsidian-vault-review.json", {}),
            "cross_source_reconciliation": _read_json_payload(path / "cross-source-reconciliation.json", {}),
            "samples": _read_json_payload(path / "samples" / "index.json", {}),
        }

    def get_events(self, run_id: str, *, limit: int = 500) -> dict[str, Any]:
        path = self._run_path(run_id)
        return {
            "run_id": path.name,
            "events": _read_jsonl(path / "ui-events.jsonl", limit=limit),
        }

    def get_llm_calls(self, run_id: str, *, limit: int = 500) -> dict[str, Any]:
        path = self._run_path(run_id)
        return {
            "run_id": path.name,
            "llm_calls": _read_jsonl(path / "llm-calls.jsonl", limit=limit),
        }

    def _run_path(self, run_id: str) -> Path:
        safe_id = _safe_run_id(run_id)
        path = (self.root_dir / safe_id).resolve()
        if path.parent != self.root_dir or not path.is_dir():
            raise RunNotFoundError(f"Run not found: {run_id}")
        return path

    @staticmethod
    def _summary(run_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        acceptance = manifest.get("acceptance") or {}
        result = manifest.get("result") or {}
        return {
            "run_id": manifest.get("run_id") or run_id,
            "scenario": manifest.get("scenario"),
            "completed_at": manifest.get("completed_at"),
            "started_at": manifest.get("started_at"),
            "exit_code": manifest.get("exit_code"),
            "source_count": manifest.get("source_count", 0),
            "mocked": bool(acceptance.get("mocked", True)),
            "fake_components": acceptance.get("fake_components", []),
            "real_llm": bool(acceptance.get("real_llm", False)),
            "failures_count": manifest.get("failures_count", 0),
            "ui_event_count": manifest.get("ui_event_count", 0),
            "graph_edge_count_after": manifest.get("graph_edge_count_after", 0),
            "vault_counts_after": manifest.get("vault_counts_after", {}),
            "business_skipped_reason": result.get("business_skipped_reason"),
            "evidence_path": str(Path(manifest.get("run_id") or run_id)),
        }
