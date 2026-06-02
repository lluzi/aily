#!/usr/bin/env python3
"""Generate M5 graph-owned triggered I/W/I evidence.

Origin: Created by Codex evidence harness worker on 2026-05-17.
Role: Evidence-runner source code only; not acceptance evidence for M5.

This runner is intended for independent execution. It uses a real PDF, real
SourceFoundationGraph intake, real DIKIWI foundation stages, real LLM routes,
real GraphDB, real Obsidian vault writes, durable chat/workflow stores,
AsyncSqliteSaver checkpoints, and BusinessPlanningGraph-owned triggered I/W/I.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command

from aily.config import SETTINGS
from aily.graph.db import GraphDB
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.orchestration.business_planning_graph import (
    BusinessPlanningDependencies,
    build_business_planning_graph,
)
from aily.orchestration.chat_store import ChatStore
from aily.orchestration.checkpoint import async_sqlite_checkpointer
from aily.orchestration.runs import WorkflowRunStore
from aily.orchestration.source_foundation_graph import (
    SourceFoundationDependencies,
    build_source_foundation_graph,
)
from aily.processing.canonical_markdown import CanonicalMarkdownConverter
from aily.processing.router import ProcessingRouter
from aily.sessions.dikiwi_mind import DikiwiMind
from aily.source_store import SourceStore
from aily.verify.evidence import EvidenceRun, make_run_id, sha256_file, vault_counts
from aily.verify.test_vaults import resolve_test_vault_path
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter


DEFAULT_PDF_DIR = Path("/Users/luzi/aily_chaos/pdf")
DEFAULT_PDF = DEFAULT_PDF_DIR / "wb7-02-ayyagari-pres-user.pdf"
IWI_STAGE_DIRS = ("04-Insight", "05-Wisdom", "06-Impact")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Aily M5 graph-owned I/W/I evidence.")
    parser.add_argument("--pdf", type=Path, default=None, help="Original PDF source path.")
    parser.add_argument("--run-id", default="", help="Optional evidence run id.")
    parser.add_argument("--runs-root", type=Path, default=SETTINGS.evidence_runs_dir, help="Evidence root directory.")
    parser.add_argument("--vault-path", type=Path, default=None, help="Visible Obsidian vault path. Defaults to configured iCloud Documents Aily vault.")
    return parser.parse_args()


def _select_pdf(cli_pdf: Path | None) -> Path:
    if cli_pdf is not None:
        return cli_pdf.expanduser().resolve()
    if DEFAULT_PDF.exists():
        return DEFAULT_PDF.resolve()
    candidates = sorted(DEFAULT_PDF_DIR.expanduser().glob("*.pdf"))
    if candidates:
        return candidates[0].resolve()
    raise FileNotFoundError(f"No default PDF found under {DEFAULT_PDF_DIR}")


def _run_payload(run: Any) -> dict[str, Any]:
    return {
        "workflow_run_id": run.workflow_run_id,
        "langgraph_thread_id": run.langgraph_thread_id,
        "workflow_kind": run.workflow_kind,
        "status": run.status,
        "current_node": run.current_node,
        "input_summary": run.input_summary,
        "metadata": run.metadata,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "completed_at": run.completed_at,
        "last_error": run.last_error,
    }


def _stage_name(stage: Any) -> str:
    return str(getattr(stage, "name", stage) or "")


def _iwi_result_payload(result: Any) -> dict[str, Any]:
    stage_results = []
    final_stage = ""
    for stage_result in getattr(result, "stage_results", []) or []:
        stage = _stage_name(getattr(stage_result, "stage", ""))
        if bool(getattr(stage_result, "success", False)):
            final_stage = stage
        stage_results.append(
            {
                "stage": stage,
                "success": bool(getattr(stage_result, "success", False)),
                "items_processed": int(getattr(stage_result, "items_processed", 0) or 0),
                "items_output": int(getattr(stage_result, "items_output", 0) or 0),
                "error_message": str(getattr(stage_result, "error_message", "") or ""),
                "data_keys": sorted(getattr(stage_result, "data", {}).keys()),
            }
        )
    return {
        "pipeline_id": str(getattr(result, "pipeline_id", "") or ""),
        "input_id": str(getattr(result, "input_id", "") or ""),
        "final_stage": final_stage,
        "stage_count": len(stage_results),
        "stage_results": stage_results,
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"unparseable_line": line[:1000]})
    return records


def _iwi_counts(counts: dict[str, int]) -> dict[str, int]:
    return {stage: int(counts.get(stage, 0)) for stage in IWI_STAGE_DIRS}


def _checkpoint_summary(path: Path) -> dict[str, Any]:
    exists = path.exists()
    summary: dict[str, Any] = {"path": str(path), "exists": exists, "size_bytes": path.stat().st_size if exists else 0}
    if not exists:
        return summary
    try:
        with sqlite3.connect(path) as conn:
            table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            tables = [str(row[0]) for row in table_rows]
            summary["tables"] = tables
            summary["row_counts"] = {
                table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in tables
            }
    except Exception as exc:
        summary["inspection_error"] = str(exc)
    return summary


def _vault_file_set(vault_path: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for stage_dir in IWI_STAGE_DIRS:
        directory = vault_path / stage_dir
        result[stage_dir] = {str(path) for path in directory.rglob("*.md")} if directory.exists() else set()
    return result


def _vault_samples(vault_path: Path, before: dict[str, set[str]], *, limit_per_stage: int = 5) -> dict[str, list[dict[str, Any]]]:
    samples: dict[str, list[dict[str, Any]]] = {}
    for stage_dir in IWI_STAGE_DIRS:
        directory = vault_path / stage_dir
        if not directory.exists():
            samples[stage_dir] = []
            continue
        files = sorted(directory.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
        new_files = [path for path in files if str(path) not in before.get(stage_dir, set())]
        selected = new_files[:limit_per_stage] or files[:limit_per_stage]
        stage_samples = []
        for path in selected:
            text = path.read_text(encoding="utf-8", errors="replace")
            stage_samples.append(
                {
                    "path": str(path),
                    "relative_path": str(path.relative_to(vault_path)),
                    "new_in_business_iwi_window": str(path) not in before.get(stage_dir, set()),
                    "size_bytes": path.stat().st_size,
                    "contains_source_knowledge": "## Source Knowledge" in text or "## Grounded In" in text or "## Based On" in text,
                    "contains_graph_provenance": "Graph Provenance" in text or "graph_provenance" in text or "Graph Center" in text,
                    "excerpt": text[:1600],
                }
            )
        samples[stage_dir] = stage_samples
    return samples


def _selected_context(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in plan.get("knowledge_context", [])
        if isinstance(item, dict)
        and item.get("context_type") == "graph_information_node"
        and str(item.get("node_id") or "").strip()
    ]


async def _emit(events: list[dict[str, Any]], event_type: str, **payload: Any) -> None:
    events.append({"type": event_type, **payload})


async def _select_graph_context(
    graph_db: GraphDB,
    source_id: str,
    motive: str,
    topics: list[dict[str, Any]],
    source_ids: list[str],
) -> list[dict[str, Any]]:
    terms = [str(topic.get("label") or "").strip() for topic in topics if str(topic.get("label") or "").strip()]
    terms.extend(term for term in motive.replace(",", " ").replace(".", " ").split() if len(term) > 4)
    selected = await graph_db.search_information_nodes(terms[:8], limit=8)
    if len(selected) < 2:
        selected = await graph_db.get_top_information_nodes_by_semantic_edge_count(limit=8)
    if len(selected) < 2:
        selected = await graph_db.get_nodes_by_type("information")
    return [
        {
            "context_type": "graph_information_node",
            "node_id": node.get("id"),
            "label": node.get("label"),
            "source": node.get("source"),
            "source_id": source_id,
            "source_ids": source_ids or [source_id],
            "source_paths": [f"source_id:{source_id}"],
            "selection_reason": "m5_graph_owned_iwi_context_selection",
            "role": "business_planning_graph_owned_iwi_context",
        }
        for node in selected[:8]
    ]


async def _selected_nodes_with_properties(graph_db: GraphDB, node_ids: list[str]) -> list[dict[str, Any]]:
    nodes = await graph_db.get_nodes_by_ids(node_ids)
    enriched = []
    for node in nodes:
        props = await graph_db.get_node_properties(str(node["id"]))
        enriched.append({**node, "properties": props})
    return enriched


async def _ingest_foundation(
    *,
    pdf_path: Path,
    runtime_dir: Path,
    vault_path: Path,
    source_store: SourceStore,
    workflow_store: WorkflowRunStore,
    mind: DikiwiMind,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    upload_id = f"m5-{pdf_path.stem}"
    source = await source_store.store_upload(
        upload_id=upload_id,
        filename=pdf_path.name,
        content_type="application/pdf",
        data=pdf_path.read_bytes(),
        metadata={
            "intake": "m5_graph_owned_iwi",
            "origin_path": str(pdf_path),
            "origin_name": pdf_path.name,
            "source_sha256": sha256_file(pdf_path),
            "selection_reason": "M5 representative original PDF for foundation knowledge and graph-owned I/W/I",
        },
    )
    job = await source_store.enqueue_source_job(
        source_id=source["source_id"],
        job_type="process_upload_source",
        payload={
            "upload_id": upload_id,
            "filename": pdf_path.name,
            "content_type": "application/pdf",
            "origin_path": str(pdf_path),
            "source_kind": "m5_pdf",
        },
    )
    claimed = await source_store.claim_next_source_job(worker_id="m5-graph-owned-iwi-runner")
    if claimed is None:
        raise RuntimeError("M5 source job was not claimable")

    foundation_run = await workflow_store.create_run(
        workflow_kind="source_foundation",
        input_summary=f"M5 foundation PDF intake: {pdf_path.name}",
        metadata={"source_id": source["source_id"], "job_id": job["job_id"], "pdf_path": str(pdf_path)},
    )
    state = {
        "workflow_run_id": foundation_run.workflow_run_id,
        "langgraph_thread_id": foundation_run.langgraph_thread_id,
        "workflow_kind": "source_foundation",
        "status": "queued",
        "steps": [],
        "source_id": source["source_id"],
        "job_id": job["job_id"],
        "job_type": "process_upload_source",
        "metadata": {
            "source_id": source["source_id"],
            "job_id": job["job_id"],
            "job_type": "process_upload_source",
            "job_payload": {
                **dict(claimed.get("payload") or {}),
                "source_id": source["source_id"],
                "job_id": job["job_id"],
                "job_type": "process_upload_source",
            },
        },
    }
    dependencies = SourceFoundationDependencies(
        source_store=source_store,
        processing_router_factory=lambda: ProcessingRouter(),
        canonical_markdown_converter_factory=lambda: CanonicalMarkdownConverter(source_store=source_store),
        dikiwi_ingestion=mind.process_input_foundation,
        emit_event=lambda event_type, **payload: _emit(events, event_type, **payload),
        workflow_run_store=workflow_store,
        vault_path=vault_path,
    )
    checkpoint_path = runtime_dir / "foundation_langgraph_checkpoints.sqlite"
    async with async_sqlite_checkpointer(checkpoint_path) as checkpointer:
        graph = build_source_foundation_graph(checkpointer, dependencies=dependencies)
        graph_result = await graph.ainvoke(state, {"configurable": {"thread_id": foundation_run.langgraph_thread_id}})

    if graph_result.get("status") == "completed":
        await source_store.complete_source_job(str(job["job_id"]))
    else:
        await source_store.fail_source_job(str(job["job_id"]), error=str(graph_result.get("status") or "failed"))

    return {
        "source": source,
        "source_job": job,
        "claimed_source_job": claimed,
        "foundation_run": _run_payload(foundation_run),
        "foundation_graph_result": graph_result,
        "checkpoint_db_path": str(checkpoint_path),
        "checkpoint_summary": _checkpoint_summary(checkpoint_path),
    }


async def _run_business_graph(
    *,
    runtime_dir: Path,
    source_id: str,
    pdf_path: Path,
    graph_db: GraphDB,
    chat_store: ChatStore,
    workflow_store: WorkflowRunStore,
    mind: DikiwiMind,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    graph_nodes = await graph_db.get_top_information_nodes_by_semantic_edge_count(limit=8)
    if len(graph_nodes) < 2:
        graph_nodes = await graph_db.get_nodes_by_type("information")
    labels = [str(node.get("label") or "").strip() for node in graph_nodes[:3] if str(node.get("label") or "").strip()]
    motive = (
        "Run graph-owned Insight, Wisdom, and Impact for a business planning workflow. "
        f"Focus on: {', '.join(labels[:3]) or pdf_path.stem}."
    )
    workflow_run = await workflow_store.create_run(
        workflow_kind="business_planning",
        input_summary=motive[:240],
        metadata={"source_ids": [source_id], "pdf_path": str(pdf_path), "runner": "m5_graph_owned_iwi"},
    )
    checkpoint_path = runtime_dir / "business_planning_langgraph_checkpoints.sqlite"
    captured_iwi: dict[str, Any] = {}

    async def run_iwi_dependency(motive_arg: str, workflow_run_id: str, node_ids: list[str]) -> Any:
        captured_iwi["called"] = True
        captured_iwi["motive"] = motive_arg
        captured_iwi["workflow_run_id"] = workflow_run_id
        captured_iwi["node_ids"] = list(node_ids)
        result = await mind.process_triggered_iwi(
            motive=motive_arg,
            workflow_run_id=workflow_run_id,
            node_ids=list(node_ids),
        )
        captured_iwi["result"] = _iwi_result_payload(result)
        return result

    dependencies = BusinessPlanningDependencies(
        chat_store=chat_store,
        workflow_run_store=workflow_store,
        select_context=lambda motive_arg, topics, source_ids: _select_graph_context(
            graph_db,
            source_id,
            motive_arg,
            topics,
            source_ids,
        ),
        run_iwi=run_iwi_dependency,
        emit_event=lambda event_type, **payload: _emit(events, event_type, **payload),
    )
    state = {
        "workflow_run_id": workflow_run.workflow_run_id,
        "langgraph_thread_id": workflow_run.langgraph_thread_id,
        "workflow_kind": "business_planning",
        "status": "queued",
        "steps": [],
        "motive": motive,
        "source_ids": [source_id],
        "research_required": False,
        "metadata": {"source_ids": [source_id], "pdf_path": str(pdf_path)},
    }
    config = {"configurable": {"thread_id": workflow_run.langgraph_thread_id}}
    async with async_sqlite_checkpointer(checkpoint_path) as checkpointer:
        graph = build_business_planning_graph(checkpointer, dependencies=dependencies)
        first_result = await graph.ainvoke(state, config)
        workflow_plan_id = str(first_result.get("workflow_plan_id") or "")
        before_plan = await chat_store.get_workflow_plan(workflow_plan_id) if workflow_plan_id else None
        resumed_result = await graph.ainvoke(
            Command(resume={"approved": True, "decided_by": "m5_graph_owned_iwi_evidence", "dispatch_iwi": True}),
            config,
        )
    after_run = await workflow_store.get_run(workflow_run.workflow_run_id)
    after_plan_id = str(resumed_result.get("workflow_plan_id") or workflow_plan_id)
    after_plan = await chat_store.get_workflow_plan(after_plan_id) if after_plan_id else None
    history = await workflow_store.list_run_history(workflow_run.workflow_run_id)
    selected = _selected_context(after_plan or before_plan or {})
    selected_node_ids = [str(item["node_id"]) for item in selected]
    selected_nodes = await _selected_nodes_with_properties(graph_db, selected_node_ids)
    return {
        "motive": motive,
        "workflow_run_initial": _run_payload(workflow_run),
        "workflow_run_after": _run_payload(after_run) if after_run else None,
        "workflow_run_history": history,
        "first_result": first_result,
        "resumed_result": resumed_result,
        "workflow_plan_before": before_plan,
        "workflow_plan_after": after_plan,
        "checkpoint_db_path": str(checkpoint_path),
        "checkpoint_summary": _checkpoint_summary(checkpoint_path),
        "selected_context": selected,
        "selected_context_nodes": selected_nodes,
        "captured_iwi_dependency": captured_iwi,
    }


async def _run() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    pdf_path = _select_pdf(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(f"M5 PDF does not exist: {pdf_path}")

    run_id = args.run_id or make_run_id("m5_graph_owned_iwi")
    run_root = args.runs_root.expanduser().resolve() / run_id
    runtime_dir = run_root / "runtime"
    llm_trace_path = runtime_dir / "llm-calls.jsonl"
    vault_path = resolve_test_vault_path(run_id, args.vault_path)
    graph_db_path = runtime_dir / "graph.db"
    source_store = SourceStore(runtime_dir / "source_store.sqlite", runtime_dir / "objects", runtime_dir / "canonical_markdown")
    workflow_store = WorkflowRunStore(runtime_dir / "workflow_runs.sqlite")
    chat_store = ChatStore(runtime_dir / "chat.sqlite")
    graph_db = GraphDB(graph_db_path)

    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="m5_graph_owned_iwi",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=[pdf_path],
        source_selector="explicit_m5_pdf" if args.pdf else "default_pdf_dir_m5",
        source_contexts={
            str(pdf_path): {
                "role": "m5_graph_owned_iwi_pdf",
                "selection_reason": "M5 representative original PDF for graph-owned triggered I/W/I",
            }
        },
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
    exit_code = 1

    await source_store.initialize()
    await workflow_store.initialize()
    await chat_store.initialize()
    await graph_db.initialize()
    try:
        SETTINGS.llm_trace_log_path = llm_trace_path
        llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
        dikiwi_writer = DikiwiObsidianWriter(vault_path=vault_path, folder_prefix="", zettelkasten_only=True)
        mind = DikiwiMind(
            llm_client=llm_resolver("dikiwi"),
            llm_client_resolver=llm_resolver,
            graph_db=graph_db,
            enabled=SETTINGS.minds.dikiwi_enabled,
            dikiwi_obsidian_writer=dikiwi_writer,
        )

        import aily.sessions.dikiwi_mind as dikiwi_mind_module

        dikiwi_mind_module.emit_ui_event = lambda event_type, **payload: _emit(events, event_type, **payload)

        foundation_payload = await _ingest_foundation(
            pdf_path=pdf_path,
            runtime_dir=runtime_dir,
            vault_path=vault_path,
            source_store=source_store,
            workflow_store=workflow_store,
            mind=mind,
            events=events,
        )
        source_id = foundation_payload["source"]["source_id"]
        foundation_result = foundation_payload["foundation_graph_result"]
        if foundation_result.get("status") != "completed":
            failures.append({"check": "source_foundation_graph", "result": foundation_result})
        if not any(str(stage.get("stage")) == "KNOWLEDGE" and stage.get("success") for stage in foundation_result.get("stage_results", [])):
            failures.append({"check": "foundation_knowledge_stage", "stage_results": foundation_result.get("stage_results", [])})

        pre_business_counts = vault_counts(vault_path)
        pre_business_files = _vault_file_set(vault_path)
        business_payload = await _run_business_graph(
            runtime_dir=runtime_dir,
            source_id=source_id,
            pdf_path=pdf_path,
            graph_db=graph_db,
            chat_store=chat_store,
            workflow_store=workflow_store,
            mind=mind,
            events=events,
        )
        post_business_counts = vault_counts(vault_path)
        post_iwi_samples = _vault_samples(vault_path, pre_business_files)
        pre_post_iwi_delta = {
            stage: _iwi_counts(post_business_counts)[stage] - _iwi_counts(pre_business_counts)[stage]
            for stage in IWI_STAGE_DIRS
        }

        first_result = business_payload["first_result"]
        resumed_result = business_payload["resumed_result"]
        before_plan = business_payload["workflow_plan_before"] or {}
        after_plan = business_payload["workflow_plan_after"] or {}
        workflow_run_after = business_payload["workflow_run_after"] or {}
        workflow_runs = await workflow_store.list_runs(limit=100)
        run_kinds = [run.workflow_kind for run in workflow_runs]
        workflow_history = business_payload["workflow_run_history"]
        source_jobs = await source_store.list_source_jobs(limit=20)
        source_record = await source_store.get_source(source_id)
        markdown_package = await source_store.get_markdown_package(source_id)
        chat_thread = await chat_store.get_thread(after_plan.get("chat_thread_id", ""))
        llm_trace_records = _load_jsonl(llm_trace_path)

        interrupt_payload = first_result.get("__interrupt__")
        if not interrupt_payload:
            failures.append({"check": "business_graph_interrupt", "first_result": first_result})
        if before_plan.get("status") != "awaiting_confirmation":
            failures.append({"check": "workflow_plan_before_status", "workflow_plan": before_plan})
        if after_plan.get("status") != "approved":
            failures.append({"check": "workflow_plan_after_status", "workflow_plan": after_plan})
        if workflow_run_after.get("workflow_kind") != "business_planning":
            failures.append({"check": "business_workflow_kind", "workflow_run": workflow_run_after})
        if workflow_run_after.get("status") != "completed":
            failures.append({"check": "business_workflow_completion", "workflow_run": workflow_run_after})
        if workflow_run_after.get("current_node") != "IMPACT":
            failures.append({"check": "business_workflow_current_node_impact", "workflow_run": workflow_run_after})
        if workflow_run_after.get("metadata", {}).get("final_stage") != "IMPACT":
            failures.append({"check": "business_workflow_final_stage", "workflow_run": workflow_run_after})
        if business_payload["workflow_run_initial"]["workflow_run_id"] != workflow_run_after.get("workflow_run_id"):
            failures.append({"check": "workflow_run_id_preserved", "payload": business_payload})
        if business_payload["workflow_run_initial"]["langgraph_thread_id"] != workflow_run_after.get("langgraph_thread_id"):
            failures.append({"check": "langgraph_thread_id_preserved", "payload": business_payload})
        if "triggered_iwi" in run_kinds:
            failures.append({"check": "no_separate_triggered_iwi_workflow_run", "workflow_kinds": run_kinds})
        if not business_payload["captured_iwi_dependency"].get("called"):
            failures.append({"check": "real_iwi_dependency_called", "captured": business_payload["captured_iwi_dependency"]})
        if business_payload["captured_iwi_dependency"].get("workflow_run_id") != workflow_run_after.get("workflow_run_id"):
            failures.append({"check": "iwi_dependency_same_workflow_run", "captured": business_payload["captured_iwi_dependency"], "workflow_run": workflow_run_after})
        selected_node_ids = [str(item.get("node_id")) for item in business_payload["selected_context"] if str(item.get("node_id") or "").strip()]
        captured_node_ids = [str(item) for item in business_payload["captured_iwi_dependency"].get("node_ids", [])]
        if not selected_node_ids or selected_node_ids != captured_node_ids:
            failures.append({"check": "selected_graph_node_ids_passed_to_iwi", "selected": selected_node_ids, "captured": captured_node_ids})
        if any(delta <= 0 for delta in pre_post_iwi_delta.values()):
            failures.append({"check": "iwi_vault_notes_created", "delta": pre_post_iwi_delta})
        if not all(samples and any(item["contains_graph_provenance"] for item in samples) for samples in post_iwi_samples.values()):
            failures.append({"check": "iwi_notes_cite_graph_anchors", "samples": post_iwi_samples})
        if not all(samples and any(item["contains_source_knowledge"] for item in samples) for samples in post_iwi_samples.values()):
            failures.append({"check": "iwi_notes_cite_knowledge_anchors", "samples": post_iwi_samples})
        if not business_payload["checkpoint_summary"].get("exists"):
            failures.append({"check": "business_checkpoint_exists", "checkpoint": business_payload["checkpoint_summary"]})
        if (source_record or {}).get("status") != "completed":
            failures.append({"check": "source_ingestion_status_remains_completed", "source_record": source_record})

        result_summary = {
            "scenario": "m5_graph_owned_iwi",
            "source_id": source_id,
            "foundation_status": foundation_result.get("status"),
            "business_workflow_run_id": workflow_run_after.get("workflow_run_id", ""),
            "business_langgraph_thread_id": workflow_run_after.get("langgraph_thread_id", ""),
            "business_workflow_status": workflow_run_after.get("status", ""),
            "business_current_node": workflow_run_after.get("current_node", ""),
            "business_final_stage": workflow_run_after.get("metadata", {}).get("final_stage", ""),
            "workflow_plan_id": after_plan.get("workflow_plan_id", ""),
            "workflow_plan_before_status": before_plan.get("status", ""),
            "workflow_plan_after_status": after_plan.get("status", ""),
            "interrupt_seen": bool(interrupt_payload),
            "separate_triggered_iwi_run_count": run_kinds.count("triggered_iwi"),
            "selected_context_node_count": len(selected_node_ids),
            "selected_context_node_ids": selected_node_ids,
            "captured_iwi_dependency": business_payload["captured_iwi_dependency"],
            "pre_post_business_iwi_delta": pre_post_iwi_delta,
            "llm_trace_record_count": len(llm_trace_records),
            "source_status_after_iwi": (source_record or {}).get("status", ""),
        }
        result_payload = {
            **result_summary,
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
            "workflow_runs_db_path": str(runtime_dir / "workflow_runs.sqlite"),
            "chat_store_db_path": str(runtime_dir / "chat.sqlite"),
            "graph_db_path": str(graph_db_path),
            "foundation_checkpoint_db_path": str(runtime_dir / "foundation_langgraph_checkpoints.sqlite"),
            "business_checkpoint_db_path": str(runtime_dir / "business_planning_langgraph_checkpoints.sqlite"),
            "llm_trace_path": str(llm_trace_path),
        }

        evidence.write_json("source-record.json", source_record, generation_method="SourceStore.get_source after M5 run")
        evidence.write_json("source-store-jobs.json", source_jobs, generation_method="SourceStore.list_source_jobs after M5 run")
        evidence.write_json("canonical-markdown-package.json", markdown_package, generation_method="SourceStore.get_markdown_package after M5 run")
        evidence.write_json("foundation-result.json", foundation_payload, generation_method="SourceFoundationGraph result state")
        evidence.write_json("business-graph-first-result.json", first_result, generation_method="BusinessPlanningGraph first interrupted result")
        evidence.write_json("business-graph-resumed-result.json", resumed_result, generation_method="BusinessPlanningGraph resumed result via Command(resume={'approved': True, 'dispatch_iwi': True})")
        evidence.write_json("chat-records.json", chat_thread, generation_method="ChatStore.get_thread after M5 run")
        evidence.write_json("workflow-plan-before-confirmation.json", before_plan, generation_method="ChatStore workflow plan before BusinessPlanningGraph confirmation")
        evidence.write_json("workflow-plan-after-confirmation.json", after_plan, generation_method="ChatStore workflow plan after BusinessPlanningGraph I/W/I resume")
        evidence.write_json("workflow-runs.json", [_run_payload(run) for run in workflow_runs], generation_method="WorkflowRunStore.list_runs after M5 run")
        evidence.write_json("workflow-run-history.json", workflow_history, generation_method="WorkflowRunStore.list_run_history for graph-owned BusinessPlanningGraph I/W/I")
        evidence.write_json("checkpoint-summary.json", {"foundation": foundation_payload["checkpoint_summary"], "business": business_payload["checkpoint_summary"]}, generation_method="SQLite inspection of AsyncSqliteSaver checkpoint DBs")
        evidence.write_json("selected-graph-nodes.json", business_payload["selected_context_nodes"], generation_method="GraphDB selected nodes and properties from BusinessPlanningGraph workflow plan context")
        evidence.write_json("pre-post-vault-counts.json", {"pre_business": pre_business_counts, "post_business": post_business_counts, "iwi_delta": pre_post_iwi_delta}, generation_method="Vault counts before and after graph-owned BusinessPlanningGraph I/W/I")
        evidence.write_json("post-iwi-vault-sample-paths.json", post_iwi_samples, generation_method="Recent/new I/W/I vault notes after graph-owned BusinessPlanningGraph I/W/I")
        evidence.write_jsonl("events.jsonl", events, generation_method="M5 captured runtime events")
        evidence.write_json("llm-trace-records.json", llm_trace_records, generation_method="LLM trace JSONL parsed from isolated runtime trace")
        evidence.write_json("result-summary.json", result_summary, generation_method="M5 result summary")

        exit_code = 0 if not failures else 1
    except Exception as exc:
        failures.append({"error": str(exc)})
        result_payload = {"error": str(exc)}
    finally:
        await graph_db.close()
        await chat_store.close()
        await workflow_store.close()
        await source_store.close()

    manifest = evidence.finalize(
        exit_code=exit_code if not failures else 1,
        result=result_payload,
        failures=failures,
        ui_events=events,
        llm_log_file=str(llm_trace_path),
        stderr_text="" if not failures else json.dumps(failures, ensure_ascii=False),
        repo_root=repo_root,
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
