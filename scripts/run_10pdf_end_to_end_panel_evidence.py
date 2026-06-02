#!/usr/bin/env python3
"""Run an N-PDF end-to-end Aily evidence pass for expert panel review.

Origin: Created by Codex lead agent on 2026-05-18.
Role: Evidence-runner source code only; not acceptance evidence by itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aily.business import BusinessPlanStore
from aily.config import SETTINGS
from aily.graph.db import GraphDB
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.orchestration.chat_store import ChatStore
from aily.orchestration.runs import WorkflowRunStore
from aily.research import ResearchStore
from aily.sessions.dikiwi_mind import DikiwiMind
from aily.source_store import SourceStore
from aily.verify.evidence import EvidenceRun, make_run_id, sha256_file, vault_counts
from aily.verify.llm_traffic import build_traffic_monitor
from aily.verify.obsidian_quality import QualityThresholds, score_vault_output
from aily.verify.chaos_quality import score_chaos_vault
from aily.verify.test_vaults import resolve_test_vault_path
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter
from aily.writer.moc_generator import generate_curated_mocs
from aily.writer.vault_layout import ensure_v1_vault_layout
from scripts.run_m7_business_plan_evidence import (
    _business_plan_vault_review,
    _ingest_foundation,
    _run_business_graph,
    _sanitize_packet,
    _source_ids_in_vault,
    _sqlite_summary,
)


DEFAULT_PDF_DIR = Path("/Users/luzi/aily_chaos/pdf")
PREVIOUS_PDF_NAMES = {
    "lp-01-tu-paper.pdf",
    "lp-02-wu-paper.pdf",
    "verification-2-tu-paper.pdf",
    "tb01-03-imperato-paper.pdf",
    "meloux-paper.pdf",
    "lowpower-9-meloux-pres-user.pdf",
    "paper16-hua.pdf",
    "paper10-heng.pdf",
    "publish-only-139-mehta.pdf",
    "publish-only-102-bisht.pdf",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an N-PDF Aily end-to-end panel evidence pass.")
    parser.add_argument("--pdf", action="append", type=Path, default=[], help="Selected PDF. Repeat --pdf-count times.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--pdf-count", type=int, default=10, help="Number of PDFs to select when --pdf is omitted.")
    parser.add_argument("--seed", type=int, default=20260518)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--runs-root", type=Path, default=SETTINGS.evidence_runs_dir)
    parser.add_argument("--vault-path", type=Path, default=None, help="Visible Obsidian vault path. Defaults to configured iCloud Documents Aily vault.")
    parser.add_argument("--research-model", default="mini", choices=["mini", "pro"])
    parser.add_argument("--max-results", type=int, default=3)
    return parser.parse_args()


def _select_pdfs(args: argparse.Namespace) -> list[Path]:
    expected_count = int(args.pdf_count)
    if expected_count < 1:
        raise ValueError("--pdf-count must be >= 1")
    if args.pdf:
        selected = [path.expanduser().resolve() for path in args.pdf]
        expected_count = len(selected)
    else:
        candidates = [
            path.resolve()
            for path in sorted(args.pdf_dir.expanduser().glob("*.pdf"))
            if path.name not in PREVIOUS_PDF_NAMES
        ]
        if len(candidates) < expected_count:
            raise ValueError(f"Expected at least {expected_count} candidate PDFs, got {len(candidates)}")
        rng = random.Random(args.seed)
        selected = rng.sample(candidates, expected_count)
    if len(selected) != expected_count:
        raise ValueError(f"Expected exactly {expected_count} PDFs, got {len(selected)}")
    missing = [str(path) for path in selected if not path.is_file() or path.suffix.lower() != ".pdf"]
    if missing:
        raise FileNotFoundError(f"Invalid selected PDFs: {missing}")
    if len({path.resolve() for path in selected}) != len(selected):
        raise ValueError("Selected PDFs must be distinct")
    return selected


async def _run() -> int:
    args = _parse_args()
    selected_pdfs = _select_pdfs(args)
    pdf_count = len(selected_pdfs)
    run_id = args.run_id or make_run_id(f"{pdf_count}pdf_panel_end_to_end")
    run_root = args.runs_root.expanduser().resolve() / run_id
    runtime_dir = run_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    vault_path = resolve_test_vault_path(run_id, args.vault_path)
    ensure_v1_vault_layout(vault_path)
    llm_trace_path = runtime_dir / "llm-calls.jsonl"
    graph_db_path = runtime_dir / "graph.db"
    source_store = SourceStore(runtime_dir / "source_store.sqlite", runtime_dir / "objects", runtime_dir / "canonical_markdown")
    workflow_store = WorkflowRunStore(runtime_dir / "workflow_runs.sqlite")
    chat_store = ChatStore(runtime_dir / "chat.sqlite")
    research_store = ResearchStore(runtime_dir / "research.sqlite")
    business_plan_store = BusinessPlanStore(runtime_dir / "business_plans.sqlite")
    graph_db = GraphDB(graph_db_path)
    source_contexts = {
        str(path): {
            "role": f"random_{pdf_count}pdf_internal_source",
            "selection_reason": f"deterministic random seed {args.seed}; selected {pdf_count} PDFs; excluded previous 10-PDF set",
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in selected_pdfs
    }
    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario=f"{pdf_count}pdf_panel_end_to_end",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=selected_pdfs,
        source_selector=f"deterministic_random_seed_{args.seed}_count_{pdf_count}",
        source_seed=str(args.seed),
        source_contexts=source_contexts,
        mocked=False,
        real_files=True,
        real_graph_db=True,
        real_vault=True,
        real_llm=True,
        real_chat=True,
        real_workflow=True,
        claimed_components=["files", "graph_db", "vault", "llm", "chat", "workflow"],
        command=sys.argv,
    )
    evidence.capture_before()
    events: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    result_payload: dict[str, Any] = {}

    await source_store.initialize()
    await workflow_store.initialize()
    await chat_store.initialize()
    await research_store.initialize()
    await business_plan_store.initialize()
    await graph_db.initialize()
    try:
        SETTINGS.llm_trace_log_path = llm_trace_path
        llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
        writer = DikiwiObsidianWriter(vault_path=vault_path, folder_prefix="", zettelkasten_only=True)
        mind = DikiwiMind(
            llm_client=llm_resolver("dikiwi"),
            llm_client_resolver=llm_resolver,
            graph_db=graph_db,
            enabled=SETTINGS.minds.dikiwi_enabled,
            dikiwi_obsidian_writer=writer,
        )
        import aily.sessions.dikiwi_mind as dikiwi_mind_module

        async def emit_event(event_type: str, **payload: Any) -> None:
            events.append({"type": event_type, **payload})

        dikiwi_mind_module.emit_ui_event = emit_event

        foundations: list[dict[str, Any]] = []
        source_ids: list[str] = []
        for pdf_path in selected_pdfs:
            payload = await _ingest_foundation(
                pdf_path=pdf_path,
                runtime_dir=runtime_dir,
                vault_path=vault_path,
                source_store=source_store,
                workflow_store=workflow_store,
                mind=mind,
                events=events,
            )
            foundations.append(payload)
            source_id = str(payload.get("source", {}).get("source_id") or "")
            if source_id:
                source_ids.append(source_id)
            graph_result = payload.get("foundation_graph_result", {})
            if graph_result.get("status") != "completed":
                failures.append({"check": "foundation_completed", "pdf": str(pdf_path), "result": graph_result})
            if not any(str(stage.get("stage")) == "KNOWLEDGE" and stage.get("success") for stage in graph_result.get("stage_results", [])):
                failures.append({"check": "foundation_knowledge_stage", "pdf": str(pdf_path), "stage_results": graph_result.get("stage_results", [])})

        business_payload = await _run_business_graph(
            runtime_dir=runtime_dir,
            vault_path=vault_path,
            primary_source_id=source_ids[0],
            all_source_ids=source_ids,
            primary_pdf_path=selected_pdfs[0],
            second_opinion_pdf_path=selected_pdfs[-1],
            source_store=source_store,
            research_store=research_store,
            business_plan_store=business_plan_store,
            graph_db=graph_db,
            chat_store=chat_store,
            workflow_store=workflow_store,
            mind=mind,
            events=events,
            research_model=args.research_model,
            max_results=args.max_results,
        )
        business_plans = await business_plan_store.list_business_plans(limit=20)
        business_plan_record = business_payload["captured_business_plan_dependency"].get("business_plan") or (business_plans[0] if business_plans else {})
        generated_mocs = generate_curated_mocs(vault_path)
        traffic_monitor = build_traffic_monitor([llm_trace_path], run_id=run_id, scenario=f"{pdf_count}pdf_panel_end_to_end")
        chaos_quality = score_chaos_vault(vault_path)
        obsidian_quality = score_vault_output(
            vault_path,
            thresholds=QualityThresholds(
                overall_score=90.0,
                dimension_floor=85.0,
                source_clarity=80.0,
                content_substance=85.0,
                report_substance=85.0,
                note_pass_rate=1.0,
                high_value_note_floor=85.0,
                max_index_link_count=0,
                max_index_link_note_ratio=0.0,
                max_generic_tag_share=0.15,
                max_unresolved_link_count=0,
                min_valid_connector_ratio=0.95,
                min_info_connector_coverage=0.75,
                min_info_pair_density=0.20,
            ),
        )
        business_plan_vault_review = _business_plan_vault_review(vault_path, business_plan_record)
        vault_source_reconciliation = _source_ids_in_vault(vault_path, source_ids)
        team_evaluations = await business_plan_store.list_team_evaluations(limit=50)
        research_jobs = await research_store.list_research_jobs(limit=50)
        workflow_runs = await workflow_store.list_runs(limit=100)

        if not traffic_monitor.get("passed"):
            failures.append({"check": "llm_traffic_monitor", "failures": traffic_monitor.get("failures", [])})
        if not chaos_quality.get("passed"):
            failures.append({"check": "chaos_quality", "failures": chaos_quality.get("scores", [])})
        if not obsidian_quality.get("passed"):
            failures.append({"check": "obsidian_quality", "failures": obsidian_quality.get("failures", [])})
        if not business_plan_vault_review.get("exists"):
            failures.append({"check": "business_plan_vault_review", "review": business_plan_vault_review})
        if not vault_source_reconciliation.get("all_present"):
            failures.append({"check": "all_source_ids_in_vault", "reconciliation": vault_source_reconciliation})

        result_payload = {
            "selected_pdfs": [str(path) for path in selected_pdfs],
            "selected_pdf_count": len(selected_pdfs),
            "source_ids": source_ids,
            "foundation_count": len(foundations),
            "business_plan_id": business_plan_record.get("business_plan_id", ""),
            "business_plan_obsidian_path": business_plan_record.get("obsidian_path", ""),
            "team_evaluation_count": len(team_evaluations),
            "research_job_count": len(research_jobs),
            "workflow_run_count": len(workflow_runs),
            "downstream_delivery_generation_removed": True,
            "generated_moc_count": len(generated_mocs),
            "traffic_monitor_passed": bool(traffic_monitor.get("passed")),
            "chaos_quality_passed": bool(chaos_quality.get("passed")),
            "obsidian_quality_passed": bool(obsidian_quality.get("passed")),
            "vault_counts": vault_counts(vault_path),
            "traffic_record_count": traffic_monitor.get("record_count", 0),
            "traffic_success_count": traffic_monitor.get("success_count", 0),
            "traffic_failure_count": traffic_monitor.get("failure_count", 0),
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
            "workflow_store_db_path": str(runtime_dir / "workflow_runs.sqlite"),
            "chat_store_db_path": str(runtime_dir / "chat.sqlite"),
            "research_store_db_path": str(runtime_dir / "research.sqlite"),
            "business_plan_store_db_path": str(runtime_dir / "business_plans.sqlite"),
        }
        evidence.write_json("selected-pdfs.json", {"seed": args.seed, "pdfs": source_contexts})
        evidence.write_json("foundation-results.json", {"source_ids": source_ids, "foundations": foundations})
        evidence.write_json("business-graph-result.json", business_payload)
        evidence.write_json("business-plan-record.json", business_plan_record)
        evidence.write_text("business-plan-markdown.md", business_plan_record.get("markdown", ""))
        evidence.write_json("business-plan-vault-review.json", business_plan_vault_review)
        evidence.write_json("team-evaluations.json", {"records": team_evaluations, "store": _sqlite_summary(runtime_dir / "business_plans.sqlite")})
        evidence.write_json("research-jobs.json", {"records": research_jobs, "secret_scan": [_sanitize_packet((job.get("packet") or {})) for job in research_jobs]})
        evidence.write_json("traffic-monitor.json", traffic_monitor)
        evidence.write_json("chaos-quality.json", chaos_quality)
        evidence.write_json("obsidian-quality-strict-visible.json", obsidian_quality)
        evidence.write_json("generated-mocs.json", {"paths": [str(path.relative_to(vault_path)) for path in generated_mocs]})
        evidence.write_json("source-id-vault-reconciliation.json", vault_source_reconciliation)
    except Exception as exc:
        failures.append({"check": "runner_exception", "error": str(exc)})
        result_payload = {"error": str(exc), "selected_pdfs": [str(path) for path in selected_pdfs]}
    finally:
        await source_store.close()
        await workflow_store.close()
        await chat_store.close()
        await research_store.close()
        await business_plan_store.close()
        await graph_db.close()

    exit_code = 0 if not failures else 1
    manifest = evidence.finalize(
        exit_code=exit_code,
        result=result_payload,
        failures=failures,
        llm_log_file=str(llm_trace_path),
        ui_events=events,
        repo_root=Path(__file__).resolve().parents[1],
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
