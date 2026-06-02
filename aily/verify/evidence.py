from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAGE_SAMPLE_DIRS: dict[str, str] = {
    "00-Chaos": "chaos",
    "00-Chaos/sources": "chaos/sources",
    "00-Chaos/canonical-markdown": "chaos/canonical-markdown",
    "01-Data": "data",
    "02-Information": "information",
    "03-Knowledge": "knowledge",
    "04-Insight": "insight",
    "05-Wisdom": "wisdom",
    "06-Impact": "impact",
    "07-Research": "research",
    "07-Research/Second-Opinions": "research/second-opinions",
    "08-Evaluations": "evaluations",
    "09-Business-Plans": "business-plans",
    "10-Dossiers": "dossier",
    "99-System": "system",
    "07-Proposal": "proposal",
    "08-Entrepreneurship": "entrepreneurship",
    "99-MOC": "moc",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    return str(value)


def make_run_id(scenario: str, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H-%M-%SZ")
    safe_scenario = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in scenario).strip("_")
    return f"{stamp}_{safe_scenario or 'run'}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(paths: list[Path], contexts: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    contexts = contexts or {}
    records: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Evidence source does not exist: {resolved}")
        stat = resolved.stat()
        context = contexts.get(str(resolved), {})
        records.append(
            {
                "path": str(resolved),
                "name": resolved.name,
                "size_bytes": stat.st_size,
                "sha256": sha256_file(resolved),
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                **context,
            }
        )
    return records


def artifact_inventory(run_path: Path) -> list[dict[str, Any]]:
    """Record generated artifacts and hashes, excluding manifest files in flight."""
    root = run_path.expanduser().resolve()
    records: list[dict[str, Any]] = []
    if not root.exists():
        return records
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(root))
        if relative_path == "manifest.json":
            continue
        if relative_path.endswith(("-wal", "-shm")):
            continue
        is_runtime_artifact = relative_path.startswith("runtime/")
        records.append(
            {
                "path": str(path),
                "relative_path": relative_path,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "artifact_class": "runtime-artifact" if is_runtime_artifact else "evidence-file",
                "requires_origin": not is_runtime_artifact,
            }
        )
    return records


def vault_counts(vault_path: Path) -> dict[str, int]:
    vault = vault_path.expanduser().resolve()
    counts: dict[str, int] = {}
    for stage_dir in STAGE_SAMPLE_DIRS:
        directory = vault / stage_dir
        counts[stage_dir] = len(list(directory.rglob("*.md"))) if directory.exists() else 0
    return counts


def graph_snapshot(graph_db_path: Path, *, limit: int = 200) -> dict[str, Any]:
    db_path = graph_db_path.expanduser().resolve()
    if not db_path.exists():
        return {
            "exists": False,
            "path": str(db_path),
            "node_counts": {},
            "edge_count": 0,
            "recent_nodes": [],
            "recent_edges": [],
        }

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        node_counts = {
            row["type"]: row["count"]
            for row in conn.execute("SELECT type, COUNT(*) AS count FROM nodes GROUP BY type").fetchall()
        }
        edge_count = int(conn.execute("SELECT COUNT(*) AS count FROM edges").fetchone()["count"])
        recent_nodes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, type, label, source, created_at
                FROM nodes
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        recent_edges = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, source_node_id, target_node_id, relation_type, weight, source, created_at
                FROM edges
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]

    return {
        "exists": True,
        "path": str(db_path),
        "node_counts": node_counts,
        "edge_count": edge_count,
        "recent_nodes": recent_nodes,
        "recent_edges": recent_edges,
    }


def _frontmatter_keys(text: str) -> list[str]:
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    if end == -1:
        return []
    keys: list[str] = []
    for line in text[4:end].splitlines():
        if ":" in line:
            keys.append(line.split(":", 1)[0].strip())
    return keys


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.strip()
    return ""


def _source_ids_in_text(text: str) -> list[str]:
    candidates = re.findall(r"\b(?:source[_-]?id|source):\s*['\"]?([A-Za-z0-9_.:-]+)", text, flags=re.IGNORECASE)
    return sorted(set(candidates))


