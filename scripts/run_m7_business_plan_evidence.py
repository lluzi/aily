#!/usr/bin/env python3
"""Generate M7 Gate 5 business-planning evidence.

Origin: Created by Codex independent M7 / Gate 5 evidence worker on 2026-05-17.
Role: Evidence-runner source code only; not acceptance evidence for M7 or Gate 5.

This runner is intended for independent execution. It uses real PDFs from
/Users/luzi/aily_chaos/pdf, isolated runtime databases under the evidence run
root, real SourceFoundationGraph intake, real DIKIWI foundation and triggered
I/W/I stages, real GraphDB and Obsidian vault writes, durable chat/workflow,
research, and business-plan stores, TavilyResearchService, and
BusinessPlanningGraph confirmation resume.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command

from aily.business import BusinessPlanStore, BusinessPlanSynthesizer
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
from aily.research import ResearchStore, TavilyResearchService
from aily.research.tavily_packets import build_second_opinion_packet
from aily.sessions.dikiwi_mind import DikiwiMind
from aily.source_store import SourceStore
from aily.verify.evidence import EvidenceRun, make_run_id, sha256_file, vault_counts
from aily.verify.test_vaults import resolve_test_vault_path
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter
from scripts.run_m5_graph_owned_iwi_evidence import (
    _checkpoint_summary,
    _emit,
    _iwi_result_payload,
    _load_jsonl,
    _run_payload,
    _select_graph_context,
    _selected_context,
    _selected_nodes_with_properties,
)


DEFAULT_PDF_DIR = Path("/Users/luzi/aily_chaos/pdf")
DEFAULT_PRIMARY_PDF = DEFAULT_PDF_DIR / "wb7-02-ayyagari-pres-user.pdf"
DEFAULT_SECOND_OPINION_PDF = DEFAULT_PDF_DIR / "lp-01-tu-paper.pdf"
IWI_STAGE_DIRS = ("04-Insight", "05-Wisdom", "06-Impact")
BUSINESS_PLAN_DIR = "09-Business-Plans"
EXPECTED_TEAMS = {"technical_innovation", "engineering_assessment", "commercial_feasibility"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Aily M7 Gate 5 business-plan evidence.")
    parser.add_argument("--primary-pdf", type=Path, default=None, help="Primary internal PDF source path.")
    parser.add_argument("--second-opinion-pdf", type=Path, default=None, help="Second-opinion/reference PDF path.")
    parser.add_argument("--run-id", default="", help="Optional evidence run id.")
    parser.add_argument("--runs-root", type=Path, default=SETTINGS.evidence_runs_dir, help="Evidence root directory.")
    parser.add_argument("--vault-path", type=Path, default=None, help="Visible Obsidian vault path. Defaults to configured iCloud Documents Aily vault.")
    parser.add_argument("--research-model", default="mini", choices=["mini", "pro"], help="Tavily research model/depth.")
    parser.add_argument("--max-results", type=int, default=3, help="Tavily max results.")
    return parser.parse_args()


def _select_pdfs(primary: Path | None, second: Path | None) -> tuple[Path, Path]:
    candidates = sorted(DEFAULT_PDF_DIR.expanduser().glob("*.pdf"))
    if not candidates:
        raise FileNotFoundError(f"No PDFs found under {DEFAULT_PDF_DIR}")
    primary_path = (primary or (DEFAULT_PRIMARY_PDF if DEFAULT_PRIMARY_PDF.exists() else candidates[0])).expanduser().resolve()
    second_path = (second or (DEFAULT_SECOND_OPINION_PDF if DEFAULT_SECOND_OPINION_PDF.exists() else candidates[min(1, len(candidates) - 1)])).expanduser().resolve()
    if primary_path == second_path:
        second_path = next((item.resolve() for item in candidates if item.resolve() != primary_path), second_path)
    if primary_path == second_path:
        raise ValueError("M7 requires two distinct real PDFs")
    for path in (primary_path, second_path):
        if not path.exists() or path.suffix.lower() != ".pdf":
            raise FileNotFoundError(f"PDF does not exist: {path}")
    return primary_path, second_path


def _sanitize_packet(packet: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(packet, ensure_ascii=False)
    secret_patterns = (
        re.compile(r"tvly-[A-Za-z0-9_-]{20,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"api[_-]?key['\"]?\s*[:=]\s*['\"][A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    )
    return {
        "has_forbidden_secret_marker": any(pattern.search(text) for pattern in secret_patterns),
        "packet": packet,
    }


def _sqlite_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"path": str(path), "exists": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0}
    if not path.exists():
        return summary
    try:
        with sqlite3.connect(path) as conn:
            tables = [str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            summary["tables"] = tables
            summary["row_counts"] = {table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}
    except Exception as exc:
        summary["inspection_error"] = str(exc)
    return summary


def _vault_file_set(vault_path: Path, directories: tuple[str, ...]) -> dict[str, set[str]]:
    return {
        directory: {str(path) for path in (vault_path / directory).rglob("*.md")} if (vault_path / directory).exists() else set()
        for directory in directories
    }


def _vault_samples(vault_path: Path, before: dict[str, set[str]], directories: tuple[str, ...], *, limit_per_dir: int = 5) -> dict[str, list[dict[str, Any]]]:
    samples: dict[str, list[dict[str, Any]]] = {}
    for directory in directories:
        root = vault_path / directory
        if not root.exists():
            samples[directory] = []
            continue
        files = sorted(root.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
        new_files = [path for path in files if str(path) not in before.get(directory, set())]
        selected = new_files[:limit_per_dir] or files[:limit_per_dir]
        records = []
        for path in selected:
            text = path.read_text(encoding="utf-8", errors="replace")
            records.append(
                {
                    "path": str(path),
                    "relative_path": str(path.relative_to(vault_path)),
                    "new_in_m7_window": str(path) not in before.get(directory, set()),
                    "size_bytes": path.stat().st_size,
                    "contains_frontmatter": text.startswith("---"),
                    "contains_source_lineage": "Source Knowledge Lineage" in text or "source_ids:" in text,
                    "contains_unresolved_risk_or_kill": "Risks And Kill Criteria" in text or "kill" in text.lower(),
                    "excerpt": text[:2400],
                }
            )
        samples[directory] = records
    return samples


def _source_ids_in_vault(vault_path: Path, source_ids: list[str]) -> dict[str, Any]:
    hits: dict[str, list[str]] = {source_id: [] for source_id in source_ids}
    for directory in ("01-Data", "02-Information", "03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact", "08-Evaluations", "09-Business-Plans"):
        root = vault_path / directory
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for source_id in source_ids:
                if source_id in text:
                    hits[source_id].append(str(path.relative_to(vault_path)))
    return {"source_ids": source_ids, "hits": hits, "all_present": all(hits.values())}


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
    upload_id = f"m7-primary-{pdf_path.stem}"
    source = await source_store.store_upload(
        upload_id=upload_id,
        filename=pdf_path.name,
        content_type="application/pdf",
        data=pdf_path.read_bytes(),
        metadata={
            "intake": "m7_gate5_business_plan",
            "origin_path": str(pdf_path),
            "origin_name": pdf_path.name,
            "source_sha256": sha256_file(pdf_path),
            "selection_reason": "M7 primary PDF for foundation knowledge and Gate 5 business planning",
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
            "source_kind": "m7_primary_pdf",
        },
    )
    claimed = await source_store.claim_next_source_job(worker_id="m7-business-plan-runner")
    if claimed is None:
        raise RuntimeError("M7 source job was not claimable")

    foundation_run = await workflow_store.create_run(
        workflow_kind="source_foundation",
        input_summary=f"M7 foundation PDF intake: {pdf_path.name}",
        metadata={"source_id": source["source_id"], "job_id": job["job_id"], "pdf_path": str(pdf_path), "runner": "m7_business_plan"},
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


async def _store_second_opinion(
    *,
    pdf_path: Path,
    source_store: SourceStore,
    research_store: ResearchStore,
    workflow_run_id: str,
    attached_to: str,
) -> dict[str, Any]:
    source = await source_store.store_upload(
        upload_id=f"m7-second-opinion-{pdf_path.stem}",
        filename=pdf_path.name,
        content_type="application/pdf",
        data=pdf_path.read_bytes(),
        metadata={
            "intake": "m7_second_opinion",
            "origin_path": str(pdf_path),
            "origin_name": pdf_path.name,
            "source_sha256": sha256_file(pdf_path),
            "authority": "external_user_provided_non_authoritative",
            "selection_reason": "M7 second-opinion/reference attachment",
        },
    )
    extracted = await ProcessingRouter().process(pdf_path.read_bytes(), filename=pdf_path.name, http_content_type="application/pdf")
    package = await CanonicalMarkdownConverter(source_store=source_store).convert_extracted(
        source_id=source["source_id"],
        extracted=extracted,
        fallback_title=pdf_path.stem,
        metadata={
            "created_from": "m7_second_opinion_reference",
            "authority": "external_user_provided_non_authoritative",
            "attached_to": attached_to,
            "workflow_run_id": workflow_run_id,
        },
    )
    await source_store.update_status(source["source_id"], "completed", {"second_opinion_canonical_markdown_path": package.package_path})
    reference = await research_store.create_second_opinion_reference(
        workflow_run_id=workflow_run_id,
        source_id=source["source_id"],
        document_type="pdf",
        note="External user-provided second opinion; non-authoritative until reconciled.",
        metadata={
            "attached_to": attached_to,
            "canonical_markdown_path": package.package_path,
            "origin_path": str(pdf_path),
            "authority": "external_user_provided_non_authoritative",
        },
    )
    packet_payload = build_second_opinion_packet(
        second_opinion_id=reference["second_opinion_id"],
        source_id=source["source_id"],
        attached_to=attached_to,
        document_type="pdf",
        markdown=package.markdown,
        user_note=reference["note"],
    )
    packet = await research_store.create_second_opinion_packet(
        second_opinion_id=reference["second_opinion_id"],
        source_id=source["source_id"],
        attached_to=attached_to,
        document_type="pdf",
        packet=packet_payload,
    )
    return {
        "source": await source_store.get_source(source["source_id"]),
        "canonical_markdown_package": await source_store.get_markdown_package(source["source_id"]),
        "reference": reference,
        "packet": packet,
        "markdown_excerpt": package.markdown[:2400],
    }


async def _run_business_graph(
    *,
    runtime_dir: Path,
    vault_path: Path,
    primary_source_id: str,
    all_source_ids: list[str] | None = None,
    primary_pdf_path: Path,
    second_opinion_pdf_path: Path,
    source_store: SourceStore,
    research_store: ResearchStore,
    business_plan_store: BusinessPlanStore,
    graph_db: GraphDB,
    chat_store: ChatStore,
    workflow_store: WorkflowRunStore,
    mind: DikiwiMind,
    events: list[dict[str, Any]],
    research_model: str,
    max_results: int,
) -> dict[str, Any]:
    effective_source_ids = [source_id for source_id in (all_source_ids or [primary_source_id]) if source_id]
    if primary_source_id not in effective_source_ids:
        effective_source_ids.insert(0, primary_source_id)
    graph_nodes = await graph_db.get_top_information_nodes_by_semantic_edge_count(limit=8)
    if len(graph_nodes) < 2:
        graph_nodes = await graph_db.get_nodes_by_type("information")
    labels = [str(node.get("label") or "").strip() for node in graph_nodes[:3] if str(node.get("label") or "").strip()]
    motive = (
        "Run Gate 5 Aily business planning with internal Knowledge, triggered Insight/Wisdom/Impact, "
        "external Tavily research, a non-authoritative second opinion, three specialist evaluations, "
        f"and a merged business plan. Focus on: {', '.join(labels[:3]) or primary_pdf_path.stem}."
    )
    workflow_run = await workflow_store.create_run(
        workflow_kind="business_planning",
        input_summary=motive[:240],
        metadata={
            "source_ids": effective_source_ids,
            "primary_pdf_path": str(primary_pdf_path),
            "second_opinion_pdf_path": str(second_opinion_pdf_path),
            "runner": "m7_business_plan",
            "email_delivery_enabled": False,
            "export_enabled": False,
        },
    )
    second_opinion = await _store_second_opinion(
        pdf_path=second_opinion_pdf_path,
        source_store=source_store,
        research_store=research_store,
        workflow_run_id=workflow_run.workflow_run_id,
        attached_to=workflow_run.workflow_run_id,
    )
    checkpoint_path = runtime_dir / "business_planning_langgraph_checkpoints.sqlite"
    captured_iwi: dict[str, Any] = {}
    captured_research: dict[str, Any] = {}
    captured_business_plan: dict[str, Any] = {}

    async def run_iwi_dependency(motive_arg: str, workflow_run_id: str, node_ids: list[str]) -> Any:
        captured_iwi.update({"called": True, "motive": motive_arg, "workflow_run_id": workflow_run_id, "node_ids": list(node_ids)})
        result = await mind.process_triggered_iwi(motive=motive_arg, workflow_run_id=workflow_run_id, node_ids=list(node_ids))
        captured_iwi["result"] = _iwi_result_payload(result)
        return result

    async def run_research_dependency(state: dict[str, Any]) -> dict[str, Any]:
        topics = list(state.get("topics") or [])
        topic = str((topics[0] or {}).get("label") if topics else "").strip() or primary_pdf_path.stem
        query = f"{topic} commercial feasibility technical innovation recent market evidence"
        service = TavilyResearchService(store=research_store)
        job = await service.create_and_run_packet(
            workflow_run_id=str(state.get("workflow_run_id") or workflow_run.workflow_run_id),
            topic=topic,
            trigger="m7_business_planning_graph_confirmed_research",
            query=query,
            topic_extraction_id=str(state.get("topic_extraction_id") or ""),
            model=research_model,
            internal_context=list(state.get("knowledge_context") or []),
            max_results=max_results,
        )
        captured_research.update({"called": True, "query": query, "job": job})
        return job

    async def run_business_plan_dependency(state: dict[str, Any]) -> dict[str, Any]:
        workflow_run_id = str(state.get("workflow_run_id") or workflow_run.workflow_run_id)
        workflow_plan_id = str(state.get("workflow_plan_id") or "")
        research_ids = [str(item) for item in state.get("research_ids", []) if str(item).strip()]
        research_jobs = []
        for research_id in research_ids:
            job = await research_store.get_research_job(research_id)
            if job:
                research_jobs.append(job)
        if not research_jobs:
            research_jobs = [item for item in await research_store.list_research_jobs(limit=20) if item.get("workflow_run_id") == workflow_run_id]
        second_packets = [business_payload for business_payload in [second_opinion["packet"]] if business_payload.get("attached_to") == workflow_run_id]
        synthesizer = BusinessPlanSynthesizer(store=business_plan_store, vault_path=vault_path)
        evaluations = await synthesizer.run_team_evaluations(
            workflow_run_id=workflow_run_id,
            workflow_plan_id=workflow_plan_id,
            motive=str(state.get("motive") or motive),
            knowledge_context=list(state.get("knowledge_context") or []),
            iwi_result=dict(state.get("iwi_result") or {}),
            research_jobs=research_jobs,
            second_opinion_packets=second_packets,
        )
        business_plan = await synthesizer.synthesize_business_plan(
            workflow_run_id=workflow_run_id,
            workflow_plan_id=workflow_plan_id,
            motive=str(state.get("motive") or motive),
            evaluations=evaluations,
            knowledge_context=list(state.get("knowledge_context") or []),
            research_jobs=research_jobs,
            second_opinion_packets=second_packets,
        )
        result = {"evaluations": evaluations, "business_plan": business_plan, "research_jobs": research_jobs, "second_opinion_packets": second_packets}
        captured_business_plan.update({"called": True, **result})
        return result

    dependencies = BusinessPlanningDependencies(
        chat_store=chat_store,
        workflow_run_store=workflow_store,
        select_context=lambda motive_arg, topics, source_ids: _select_graph_context(graph_db, primary_source_id, motive_arg, topics, source_ids or effective_source_ids),
        run_iwi=run_iwi_dependency,
        run_research=run_research_dependency,
        run_business_plan=run_business_plan_dependency,
        emit_event=lambda event_type, **payload: _emit(events, event_type, **payload),
    )
    state = {
        "workflow_run_id": workflow_run.workflow_run_id,
        "langgraph_thread_id": workflow_run.langgraph_thread_id,
        "workflow_kind": "business_planning",
        "status": "queued",
        "steps": [],
        "motive": motive,
        "source_ids": effective_source_ids,
        "research_required": True,
        "metadata": {
            "source_ids": effective_source_ids,
            "second_opinion_source_id": (second_opinion["source"] or {}).get("source_id", ""),
            "email_delivery_enabled": False,
            "export_enabled": False,
        },
    }
    config = {"configurable": {"thread_id": workflow_run.langgraph_thread_id}}
    async with async_sqlite_checkpointer(checkpoint_path) as checkpointer:
        graph = build_business_planning_graph(checkpointer, dependencies=dependencies)
        first_result = await graph.ainvoke(state, config)
        workflow_plan_id = str(first_result.get("workflow_plan_id") or "")
        before_plan = await chat_store.get_workflow_plan(workflow_plan_id) if workflow_plan_id else None
        resumed_result = await graph.ainvoke(
            Command(
                resume={
                    "approved": True,
                    "decided_by": "m7_business_plan_evidence",
                    "dispatch_iwi": True,
                    "dispatch_research": True,
                    "dispatch_business_plan": True,
                }
            ),
            config,
        )
    after_run = await workflow_store.get_run(workflow_run.workflow_run_id)
    after_plan_id = str(resumed_result.get("workflow_plan_id") or workflow_plan_id)
    after_plan = await chat_store.get_workflow_plan(after_plan_id) if after_plan_id else None
    topic_extraction = await chat_store.get_topic_extraction(str((after_plan or {}).get("topic_extraction_id") or ""))
    selected = _selected_context(after_plan or before_plan or {})
    selected_nodes = await _selected_nodes_with_properties(graph_db, [str(item["node_id"]) for item in selected])
    return {
        "motive": motive,
        "workflow_run_initial": _run_payload(workflow_run),
        "workflow_run_after": _run_payload(after_run) if after_run else None,
        "workflow_run_history": await workflow_store.list_run_history(workflow_run.workflow_run_id),
        "first_result": first_result,
        "resumed_result": resumed_result,
        "workflow_plan_before": before_plan,
        "workflow_plan_after": after_plan,
        "topic_extraction": topic_extraction,
        "checkpoint_db_path": str(checkpoint_path),
        "checkpoint_summary": _checkpoint_summary(checkpoint_path),
        "selected_context": selected,
        "selected_context_nodes": selected_nodes,
        "captured_iwi_dependency": captured_iwi,
        "captured_research_dependency": captured_research,
        "captured_business_plan_dependency": captured_business_plan,
        "second_opinion": second_opinion,
    }


def _business_plan_vault_review(vault_path: Path, business_plan: dict[str, Any]) -> dict[str, Any]:
    relative_path = str(business_plan.get("obsidian_path") or "")
    path = vault_path / relative_path if relative_path else Path()
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    source_ids = [str(item) for item in (business_plan.get("payload") or {}).get("source_ids", [])]
    evaluation_ids = [str(item) for item in (business_plan.get("payload") or {}).get("evaluation_ids", [])]
    research_ids = [str(item) for item in (business_plan.get("payload") or {}).get("research_ids", [])]
    second_opinion_ids = [str(item) for item in (business_plan.get("payload") or {}).get("second_opinion_ids", [])]
    return {
        "path": str(path) if relative_path else "",
        "relative_path": relative_path,
        "exists": path.exists() if relative_path else False,
        "under_09_business_plans": relative_path.startswith(f"{BUSINESS_PLAN_DIR}/"),
        "starts_with_frontmatter": text.startswith("---"),
        "contains_artifact_type_frontmatter": "artifact_type: business_plan" in text,
        "contains_workflow_run_id": str(business_plan.get("workflow_run_id") or "") in text,
        "contains_workflow_plan_id": str(business_plan.get("workflow_plan_id") or "") in text,
        "source_ids": source_ids,
        "source_links_present": {source_id: source_id in text for source_id in source_ids},
        "evaluation_links_present": {evaluation_id: evaluation_id in text for evaluation_id in evaluation_ids},
        "research_links_present": {research_id: research_id in text for research_id in research_ids},
        "second_opinion_links_present": {second_id: second_id in text for second_id in second_opinion_ids},
        "contains_source_lineage_section": "## Source Knowledge Lineage" in text,
        "contains_risks_and_kill_criteria_section": "## Risks And Kill Criteria" in text,
        "excerpt": text[:3000],
    }


async def _run() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    primary_pdf, second_opinion_pdf = _select_pdfs(args.primary_pdf, args.second_opinion_pdf)
    run_id = args.run_id or make_run_id("m7_business_plan")
    run_root = args.runs_root.expanduser().resolve() / run_id
    runtime_dir = run_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    llm_trace_path = runtime_dir / "llm-calls.jsonl"
    vault_path = resolve_test_vault_path(run_id, args.vault_path)
    graph_db_path = runtime_dir / "graph.db"
    source_store = SourceStore(runtime_dir / "source_store.sqlite", runtime_dir / "objects", runtime_dir / "canonical_markdown")
    workflow_store = WorkflowRunStore(runtime_dir / "workflow_runs.sqlite")
    chat_store = ChatStore(runtime_dir / "chat.sqlite")
    research_store = ResearchStore(runtime_dir / "research.sqlite")
    business_plan_store = BusinessPlanStore(runtime_dir / "business_plans.sqlite")
    graph_db = GraphDB(graph_db_path)
    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="m7_business_plan",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=[primary_pdf, second_opinion_pdf],
        source_selector="explicit_m7_pdfs" if args.primary_pdf or args.second_opinion_pdf else "default_pdf_dir_m7",
        source_contexts={
            str(primary_pdf): {"role": "primary_internal_source", "selection_reason": "M7 primary PDF for Aily Knowledge and business plan"},
            str(second_opinion_pdf): {"role": "second_opinion_reference_attachment", "selection_reason": "M7 external non-authoritative reference PDF"},
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
    await research_store.initialize()
    await business_plan_store.initialize()
    await graph_db.initialize()
    try:
        SETTINGS.llm_trace_log_path = llm_trace_path
        llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
        mind = DikiwiMind(
            llm_client=llm_resolver("dikiwi"),
            llm_client_resolver=llm_resolver,
            graph_db=graph_db,
            enabled=SETTINGS.minds.dikiwi_enabled,
            dikiwi_obsidian_writer=DikiwiObsidianWriter(vault_path=vault_path, folder_prefix="", zettelkasten_only=True),
        )
        import aily.sessions.dikiwi_mind as dikiwi_mind_module

        dikiwi_mind_module.emit_ui_event = lambda event_type, **payload: _emit(events, event_type, **payload)

        foundation_payload = await _ingest_foundation(
            pdf_path=primary_pdf,
            runtime_dir=runtime_dir,
            vault_path=vault_path,
            source_store=source_store,
            workflow_store=workflow_store,
            mind=mind,
            events=events,
        )
        primary_source_id = foundation_payload["source"]["source_id"]
        foundation_result = foundation_payload["foundation_graph_result"]
        if foundation_result.get("status") != "completed":
            failures.append({"check": "source_foundation_graph", "result": foundation_result})
        if not any(str(stage.get("stage")) == "KNOWLEDGE" and stage.get("success") for stage in foundation_result.get("stage_results", [])):
            failures.append({"check": "foundation_knowledge_stage", "stage_results": foundation_result.get("stage_results", [])})

        pre_business_counts = vault_counts(vault_path)
        pre_business_files = _vault_file_set(vault_path, (*IWI_STAGE_DIRS, "08-Evaluations", BUSINESS_PLAN_DIR))
        business_payload = await _run_business_graph(
            runtime_dir=runtime_dir,
            vault_path=vault_path,
            primary_source_id=primary_source_id,
            primary_pdf_path=primary_pdf,
            second_opinion_pdf_path=second_opinion_pdf,
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

        post_business_counts = vault_counts(vault_path)
        post_vault_samples = _vault_samples(vault_path, pre_business_files, (*IWI_STAGE_DIRS, "08-Evaluations", BUSINESS_PLAN_DIR))
        source_jobs = await source_store.list_source_jobs(limit=50)
        primary_source_record = await source_store.get_source(primary_source_id)
        primary_markdown_package = await source_store.get_markdown_package(primary_source_id)
        all_sources = await source_store.list_sources(limit=50)
        research_jobs = await research_store.list_research_jobs(limit=50)
        second_opinions = await research_store.list_second_opinions(limit=50)
        second_opinion_packets = [business_payload["second_opinion"]["packet"]]
        team_evaluations = await business_plan_store.list_team_evaluations(limit=50)
        business_plans = await business_plan_store.list_business_plans(limit=20)
        workflow_runs = await workflow_store.list_runs(limit=100)
        before_plan = business_payload["workflow_plan_before"] or {}
        after_plan = business_payload["workflow_plan_after"] or {}
        workflow_run_after = business_payload["workflow_run_after"] or {}
        research_job = business_payload["captured_research_dependency"].get("job") or {}
        research_packet = research_job.get("packet") or {}
        business_plan_record = (business_payload["captured_business_plan_dependency"].get("business_plan") or (business_plans[0] if business_plans else {}))
        business_plan_vault_review = _business_plan_vault_review(vault_path, business_plan_record)
        vault_source_reconciliation = _source_ids_in_vault(vault_path, [primary_source_id])
        llm_trace_records = _load_jsonl(llm_trace_path)
        selected_node_ids = [str(item.get("node_id")) for item in business_payload["selected_context"] if str(item.get("node_id") or "").strip()]
        captured_node_ids = [str(item) for item in business_payload["captured_iwi_dependency"].get("node_ids", [])]
        team_names = {str(item.get("team")) for item in team_evaluations}
        business_payload_record = business_plan_record.get("payload") or {}

        if not business_payload["first_result"].get("__interrupt__"):
            failures.append({"check": "business_graph_interrupt", "first_result": business_payload["first_result"]})
        if before_plan.get("status") != "awaiting_confirmation":
            failures.append({"check": "workflow_plan_before_status", "workflow_plan": before_plan})
        if after_plan.get("status") != "approved":
            failures.append({"check": "workflow_plan_after_status", "workflow_plan": after_plan})
        if workflow_run_after.get("status") != "completed" or workflow_run_after.get("current_node") != "business_plan_completed":
            failures.append({"check": "business_workflow_plan_completion", "workflow_run": workflow_run_after})
        if not (business_payload.get("topic_extraction") or {}).get("topics"):
            failures.append({"check": "topic_extraction_present", "topic_extraction": business_payload.get("topic_extraction")})
        if len(selected_node_ids) < 1:
            failures.append({"check": "graph_context_selected", "selected_context": business_payload["selected_context"]})
        if selected_node_ids != captured_node_ids:
            failures.append({"check": "selected_graph_node_ids_passed_to_iwi", "selected": selected_node_ids, "captured": captured_node_ids})
        if not business_payload["captured_iwi_dependency"].get("called"):
            failures.append({"check": "real_iwi_dependency_called", "captured": business_payload["captured_iwi_dependency"]})
        if not business_payload["captured_research_dependency"].get("called") or research_job.get("status") != "completed":
            failures.append({"check": "real_tavily_research_completed", "captured": business_payload["captured_research_dependency"]})
        if research_packet.get("provider") != "tavily" or not research_packet.get("truth_policy", {}).get("requires_reconciliation"):
            failures.append({"check": "tavily_packet_truth_policy", "packet": research_packet})
        if _sanitize_packet(research_packet)["has_forbidden_secret_marker"]:
            failures.append({"check": "no_tavily_or_bearer_secret_in_packet"})
        if business_payload["second_opinion"]["reference"].get("authority") != "external_user_provided_non_authoritative":
            failures.append({"check": "second_opinion_non_authoritative", "second_opinion": business_payload["second_opinion"]})
        if business_payload["second_opinion"]["packet"].get("packet", {}).get("truth_policy", {}).get("trusted_by_default") is not False:
            failures.append({"check": "second_opinion_truth_policy", "second_opinion": business_payload["second_opinion"]})
        if not EXPECTED_TEAMS.issubset(team_names) or len(team_evaluations) < 3:
            failures.append({"check": "three_team_evaluations", "teams": sorted(team_names), "evaluations": team_evaluations})
        if not business_plan_record or business_plan_record.get("status") != "completed":
            failures.append({"check": "business_plan_record_completed", "business_plan": business_plan_record})
        if not business_payload_record.get("source_ids") or primary_source_id not in business_payload_record.get("source_ids", []):
            failures.append({"check": "business_plan_source_lineage", "business_plan": business_plan_record})
        if not business_payload_record.get("unresolved_risks") or not business_payload_record.get("kill_criteria"):
            failures.append({"check": "business_plan_risks_and_kill_criteria", "payload": business_payload_record})
        if not business_plan_vault_review.get("under_09_business_plans") or not business_plan_vault_review.get("exists"):
            failures.append({"check": "business_plan_obsidian_path", "vault_review": business_plan_vault_review})
        if not business_plan_vault_review.get("starts_with_frontmatter") or not business_plan_vault_review.get("contains_source_lineage_section"):
            failures.append({"check": "business_plan_vault_frontmatter_lineage", "vault_review": business_plan_vault_review})
        if not business_plan_vault_review.get("contains_risks_and_kill_criteria_section"):
            failures.append({"check": "business_plan_vault_risks_kill_section", "vault_review": business_plan_vault_review})
        if not all(business_plan_vault_review.get("source_links_present", {}).values()):
            failures.append({"check": "business_plan_source_links_present", "vault_review": business_plan_vault_review})
        if not vault_source_reconciliation["all_present"]:
            failures.append({"check": "vault_reconciles_primary_source_id", "vault_source_reconciliation": vault_source_reconciliation})

        packet_summary = {
            "research_id": research_job.get("research_id", ""),
            "status": research_job.get("status", ""),
            "workflow_run_id": research_job.get("workflow_run_id", ""),
            "topic_extraction_id": research_job.get("topic_extraction_id", ""),
            "query": research_job.get("query", ""),
            "model": research_job.get("model", ""),
            "quota_checked_at": research_job.get("quota_checked_at", ""),
            "quota_allowed": research_job.get("quota_allowed", False),
            "search_depth": research_packet.get("search_depth", ""),
            "source_count": len(research_packet.get("sources", []) or []),
            "claim_count": len(research_packet.get("claims", []) or []),
            "truth_policy": research_packet.get("truth_policy", {}),
            "secret_scan": _sanitize_packet(research_packet),
        }
        result_summary = {
            "scenario": "m7_business_plan",
            "primary_source_id": primary_source_id,
            "second_opinion_source_id": str((business_payload["second_opinion"]["source"] or {}).get("source_id", "")),
            "business_workflow_run_id": workflow_run_after.get("workflow_run_id", ""),
            "workflow_plan_id": after_plan.get("workflow_plan_id", ""),
            "topic_extraction_id": after_plan.get("topic_extraction_id", ""),
            "selected_context_node_count": len(selected_node_ids),
            "research_id": research_job.get("research_id", ""),
            "research_status": research_job.get("status", ""),
            "team_evaluation_count": len(team_evaluations),
            "team_names": sorted(team_names),
            "business_plan_id": business_plan_record.get("business_plan_id", ""),
            "business_plan_obsidian_path": business_plan_record.get("obsidian_path", ""),
            "business_plan_has_source_lineage": bool(business_payload_record.get("source_ids")),
            "business_plan_has_unresolved_risks": bool(business_payload_record.get("unresolved_risks")),
            "business_plan_has_kill_criteria": bool(business_payload_record.get("kill_criteria")),
            "vault_business_plan_verified": bool(business_plan_vault_review.get("exists") and business_plan_vault_review.get("under_09_business_plans")),
            "llm_trace_record_count": len(llm_trace_records),
        }
        result_payload = {
            **result_summary,
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
            "research_store_db_path": str(runtime_dir / "research.sqlite"),
            "business_plan_store_db_path": str(runtime_dir / "business_plans.sqlite"),
            "workflow_runs_db_path": str(runtime_dir / "workflow_runs.sqlite"),
            "chat_store_db_path": str(runtime_dir / "chat.sqlite"),
            "graph_db_path": str(graph_db_path),
            "foundation_checkpoint_db_path": str(runtime_dir / "foundation_langgraph_checkpoints.sqlite"),
            "business_checkpoint_db_path": str(runtime_dir / "business_planning_langgraph_checkpoints.sqlite"),
            "llm_trace_path": str(llm_trace_path),
        }

        evidence.write_json("source-records.json", {"primary": primary_source_record, "sources": all_sources}, generation_method="SourceStore source records after M7 run")
        evidence.write_json("source-store-jobs.json", source_jobs, generation_method="SourceStore.list_source_jobs after M7 run")
        evidence.write_json("canonical-markdown-packages.json", {"primary": primary_markdown_package, "second_opinion": business_payload["second_opinion"]["canonical_markdown_package"]}, generation_method="SourceStore markdown packages after M7 run")
        evidence.write_json("second-opinion-reference.json", business_payload["second_opinion"]["reference"], generation_method="ResearchStore second-opinion reference")
        evidence.write_json("second-opinion-packet.json", business_payload["second_opinion"]["packet"], generation_method="ResearchStore second-opinion packet")
        evidence.write_json("research-jobs.json", research_jobs, generation_method="ResearchStore.list_research_jobs after M7 run")
        evidence.write_json("research-packet.json", research_job, generation_method="ResearchStore completed Tavily research packet")
        evidence.write_json("tavily-packet-summary.json", packet_summary, generation_method="M7 Tavily packet audit summary")
        evidence.write_json("foundation-result.json", foundation_payload, generation_method="SourceFoundationGraph result state")
        evidence.write_json("business-graph-first-result.json", business_payload["first_result"], generation_method="BusinessPlanningGraph first interrupted result")
        evidence.write_json("business-graph-resumed-result.json", business_payload["resumed_result"], generation_method="BusinessPlanningGraph resumed result with dispatch_iwi=true, dispatch_research=true, dispatch_business_plan=true")
        evidence.write_json("workflow-plan-before-confirmation.json", before_plan, generation_method="ChatStore workflow plan before BusinessPlanningGraph confirmation")
        evidence.write_json("workflow-plan-after-confirmation.json", after_plan, generation_method="ChatStore workflow plan after M7 confirmation")
        evidence.write_json("topic-extraction.json", business_payload["topic_extraction"], generation_method="ChatStore topic extraction for M7 BusinessPlanningGraph")
        evidence.write_json("workflow-runs.json", [_run_payload(run) for run in workflow_runs], generation_method="WorkflowRunStore.list_runs after M7 run")
        evidence.write_json("workflow-run-history.json", business_payload["workflow_run_history"], generation_method="WorkflowRunStore.list_run_history for M7 BusinessPlanningGraph")
        evidence.write_json("selected-context-nodes.json", {"context": business_payload["selected_context"], "nodes": business_payload["selected_context_nodes"]}, generation_method="GraphDB context selected for M7 BusinessPlanningGraph")
        evidence.write_json("team-evaluations.json", team_evaluations, generation_method="BusinessPlanStore.list_team_evaluations after M7 run")
        evidence.write_json("business-plan-record.json", business_plan_record, generation_method="BusinessPlanStore completed business plan record")
        evidence.write_text("business-plan-markdown.md", business_plan_record.get("markdown", ""), generation_method="BusinessPlanStore business plan markdown")
        evidence.write_json("business-plan-path.json", {"vault_path": str(vault_path), "business_plan_obsidian_path": business_plan_record.get("obsidian_path", ""), "review": business_plan_vault_review}, generation_method="Business plan Obsidian path and vault review")
        evidence.write_json("business-store-records.json", {"team_evaluations": team_evaluations, "business_plans": business_plans, "store_db": _sqlite_summary(runtime_dir / "business_plans.sqlite")}, generation_method="BusinessPlanStore records and SQLite inspection")
        evidence.write_json("second-opinion-records.json", {"references": second_opinions, "packets": second_opinion_packets}, generation_method="ResearchStore second-opinion records after M7 run")
        evidence.write_json("checkpoint-summary.json", {"foundation": foundation_payload["checkpoint_summary"], "business": business_payload["checkpoint_summary"]}, generation_method="SQLite inspection of LangGraph checkpoint DBs")
        evidence.write_json("vault-counts-and-samples.json", {"pre_business": pre_business_counts, "post_business": post_business_counts, "samples": post_vault_samples}, generation_method="Vault counts and samples after M7 run")
        evidence.write_json("vault-source-reconciliation.json", vault_source_reconciliation, generation_method="Obsidian vault source ID reconciliation")
        evidence.write_json("obsidian-plan-note-review.json", business_plan_vault_review, generation_method="M7 direct review of business plan note frontmatter and source links")
        evidence.write_jsonl("events.jsonl", events, generation_method="M7 captured runtime events")
        evidence.write_json("llm-trace-records.json", llm_trace_records, generation_method="LLM trace JSONL parsed from isolated runtime trace")
        evidence.write_json("result-summary.json", result_summary, generation_method="M7 result summary")
        exit_code = 0 if not failures else 1
    except Exception as exc:
        failures.append({"error": str(exc)})
        result_payload = {"error": str(exc)}
    finally:
        await graph_db.close()
        await business_plan_store.close()
        await research_store.close()
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
