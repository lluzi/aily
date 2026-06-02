#!/usr/bin/env python3
"""M9 V1 release-readiness evidence runner source code only.

Origin: Created by Codex release-evidence worker on 2026-05-17.
Role: Runner source code only; not acceptance evidence for any gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aily.config import SETTINGS
from aily.verify.evidence import EvidenceRun, make_run_id, sha256_file, vault_counts


REQUIRED_MANIFESTS: tuple[dict[str, str], ...] = (
    {
        "key": "gate0",
        "gate": "Gate0",
        "milestone": "M0",
        "role": "release_gate",
        "path": "/Users/luzi/.aily/runs/2026-05-17T10-27-19Z_gate0_readiness/manifest.json",
    },
    {
        "key": "gate1",
        "gate": "Gate1",
        "milestone": "M1",
        "role": "release_gate",
        "path": "/Users/luzi/.aily/runs/2026-05-17T10-43-47Z_gate1_pdf_intake/manifest.json",
    },
    {
        "key": "gate2",
        "gate": "Gate2",
        "milestone": "M2",
        "role": "release_gate",
        "path": "/Users/luzi/.aily/runs/2026-05-17T10-51-47Z_gate2_resume_idempotency/manifest.json",
    },
    {
        "key": "gate3",
        "gate": "Gate3",
        "milestone": "M3",
        "role": "release_gate",
        "path": "/Users/luzi/.aily/runs/2026-05-17T11-09-24Z_gate3_triggered_iwi/manifest.json",
    },
    {
        "key": "m4",
        "gate": "support",
        "milestone": "M4",
        "role": "supporting_orchestration",
        "path": "/Users/luzi/.aily/runs/2026-05-17T11-25-06Z_m4_business_planning_graph/manifest.json",
    },
    {
        "key": "m5",
        "gate": "support",
        "milestone": "M5",
        "role": "supporting_orchestration",
        "path": "/Users/luzi/.aily/runs/2026-05-17T11-38-53Z_m5_graph_owned_iwi/manifest.json",
    },
    {
        "key": "gate4_m6",
        "gate": "Gate4",
        "milestone": "M6",
        "role": "release_gate",
        "path": "/Users/luzi/.aily/runs/2026-05-17T12-00-19Z_m6_research_second_opinion/manifest.json",
    },
    {
        "key": "gate5_m7",
        "gate": "Gate5",
        "milestone": "M7",
        "role": "release_gate",
        "path": "/Users/luzi/.aily/runs/2026-05-17T12-12-54Z_m7_business_plan/manifest.json",
    },
    {
        "key": "gate6_m8",
        "gate": "Gate6",
        "milestone": "M8",
        "role": "release_gate",
        "path": "/Users/luzi/.aily/runs/2026-05-17T12-23-22Z_m8_export_email_dry_run/manifest.json",
    },
)

REQUIRED_RUN_PATH_KEYS: tuple[str, ...] = (
    "evidence_matrix_path",
    "obsidian_vault_review_path",
    "cross_source_reconciliation_path",
    "artifact_index_path",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate and audit Aily V1 M9 release-readiness evidence.")
    parser.add_argument("--run-id", default="", help="Optional evidence run id.")
    parser.add_argument("--runs-dir", type=Path, default=SETTINGS.evidence_runs_dir, help="Evidence runs root.")
    parser.add_argument("--vault-path", type=Path, default=SETTINGS.dikiwi_vault_path, help="Obsidian vault path.")
    return parser.parse_args()


def _run_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _failure(check: str, **details: Any) -> dict[str, Any]:
    return {"check": check, **details}


def _validate_run(manifest_path: Path, repo_root: Path) -> dict[str, Any]:
    run_dir = manifest_path.parent
    result = _run_command(["uv", "run", "python", "scripts/validate_evidence_run.py", str(run_dir)], cwd=repo_root)
    parsed: dict[str, Any] = {}
    if result["stdout"].strip():
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError as exc:
            parsed = {"valid": False, "parse_error": str(exc), "raw_stdout": result["stdout"]}
    return {**result, "run_dir": str(run_dir), "parsed": parsed, "valid": result["exit_code"] == 0 and parsed.get("valid") is True}


def _required_artifact_paths(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in REQUIRED_RUN_PATH_KEYS:
        value = manifest.get(key)
        if value:
            records.append({"source": key, "path": str(value), "required": True})

    result = manifest.get("result", {})
    if isinstance(result, dict):
        for key, value in result.items():
            if key.endswith("_path") or key.endswith("_db_path") or key in {"llm_trace_path", "export_record_path"}:
                if value:
                    records.append({"source": f"result.{key}", "path": str(value), "required": True})
        for item in result.get("exported_artifacts", []) if isinstance(result.get("exported_artifacts"), list) else []:
            path = item.get("path") if isinstance(item, dict) else None
            if path:
                records.append({"source": "result.exported_artifacts.path", "path": str(path), "required": True})
        for item in result.get("selected_sources", []) if isinstance(result.get("selected_sources"), list) else []:
            path = item.get("path") if isinstance(item, dict) else None
            if path:
                records.append({"source": "result.selected_sources.path", "path": str(path), "required": True})
    return records


def _check_artifact_paths(records: list[dict[str, Any]], *, base_path: Path | None = None) -> list[dict[str, Any]]:
    checked: list[dict[str, Any]] = []
    for record in records:
        path = Path(record["path"]).expanduser()
        if not path.is_absolute() and base_path is not None:
            path = base_path / path
        exists = path.exists()
        checked.append({**record, "resolved_path": str(path), "exists": exists, "is_file": path.is_file(), "is_dir": path.is_dir()})
    return checked


def _acceptance_review(manifest: dict[str, Any]) -> dict[str, Any]:
    acceptance = manifest.get("acceptance", {})
    return {
        "mocked": acceptance.get("mocked"),
        "fake_components": acceptance.get("fake_components", []),
        "claimed_components": acceptance.get("claimed_components", []),
        "real_files": acceptance.get("real_files"),
        "real_graph_db": acceptance.get("real_graph_db"),
        "real_vault": acceptance.get("real_vault"),
        "real_llm": acceptance.get("real_llm"),
        "real_chat": acceptance.get("real_chat"),
        "real_workflow": acceptance.get("real_workflow"),
    }


def _gate6_real_send_review(manifest: dict[str, Any]) -> dict[str, Any]:
    result = manifest.get("result", {}) if isinstance(manifest.get("result"), dict) else {}
    paths = [
        result.get("email_dry_run_json_path"),
        result.get("email_dry_run_markdown_path"),
    ]
    file_reviews = []
    real_send_detected = result.get("real_send_performed") is True
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(str(raw_path)).expanduser()
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        lowered = text.lower()
        file_reviews.append({"path": str(path), "exists": path.is_file(), "real_send_text_seen": "real_send_performed" in lowered})
        if '"real_send_performed": true' in lowered or "real_send_performed: true" in lowered:
            real_send_detected = True
    return {
        "manifest_result_real_send_performed": result.get("real_send_performed"),
        "real_send_detected": real_send_detected,
        "reviewed_files": file_reviews,
    }


def _inspect_residual_risks(vault_path: Path, m6_manifest: dict[str, Any]) -> dict[str, Any]:
    vault = vault_path.expanduser().resolve()
    note_stems = {path.stem for path in vault.rglob("*.md")} if vault.exists() else set()
    unresolved: list[dict[str, str]] = []
    if vault.exists():
        for path in sorted(vault.rglob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for segment in text.split("[[")[1:]:
                target = segment.split("]]", 1)[0].split("|", 1)[0].split("#", 1)[0].strip()
                if target and "/" not in target and target not in note_stems:
                    unresolved.append({"note": str(path.relative_to(vault)), "target": target})
                    if len(unresolved) >= 50:
                        break
            if len(unresolved) >= 50:
                break

    result = m6_manifest.get("result", {}) if isinstance(m6_manifest.get("result"), dict) else {}
    broad_topic = {
        "research_query": result.get("research_query", ""),
        "topic_label": "",
        "is_broad": False,
    }
    research_packet = Path(m6_manifest["run_id"]).parent / "research-packet.json"
    manifest_path = Path(str(next(item["path"] for item in REQUIRED_MANIFESTS if item["milestone"] == "M6")))
    packet_path = manifest_path.parent / "research-packet.json"
    if packet_path.is_file():
        packet = _read_json(packet_path)
        topic = str(packet.get("topic") or "")
        broad_topic["topic_label"] = topic
        broad_topic["is_broad"] = topic.lower() in {"run", "planning", "research", "external", "knowledge"}
    return {
        "unresolved_wikilink_warning_count_sampled": len(unresolved),
        "unresolved_wikilink_warning_samples": unresolved[:20],
        "broad_m6_topic_label": broad_topic,
        "release_blocking": False,
        "notes": [
            "Residual-risk observations are recorded for follow-up and do not fail M9 unless a hard gate check fails.",
            "Wikilink scan is file-system based and samples the first 50 unresolved targets.",
        ],
    }


def _release_matrix(run_reviews: list[dict[str, Any]], m9_status: str) -> dict[str, Any]:
    by_milestone = {
        review["milestone"]: {
            "status": review["status"],
            "run_id": review.get("run_id", ""),
            "scenario": review.get("scenario", ""),
            "gate": review["gate"],
            "role": review["role"],
            "manifest_path": review["manifest_path"],
        }
        for review in run_reviews
    }
    by_milestone["M9"] = {
        "status": m9_status,
        "run_id": "current_m9_runner",
        "scenario": "m9_release_readiness",
        "gate": "release_aggregate",
        "role": "release_readiness_audit",
        "manifest_path": "generated_by_this_run",
    }
    by_gate = {
        review["gate"]: {
            "status": review["status"],
            "milestone": review["milestone"],
            "run_id": review.get("run_id", ""),
            "scenario": review.get("scenario", ""),
            "manifest_path": review["manifest_path"],
        }
        for review in run_reviews
        if review["gate"].startswith("Gate")
    }
    return {"milestones": by_milestone, "gates": by_gate}


def _missing_statuses(matrix: dict[str, Any]) -> dict[str, list[str]]:
    milestones = matrix.get("milestones", {})
    gates = matrix.get("gates", {})
    return {
        "milestones": [f"M{index}" for index in range(10) if f"M{index}" not in milestones or not milestones[f"M{index}"].get("status")],
        "gates": [f"Gate{index}" for index in range(7) if f"Gate{index}" not in gates or not gates[f"Gate{index}"].get("status")],
    }


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    vault_path = args.vault_path.expanduser().resolve()
    run_id = args.run_id or make_run_id("m9_release_readiness")
    source_paths = [Path(item["path"]).expanduser().resolve() for item in REQUIRED_MANIFESTS if Path(item["path"]).expanduser().is_file()]
    graph_db_path = args.runs_dir.expanduser().resolve() / run_id / "runtime" / "graph-not-exercised.db"

    evidence = EvidenceRun(
        root_dir=args.runs_dir,
        scenario="m9_release_readiness",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=source_paths,
        source_selector="required_m0_m8_manifest_set",
        source_contexts={
            str(Path(item["path"]).expanduser().resolve()): {
                "source_role": item["role"],
                "milestone": item["milestone"],
                "gate": item["gate"],
                "manifest_key": item["key"],
            }
            for item in REQUIRED_MANIFESTS
            if Path(item["path"]).expanduser().is_file()
        },
        command=list(sys.argv),
        mocked=False,
        fake_components=[],
        real_files=True,
        real_graph_db=False,
        real_vault=True,
        real_llm=False,
        real_chat=False,
        real_workflow=True,
        claimed_components=["files", "vault", "workflow"],
        run_id=run_id,
    )
    evidence.capture_before()

    failures: list[dict[str, Any]] = []
    run_reviews: list[dict[str, Any]] = []
    manifests_by_milestone: dict[str, dict[str, Any]] = {}

    for item in REQUIRED_MANIFESTS:
        manifest_path = Path(item["path"]).expanduser().resolve()
        if not manifest_path.is_file():
            failures.append(_failure("required_manifest_exists", milestone=item["milestone"], gate=item["gate"], path=str(manifest_path)))
            run_reviews.append({**item, "manifest_path": str(manifest_path), "status": "FAIL", "failures": ["missing_manifest"]})
            continue

        manifest = _read_json(manifest_path)
        manifests_by_milestone[item["milestone"]] = manifest
        validation = _validate_run(manifest_path, repo_root)
        manifest_vault_path = Path(str(manifest.get("vault_path") or vault_path)).expanduser().resolve()
        artifact_checks = _check_artifact_paths(_required_artifact_paths(manifest), base_path=manifest_vault_path)
        missing_artifacts = [record for record in artifact_checks if not record["exists"]]
        acceptance = _acceptance_review(manifest)

        local_failures: list[dict[str, Any]] = []
        if not validation["valid"]:
            local_failures.append(_failure("validator", validation=validation))
        if manifest.get("exit_code") != 0:
            local_failures.append(_failure("manifest_exit_code", actual=manifest.get("exit_code")))
        if int(manifest.get("failures_count") or 0) != 0:
            local_failures.append(_failure("manifest_failures_count", actual=manifest.get("failures_count")))
        if acceptance.get("mocked") is not False:
            local_failures.append(_failure("acceptance_mocked_false", acceptance=acceptance))
        if acceptance.get("fake_components"):
            local_failures.append(_failure("acceptance_fake_components_empty", acceptance=acceptance))
        if missing_artifacts:
            local_failures.append(_failure("required_artifact_paths_exist", missing=missing_artifacts))
        if item["gate"] == "Gate6":
            gate6_review = _gate6_real_send_review(manifest)
            if gate6_review["real_send_detected"]:
                local_failures.append(_failure("gate6_real_email_send_absent", review=gate6_review))
        else:
            gate6_review = None

        run_review = {
            **item,
            "manifest_path": str(manifest_path),
            "run_id": manifest.get("run_id", ""),
            "scenario": manifest.get("scenario", ""),
            "exit_code": manifest.get("exit_code"),
            "failures_count": manifest.get("failures_count"),
            "acceptance": acceptance,
            "validator": validation,
            "artifact_checks": artifact_checks,
            "gate6_real_send_review": gate6_review,
            "status": "PASS" if not local_failures else "FAIL",
            "failures": local_failures,
        }
        run_reviews.append(run_review)
        failures.extend(local_failures)

    compileall = _run_command(["uv", "run", "python", "-m", "compileall", "-q", "aily", "scripts"], cwd=repo_root)
    if compileall["exit_code"] != 0:
        failures.append(_failure("compileall", result=compileall))

    git_status = _run_command(["git", "status", "--short"], cwd=repo_root)
    git_head = _run_command(["git", "rev-parse", "HEAD"], cwd=repo_root)
    residual_risks = _inspect_residual_risks(vault_path, manifests_by_milestone.get("M6", {}))
    m9_status = "PASS" if not failures else "FAIL"
    release_matrix = _release_matrix(run_reviews, m9_status)
    missing_statuses = _missing_statuses(release_matrix)
    if missing_statuses["milestones"] or missing_statuses["gates"]:
        failures.append(_failure("required_m0_m9_gate0_gate6_status_present", missing=missing_statuses))
        m9_status = "FAIL"
        release_matrix = _release_matrix(run_reviews, m9_status)

    evidence.write_json("validated-run-reviews.json", run_reviews, generation_method="M9 validation of required manifests")
    evidence.write_json("release-matrix.json", release_matrix, generation_method="M9 release matrix covering M0-M9 and Gate0-Gate6")
    evidence.write_json("compileall.json", compileall, generation_method="M9 compileall check")
    evidence.write_json(
        "git-status.json",
        {
            "status_short": git_status,
            "head": git_head,
        },
        generation_method="M9 current git status capture",
    )
    evidence.write_json("vault-counts-v1.json", vault_counts(vault_path), generation_method="M9 V1 vault folder count inspection")
    evidence.write_json("residual-risks.json", residual_risks, generation_method="M9 residual release-risk inspection")
    evidence.write_json("failures-pre-finalize.json", failures, generation_method="M9 checks before EvidenceRun.finalize")

    exit_code = 1 if failures else 0
    result = {
        "scenario": "m9_release_readiness",
        "release_status": "PASS" if exit_code == 0 else "FAIL",
        "release_matrix_path": str(evidence.path / "release-matrix.json"),
        "validated_run_reviews_path": str(evidence.path / "validated-run-reviews.json"),
        "compileall_exit_code": compileall["exit_code"],
        "required_manifest_count": len(REQUIRED_MANIFESTS),
        "validated_manifest_count": sum(1 for review in run_reviews if review.get("validator", {}).get("valid") is True),
        "residual_risks_release_blocking": residual_risks["release_blocking"],
        "claimed_components_note": "M9 aggregates existing real evidence only; it does not claim direct graph, LLM, or chat execution.",
    }
    manifest = evidence.finalize(exit_code=exit_code, result=result, failures=failures, repo_root=repo_root)
    print(json.dumps({"run_id": run_id, "manifest_path": str(evidence.path / "manifest.json"), "exit_code": exit_code}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