def obsidian_vault_review(vault_path: Path, *, limit_per_stage: int = 5) -> dict[str, Any]:
    """Inspect vault files so gate review is not based only on runtime output."""
    vault = vault_path.expanduser().resolve()
    review: dict[str, Any] = {
        "vault_path": str(vault),
        "exists": vault.exists(),
        "stage_directories": {},
        "notes": [
            "This is a machine-generated vault review. It records observed files and metadata only.",
            "Gate auditors must compare this with runtime results, database records, and direct observations.",
        ],
    }
    for stage_dir in STAGE_SAMPLE_DIRS:
        directory = vault / stage_dir
        files = sorted(directory.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True) if directory.exists() else []
        inspected = []
        for file_path in files[:limit_per_stage]:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            inspected.append(
                {
                    "path": str(file_path),
                    "relative_path": str(file_path.relative_to(vault)),
                    "size_bytes": file_path.stat().st_size,
                    "sha256": sha256_file(file_path),
                    "frontmatter_keys": _frontmatter_keys(text),
                    "first_heading": _first_heading(text),
                    "source_ids": _source_ids_in_text(text),
                    "excerpt": text[:500],
                }
            )
        review["stage_directories"][stage_dir] = {
            "exists": directory.exists(),
            "markdown_count": len(files),
            "inspected_count": len(inspected),
            "inspected": inspected,
        }
    return review


