"""Evidence run validation helpers.

These helpers validate generated evidence structure. They do not decide product
quality by themselves; gate auditors still need to review the content against
the gate criteria.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aily.verify.evidence import sha256_file


SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"tvly-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"api[_-]?key['\"]?\s*[:=]\s*['\"][A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
)

REQUIRED_RUN_FILES: tuple[str, ...] = (
    "manifest.json",
    "artifact-index.json",
    "evidence-matrix.json",
    "obsidian-vault-review.json",
    "cross-source-reconciliation.json",
    "source-manifest.json",
    "environment.json",
    "stdout.log",
    "stderr.log",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _has_text_origin(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.startswith("---\n") and "origin_modified_by_lead_agent: false" in text[:1000]


def _has_json_origin(path: Path) -> bool:
    try:
        payload = _read_json(path)
    except json.JSONDecodeError:
        return False
    origin = payload.get("_origin") if isinstance(payload, dict) else None
    return isinstance(origin, dict) and origin.get("modified_by_lead_agent") is False


def _has_jsonl_origin(path: Path) -> bool:
    first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0:1]
    if not first_line:
        return False
    try:
        payload = json.loads(first_line[0])
    except json.JSONDecodeError:
        return False
    origin = payload.get("_origin") if isinstance(payload, dict) else None
    return isinstance(origin, dict) and origin.get("modified_by_lead_agent") is False


def _has_origin(path: Path) -> bool:
    if path.suffix == ".json":
        return _has_json_origin(path)
    if path.suffix == ".jsonl":
        return _has_jsonl_origin(path)
    return _has_text_origin(path)


def _secret_hits(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def validate_evidence_run(run_path: Path) -> dict[str, Any]:
    path = run_path.expanduser().resolve()
    failures: list[dict[str, Any]] = []
    if not path.is_dir():
        return {"valid": False, "run_path": str(path), "failures": [{"check": "run_path", "error": "missing"}]}

    for relative_path in REQUIRED_RUN_FILES:
        if not (path / relative_path).is_file():
            failures.append({"check": "required_file", "path": relative_path, "error": "missing"})

    manifest_path = path / "manifest.json"
    artifact_index_path = path / "artifact-index.json"
    manifest: dict[str, Any] = {}
    artifact_index: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = _read_json(manifest_path)
        except json.JSONDecodeError as exc:
            failures.append({"check": "manifest_json", "error": str(exc)})
    if artifact_index_path.exists():
        try:
            artifact_index = _read_json(artifact_index_path)
        except json.JSONDecodeError as exc:
            failures.append({"check": "artifact_index_json", "error": str(exc)})

    if manifest and not _has_json_origin(manifest_path):
        failures.append({"check": "origin", "path": "manifest.json", "error": "missing or invalid _origin"})
    if artifact_index and not _has_json_origin(artifact_index_path):
        failures.append({"check": "origin", "path": "artifact-index.json", "error": "missing or invalid _origin"})

    expected_artifact_index_hash = manifest.get("artifact_index_sha256") if manifest else None
    if expected_artifact_index_hash and artifact_index_path.exists():
        actual = sha256_file(artifact_index_path)
        if actual != expected_artifact_index_hash:
            failures.append(
                {
                    "check": "artifact_index_hash",
                    "expected": expected_artifact_index_hash,
                    "actual": actual,
                }
            )

    records = artifact_index.get("records") if isinstance(artifact_index, dict) else None
    if records is None and isinstance(artifact_index, dict):
        records = artifact_index.get("data")
    if records is None and isinstance(artifact_index, dict):
        records = [value for value in artifact_index.values() if isinstance(value, list)]
        records = records[0] if records else []

    for record in records or []:
        relative_path = str(record.get("relative_path") or "")
        if not relative_path:
            failures.append({"check": "artifact_record", "error": "missing relative_path", "record": record})
            continue
        artifact_path = path / relative_path
        if not artifact_path.is_file():
            failures.append({"check": "artifact_exists", "path": relative_path, "error": "missing"})
            continue
        actual_hash = sha256_file(artifact_path)
        if actual_hash != record.get("sha256"):
            failures.append(
                {
                    "check": "artifact_hash",
                    "path": relative_path,
                    "expected": record.get("sha256"),
                    "actual": actual_hash,
                }
            )
        requires_origin = bool(record.get("requires_origin", not relative_path.startswith("runtime/")))
        if requires_origin and not _has_origin(artifact_path):
            failures.append({"check": "origin", "path": relative_path, "error": "missing or invalid origin"})
        hits = _secret_hits(artifact_path) if requires_origin or artifact_path.suffix in {".json", ".jsonl", ".txt", ".log", ".md"} else []
        if hits:
            failures.append({"check": "secret_scan", "path": relative_path, "patterns": hits})

    return {
        "valid": not failures,
        "run_path": str(path),
        "failures": failures,
        "checked_artifacts": len(records or []),
    }