def evidence_matrix(
    *,
    scenario: str,
    mocked: bool,
    result: dict[str, Any],
    failures: list[dict[str, Any]],
    ui_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map gate requirements to independently generated evidence files."""
    return {
        "scenario": scenario,
        "mocked": mocked,
        "requirements": [
            {
                "requirement": "source truth",
                "evidence_sources": ["source-manifest.json", "command.txt"],
                "status": "present",
            },
            {
                "requirement": "runtime truth",
                "evidence_sources": ["command.txt", "stdout.log", "stderr.log", "environment.json"],
                "status": "present",
            },
            {
                "requirement": "durable state truth",
                "evidence_sources": ["graph-before.json", "graph-after.json", "workflow-runs.json", "source-record.json"],
                "status": "scenario-dependent",
            },
            {
                "requirement": "obsidian truth",
                "evidence_sources": ["obsidian-vault-review.json", "vault-counts-before.json", "vault-counts-after.json"],
                "status": "present",
            },
            {
                "requirement": "event truth",
                "evidence_sources": ["ui-events.jsonl", "ui-event-summary.json"],
                "status": "present" if ui_events else "empty",
            },
            {
                "requirement": "failure truth",
                "evidence_sources": ["failures.json", "stderr.log"],
                "status": "failures-present" if failures else "no-failures-recorded",
            },
        ],
        "result_keys": sorted(result.keys()),
    }


def cross_source_reconciliation(
    *,
    result: dict[str, Any],
    failures: list[dict[str, Any]],
    vault_before: dict[str, int],
    vault_after: dict[str, int],
    graph_before: dict[str, Any],
    graph_after: dict[str, Any],
    ui_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare evidence perspectives without making a gate-pass decision."""
    return {
        "summary": {
            "failures_count": len(failures),
            "ui_event_count": len(ui_events),
            "result_keys": sorted(result.keys()),
        },
        "vault_delta": {
            key: int(vault_after.get(key, 0)) - int(vault_before.get(key, 0))
            for key in sorted(set(vault_before) | set(vault_after))
        },
        "graph_delta": {
            "edge_count": int(graph_after.get("edge_count", 0)) - int(graph_before.get("edge_count", 0)),
            "node_counts": {
                key: int(graph_after.get("node_counts", {}).get(key, 0))
                - int(graph_before.get("node_counts", {}).get(key, 0))
                for key in sorted(set(graph_before.get("node_counts", {})) | set(graph_after.get("node_counts", {})))
            },
        },
        "observations": [
            "This reconciliation is generated by the evidence harness.",
            "It does not certify a gate by itself; an independent gate auditor must compare all sources.",
        ],
        "source_id_reconciliation": result.get("source_reconciliation", {}),
    }


def business_reconciliation(vault_path: Path, graph_db_path: Path, result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reconcile proposal and entrepreneurship output across vault, graph, and run result."""
    counts = vault_counts(vault_path)
    graph = graph_snapshot(graph_db_path)
    graph_counts = graph.get("node_counts", {})
    status_counts: dict[str, int] = {}
    db_path = graph_db_path.expanduser().resolve()
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "node_properties" in tables:
                rows = conn.execute(
                    """
                    SELECT json_extract(np.properties, '$.status') AS status, COUNT(*) AS count
                    FROM nodes n
                    LEFT JOIN node_properties np ON np.node_id = n.id
                    WHERE n.type IN ('reactor_proposal', 'residual_proposal')
                    GROUP BY status
                    """
                ).fetchall()
                status_counts = {str(row["status"] or "missing"): int(row["count"]) for row in rows}

    payload = result or {}
    vault_proposals = counts.get("07-Proposal", 0)
    vault_entrepreneurship = counts.get("08-Entrepreneurship", 0)
    graph_proposals = int(graph_counts.get("reactor_proposal", 0)) + int(graph_counts.get("residual_proposal", 0))
    evaluated_statuses = {
        "incubating",
        "rejected_business",
        "rejected_innovation",
        "needs_more_validation",
    }
    graph_evaluated = sum(count for status, count in status_counts.items() if status in evaluated_statuses)
    return {
        "vault_proposal_notes": vault_proposals,
        "vault_entrepreneurship_notes": vault_entrepreneurship,
        "graph_proposal_nodes": graph_proposals,
        "graph_proposal_status_counts": dict(sorted(status_counts.items())),
        "graph_evaluated_proposal_nodes": graph_evaluated,
        "run_reactor_proposals": payload.get("reactor_proposals"),
        "run_business_result": payload.get("business_result"),
        "proposal_limit": os.getenv("AILY_PROPOSAL_MAX_PER_SESSION", ""),
        "notes": [
            "08-Entrepreneurship can contain one session summary plus one note per evaluated proposal.",
            "Counts reconcile through documented filters: graph proposal nodes include Reactor/Residual nodes; vault notes include human-readable markdown artifacts.",
        ],
    }


def git_state(repo_root: Path) -> dict[str, Any]:
    def _run(args: list[str]) -> str:
        try:
            return subprocess.check_output(args, cwd=repo_root, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""

    status = _run(["git", "status", "--short"])
    return {
        "git_sha": _run(["git", "rev-parse", "HEAD"]),
        "branch": _run(["git", "branch", "--show-current"]),
        "dirty_worktree": bool(status),
        "status_short": status.splitlines(),
    }


def environment_snapshot() -> dict[str, Any]:
    return {
        "cwd": str(Path.cwd()),
        "python": os.sys.version,
        "env_flags": {
            "LLM_PROVIDER": os.getenv("LLM_PROVIDER", ""),
            "AILY_DIKIWI_ENABLED": os.getenv("AILY_DIKIWI_ENABLED", ""),
            "AILY_INNOVATION_ENABLED": os.getenv("AILY_INNOVATION_ENABLED", ""),
            "AILY_ENTREPRENEUR_ENABLED": os.getenv("AILY_ENTREPRENEUR_ENABLED", ""),
        },
    }


@dataclass
class EvidenceRun:
    root_dir: Path
    scenario: str
    vault_path: Path
    graph_db_path: Path
    source_paths: list[Path] = field(default_factory=list)
    source_selector: str = "explicit"
    source_seed: int | None = None
    source_contexts: dict[str, dict[str, Any]] = field(default_factory=dict)
    command: list[str] = field(default_factory=lambda: list(os.sys.argv))
    mocked: bool = False
    fake_components: list[str] = field(default_factory=list)
    real_files: bool = True
    real_graph_db: bool = True
    real_vault: bool = True
    real_llm: bool = True
    real_chat: bool = True
    real_workflow: bool = True
    claimed_components: list[str] = field(default_factory=lambda: ["files", "graph_db", "vault", "llm"])
    run_id: str | None = None

    def __post_init__(self) -> None:
        self.root_dir = self.root_dir.expanduser().resolve()
        self.vault_path = self.vault_path.expanduser().resolve()
        self.graph_db_path = self.graph_db_path.expanduser().resolve()
        self._validate_acceptance_contract()
        self.run_id = self.run_id or make_run_id(self.scenario)
        self.path = self.root_dir / self.run_id
        self.started_at = utc_timestamp()
        self.path.mkdir(parents=True, exist_ok=True)
        for sample_dir in STAGE_SAMPLE_DIRS.values():
            (self.path / "samples" / sample_dir).mkdir(parents=True, exist_ok=True)
        self.write_text("command.txt", " ".join(self.command))
        self.write_json("environment.json", environment_snapshot())

    def _validate_acceptance_contract(self) -> None:
        """Prevent fake runs from being labeled as real acceptance evidence."""
        if self.mocked:
            return

        component_flags = {
            "files": self.real_files,
            "graph_db": self.real_graph_db,
            "vault": self.real_vault,
            "llm": self.real_llm,
            "chat": self.real_chat,
            "workflow": self.real_workflow,
        }
        unknown_components = sorted(set(self.claimed_components) - set(component_flags))
        missing_real_paths = [
            name
            for name in self.claimed_components
            if name in component_flags and not component_flags[name]
        ]
        if missing_real_paths or self.fake_components or unknown_components:
            details = {
                "missing_real_paths": missing_real_paths,
                "fake_components": self.fake_components,
                "unknown_components": unknown_components,
            }
            raise ValueError(
                "EvidenceRun cannot claim mocked=False with non-real acceptance components: "
                f"{details}"
            )

    def _origin(self, relative_path: str, generation_method: str) -> dict[str, Any]:
        return {
            "creator": "evidence-runner",
            "created_at": utc_timestamp(),
            "generation_method": generation_method,
            "evidence_class": "development" if self.mocked else "acceptance",
            "modified_by_lead_agent": False,
            "run_id": self.run_id,
            "scenario": self.scenario,
            "relative_path": relative_path,
        }

    def _json_payload_with_origin(
        self,
        relative_path: str,
        payload: Any,
        generation_method: str,
    ) -> dict[str, Any]:
        origin = self._origin(relative_path, generation_method)
        if isinstance(payload, dict):
            return {"_origin": origin, **payload}
        if isinstance(payload, list):
            return {"_origin": origin, "records": payload}
        return {"_origin": origin, "data": payload}

    def _text_header(self, relative_path: str, generation_method: str) -> str:
        origin = self._origin(relative_path, generation_method)
        return (
            "---\n"
            f"origin_creator: {origin['creator']}\n"
            f"origin_created_at: {origin['created_at']}\n"
            f"origin_generation_method: {json.dumps(origin['generation_method'])}\n"
            f"origin_evidence_class: {origin['evidence_class']}\n"
            "origin_modified_by_lead_agent: false\n"
            f"origin_run_id: {origin['run_id']}\n"
            f"origin_scenario: {origin['scenario']}\n"
            "---\n\n"
        )

    def write_json(
        self,
        relative_path: str,
        payload: Any,
        *,
        generation_method: str = "EvidenceRun.write_json",
    ) -> Path:
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload_with_origin = self._json_payload_with_origin(relative_path, payload, generation_method)
        target.write_text(json.dumps(payload_with_origin, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        return target

    def write_text(
        self,
        relative_path: str,
        text: str,
        *,
        generation_method: str = "EvidenceRun.write_text",
    ) -> Path:
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._text_header(relative_path, generation_method) + text, encoding="utf-8")
        return target

    def write_jsonl(
        self,
        relative_path: str,
        records: list[dict[str, Any]],
        *,
        generation_method: str = "EvidenceRun.write_jsonl",
    ) -> Path:
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        origin_line = json.dumps(
            {"_origin": self._origin(relative_path, generation_method)},
            ensure_ascii=False,
            default=_json_default,
        )
        body = "".join(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n" for record in records)
        target.write_text(origin_line + "\n" + body, encoding="utf-8")
        return target

    def capture_before(self) -> None:
        self._vault_counts_before = vault_counts(self.vault_path)
        self._graph_before = graph_snapshot(self.graph_db_path)
        self.write_json("vault-counts-before.json", self._vault_counts_before)
        self.write_json("graph-before.json", self._graph_before)
        self.write_json("source-manifest.json", source_manifest(self.source_paths, self.source_contexts))

    def capture_after(self) -> dict[str, Any]:
        vault_after = vault_counts(self.vault_path)
        graph_after = graph_snapshot(self.graph_db_path)
        self._vault_counts_after = vault_after
        self._graph_after = graph_after
        self.write_json("vault-counts-after.json", vault_after)
        self.write_json("graph-after.json", graph_after)
        self.copy_vault_samples()
        return {"vault_counts": vault_after, "graph": graph_after}

    def copy_vault_samples(self, *, limit_per_stage: int = 3) -> dict[str, list[str]]:
        copied: dict[str, list[str]] = {}
        for stage_dir, sample_dir in STAGE_SAMPLE_DIRS.items():
            source_dir = self.vault_path / stage_dir
            copied[stage_dir] = []
            if not source_dir.exists():
                continue
            files = sorted(source_dir.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
            for source in files[:limit_per_stage]:
                target = self.path / "samples" / sample_dir / source.name
                relative_target = str(target.relative_to(self.path))
                text = source.read_text(encoding="utf-8", errors="replace")
                self.write_text(
                    relative_target,
                    text,
                    generation_method=f"vault sample copied from {source} sha256={sha256_file(source)}",
                )
                copied[stage_dir].append(str(target))
        self.write_json("samples/index.json", copied)
        return copied

    def finalize(
        self,
        *,
        exit_code: int,
        result: dict[str, Any] | None = None,
        failures: list[dict[str, Any]] | None = None,
        llm_log_file: str | None = None,
        ui_events: list[dict[str, Any]] | None = None,
        stderr_text: str = "",
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        after = self.capture_after()
        failures = failures or []
        result = result or {}
        reconciliation = business_reconciliation(self.vault_path, self.graph_db_path, result)
        self.write_json("proposal-review-reconciliation.json", reconciliation)
        self.write_json("failures.json", failures)
        if llm_log_file:
            llm_path = Path(llm_log_file)
            if llm_path.exists():
                target = self.path / "llm-calls.jsonl"
                origin_line = json.dumps(
                    {"_origin": self._origin("llm-calls.jsonl", f"copied from {llm_path}")},
                    ensure_ascii=False,
                    default=_json_default,
                )
                target.write_text(origin_line + "\n" + llm_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            self.write_jsonl("llm-calls.jsonl", [], generation_method="no llm log file provided")
        ui_events = ui_events or []
        self.write_jsonl(
            "ui-events.jsonl",
            ui_events,
            generation_method="captured UI/status events",
        )
        self.write_json("ui-event-summary.json", summarize_ui_events(ui_events))
        self.write_text("stdout.log", "")
        self.write_text("stderr.log", stderr_text)
        vault_review = obsidian_vault_review(self.vault_path)
        self.write_json("obsidian-vault-review.json", vault_review)
        self.write_json(
            "evidence-matrix.json",
            evidence_matrix(
                scenario=self.scenario,
                mocked=self.mocked,
                result=result,
                failures=failures,
                ui_events=ui_events,
            ),
        )
        self.write_json(
            "cross-source-reconciliation.json",
            cross_source_reconciliation(
                result=result,
                failures=failures,
                vault_before=getattr(self, "_vault_counts_before", {}),
                vault_after=after["vault_counts"],
                graph_before=getattr(self, "_graph_before", {}),
                graph_after=after["graph"],
                ui_events=ui_events,
            ),
        )
        artifact_records = artifact_inventory(self.path)
        self.write_json(
            "artifact-index.json",
            artifact_records,
            generation_method="EvidenceRun artifact inventory before manifest",
        )
        artifact_index_path = self.path / "artifact-index.json"

        git = git_state(repo_root or Path.cwd())
        manifest = {
            "run_id": self.run_id,
            **git,
            "scenario": self.scenario,
            "source_count": len(self.source_paths),
            "source_selector": self.source_selector,
            "source_seed": self.source_seed,
            "vault_path": str(self.vault_path),
            "graph_db_path": str(self.graph_db_path),
            "started_at": self.started_at,
            "completed_at": utc_timestamp(),
            "exit_code": exit_code,
            "acceptance": {
                "mocked": self.mocked,
                "fake_components": self.fake_components,
                "real_files": self.real_files,
                "real_graph_db": self.real_graph_db,
                "real_vault": self.real_vault,
                "real_llm": self.real_llm,
                "real_chat": self.real_chat,
                "real_workflow": self.real_workflow,
                "claimed_components": self.claimed_components,
            },
            "vault_counts_after": after["vault_counts"],
            "graph_node_counts_after": after["graph"].get("node_counts", {}),
            "graph_edge_count_after": after["graph"].get("edge_count", 0),
            "business_reconciliation": reconciliation,
            "evidence_matrix_path": str(self.path / "evidence-matrix.json"),
            "obsidian_vault_review_path": str(self.path / "obsidian-vault-review.json"),
            "cross_source_reconciliation_path": str(self.path / "cross-source-reconciliation.json"),
            "artifact_index_path": str(artifact_index_path),
            "artifact_index_sha256": sha256_file(artifact_index_path),
            "result": result,
            "failures_count": len(failures),
            "ui_event_count": len(ui_events),
            "evidence_integrity": {
                "origin_headers_required": True,
                "modified_by_lead_agent": False,
                "lead_agent_manual_evidence_allowed": False,
            },
        }
        self.write_json("manifest.json", manifest)
        return manifest


def summarize_ui_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str(event.get("type", "")) for event in events)
    stage_events = [
        event
        for event in events
        if event.get("type") in {"stage_started", "stage_completed", "stage_failed"}
    ]
    stages = Counter(str(event.get("stage", "")) for event in stage_events)
    return {
        "total": len(events),
        "by_type": dict(sorted(by_type.items())),
        "stages": dict(sorted(stages.items())),
        "last_event": events[-1] if events else None,
    }
