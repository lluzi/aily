#!/usr/bin/env python3
"""Generate M6 research and second-opinion evidence.

Origin: Created by Codex independent M6 evidence worker on 2026-05-17.
Role: Evidence-runner source code only; not acceptance evidence for M6.

This runner is intended for independent execution. It uses two real PDFs from
/Users/luzi/aily_chaos/pdf, isolated runtime databases under the evidence run
root, real SourceFoundationGraph intake, real DIKIWI foundation stages, real
GraphDB and vault writes, real chat/workflow/research stores, LangGraph
checkpoints, and TavilyResearchService.
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
    _ingest_foundation,
    _load_jsonl,
    _run_payload,
    _select_graph_context,
    _selected_context,
    _selected_nodes_with_properties,
)


DEFAULT_PDF_DIR = Path("/Users/luzi/aily_chaos/pdf")
DEFAULT_PRIMARY_PDF = DEFAULT_PDF_DIR / "wb7-02-ayyagari-pres-user.pdf"
DEFAULT_SECOND_OPINION_PDF = DEFAULT_PDF_DIR / "lp-01-tu-paper.pdf"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Aily M6 research/second-opinion evidence.")
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
    second_path = (second or (DEFAULT_SECOND_OPINION_PDF if DEFAULT_SECOND_OPINION_PDF.exists() else candidates[1])).expanduser().resolve()
    if primary_path == second_path:
        second_path = next((item.resolve() for item in candidates if item.resolve() != primary_path), second_path)
    if primary_path == second_path:
        raise ValueError("M6 requires two distinct real PDFs")
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


def _source_ids_in_vault(vault_path: Path, source_ids: list[str]) -> dict[str, Any]:
    hits: dict[str, list[str]] = {source_id: [] for source_id in source_ids}
    for directory in ("01-Data", "02-Information", "03-Knowledge", "04-Insight", "05-Wisdom", "06-Impact"):
        root = vault_path / directory
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for source_id in source_ids:
                if source_id in text:
                    hits[source_id].append(str(path.relative_to(vault_path)))
    return {"source_ids": source_ids, "hits": hits, "all_present": all(hits.values())}


async def _store_second_opinion(
    *,
    pdf_path: Path,
    source_store: SourceStore,
    research_store: ResearchStore,
    workflow_run_id: str,
    attached_to: str,
) -> dict[str, Any]:
    source = await source_store.store_upload(
        upload_id=f"m6-second-opinion-{pdf_path.stem}",
        filename=pdf_path.name,
        content_type="application/pdf",
        data=pdf_path.read_bytes(),
        metadata={
            "intake": "m6_second_opinion",
            "origin_path": str(pdf_path),
            "origin_name": pdf_path.name,
            "source_sha256": sha256_file(pdf_path),
            "authority": "external_user_provided_non_authoritative",
            "selection_reason": "M6 second-opinion/reference attachment",
        },
    )
    router = ProcessingRouter()
    extracted = await router.process(pdf_path.read_bytes(), filename=pdf_path.name, http_content_type="application/pdf")
    converter = CanonicalMarkdownConverter(source_store=source_store)
    package = await converter.convert_extracted(
        source_id=source["source_id"],
        extracted=extracted,
        fallback_title=pdf_path.stem,
        metadata={
            "created_from": "m6_second_opinion_reference",
            "authority": "external_user_provided_non_authoritative",
            "attached_to": attached_to,
            "workflow_run_id": workflow_run_id,
        },
    )
    await source_store.update_status(
        source["source_id"],
        "completed",
        {"second_opinion_canonical_markdown_path": package.package_path},
    )
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


async def _run_business_graph_with_research(
    *,
    runtime_dir: Path,
    primary_source_id: str,
    primary_pdf_path: Path,
    second_opinion_pdf_path: Path,
    source_store: SourceStore,
    research_store: ResearchStore,
    graph_db: GraphDB,
    chat_store: ChatStore,
    workflow_store: WorkflowRunStore,
    events: list[dict[str, Any]],
    research_model: str,
    max_results: int,
) -> dict[str, Any]:
    graph_nodes = await graph_db.get_top_information_nodes_by_semantic_edge_count(limit=8)
    if len(graph_nodes) < 2:
        graph_nodes = await graph_db.get_nodes_by_type("information")
    labels = [str(node.get("label") or "").strip() for node in graph_nodes[:3] if str(node.get("label") or "").strip()]
    motive = (
        "Run Aily business planning with external research and a non-authoritative second opinion. "
        f"Use internal Knowledge from {primary_pdf_path.name}; compare external evidence about "
        f"{', '.join(labels[:3]) or primary_pdf_path.stem}."
    )
    workflow_run = await workflow_store.create_run(
        workflow_kind="business_planning",
        input_summary=motive[:240],
        metadata={
            "source_ids": [primary_source_id],
            "primary_pdf_path": str(primary_pdf_path),
            "second_opinion_pdf_path": str(second_opinion_pdf_path),
            "runner": "m6_research_second_opinion",
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
    captured_research: dict[str, Any] = {}

    async def run_research_dependency(state: dict[str, Any]) -> dict[str, Any]:
        topic_extraction_id = str(state.get("topic_extraction_id") or "")
        topics = list(state.get("topics") or [])
        topic = str((topics[0] or {}).get("label") if topics else "").strip() or primary_pdf_path.stem
        internal_context = list(state.get("knowledge_context") or [])
        query = f"{topic} commercial feasibility technical innovation recent evidence"
        service = TavilyResearchService(store=research_store)
        job = await service.create_and_run_packet(
            workflow_run_id=str(state.get("workflow_run_id") or workflow_run.workflow_run_id),
            topic=topic,
            trigger="business_planning_graph_confirmed_research",
            query=query,
            topic_extraction_id=topic_extraction_id,
            model=research_model,
            internal_context=internal_context,
            max_results=max_results,
        )
        captured_research.update({"called": True, "query": query, "job": job})
        return job

    dependencies = BusinessPlanningDependencies(
        chat_store=chat_store,
        workflow_run_store=workflow_store,
        select_context=lambda motive_arg, topics, source_ids: _select_graph_context(
            graph_db,
            primary_source_id,
            motive_arg,
            topics,
            source_ids,
        ),
        run_research=run_research_dependency,
        emit_event=lambda event_type, **payload: _emit(events, event_type, **payload),
    )
    state = {
        "workflow_run_id": workflow_run.workflow_run_id,
        "langgraph_thread_id": workflow_run.langgraph_thread_id,
        "workflow_kind": "business_planning",
        "status": "queued",
        "steps": [],
        "motive": motive,
        "source_ids": [primary_source_id],
        "research_required": True,
        "metadata": {
            "source_ids": [primary_source_id],
            "second_opinion_source_id": second_opinion["source"]["source_id"],
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
                    "decided_by": "m6_research_second_opinion_evidence",
                    "dispatch_research": True,
                    "dispatch_iwi": False,
                }
            ),
            config,
        )
    after_run = await workflow_store.get_run(workflow_run.workflow_run_id)
    after_plan_id = str(resumed_result.get("workflow_plan_id") or workflow_plan_id)
    after_plan = await chat_store.get_workflow_plan(after_plan_id) if after_plan_id else None
    topic_extraction = await chat_store.get_topic_extraction(str((after_plan or {}).get("topic_extraction_id") or ""))
    selected = _selected_context(after_plan or before_plan or {})
    selected_node_ids = [str(item["node_id"]) for item in selected]
    selected_nodes = await _selected_nodes_with_properties(graph_db, selected_node_ids)
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
        "captured_research_dependency": captured_research,
        "second_opinion": second_opinion,
    }


async def _run() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    primary_pdf, second_opinion_pdf = _select_pdfs(args.primary_pdf, args.second_opinion_pdf)
    run_id = args.run_id or make_run_id("m6_research_second_opinion")
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
    graph_db = GraphDB(graph_db_path)
    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="m6_research_second_opinion",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=[primary_pdf, second_opinion_pdf],
        source_selector="explicit_m6_pdfs" if args.primary_pdf or args.second_opinion_pdf else "default_pdf_dir_m6",
        source_contexts={
            str(primary_pdf): {"role": "primary_internal_source", "selection_reason": "M6 primary PDF for Aily Knowledge"},
            str(second_opinion_pdf): {
                "role": "second_opinion_reference_attachment",
                "selection_reason": "M6 external non-authoritative reference PDF",
            },
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

        business_payload = await _run_business_graph_with_research(
            runtime_dir=runtime_dir,
            primary_source_id=primary_source_id,
            primary_pdf_path=primary_pdf,
            second_opinion_pdf_path=second_opinion_pdf,
            source_store=source_store,
            research_store=research_store,
            graph_db=graph_db,
            chat_store=chat_store,
            workflow_store=workflow_store,
            events=events,
            research_model=args.research_model,
            max_results=args.max_results,
        )
        source_jobs = await source_store.list_source_jobs(limit=50)
        primary_source_record = await source_store.get_source(primary_source_id)
        primary_markdown_package = await source_store.get_markdown_package(primary_source_id)
        all_sources = await source_store.list_sources(limit=20)
        research_jobs = await research_store.list_research_jobs(limit=20)
        second_opinions = await research_store.list_second_opinions(limit=20)
        workflow_runs = await workflow_store.list_runs(limit=100)
        after_plan = business_payload["workflow_plan_after"] or {}
        before_plan = business_payload["workflow_plan_before"] or {}
        workflow_run_after = business_payload["workflow_run_after"] or {}
        research_job = business_payload["captured_research_dependency"].get("job") or {}
        research_packet = research_job.get("packet") or {}
        second_source_id = str(business_payload["second_opinion"]["source"]["source_id"])
        selected_source_ids = sorted(
            {
                str(source_id)
                for item in business_payload["selected_context"]
                for source_id in (item.get("source_ids") if isinstance(item.get("source_ids"), list) else [item.get("source_id", "")])
                if str(source_id).strip()
            }
        )
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
            "internal_context_evidence": [item for item in research_packet.get("evidence", []) if item.get("type") == "internal_context"],
            "external_search_evidence": [item for item in research_packet.get("evidence", []) if item.get("type") == "external_search"],
            "truth_policy": research_packet.get("truth_policy", {}),
            "secret_scan": _sanitize_packet(research_packet),
        }
        vault_source_reconciliation = _source_ids_in_vault(vault_path, [primary_source_id])

        if before_plan.get("status") != "awaiting_confirmation":
            failures.append({"check": "workflow_plan_before_status", "workflow_plan": before_plan})
        if after_plan.get("status") != "approved":
            failures.append({"check": "workflow_plan_after_status", "workflow_plan": after_plan})
        if workflow_run_after.get("status") != "completed" or workflow_run_after.get("current_node") != "research_completed":
            failures.append({"check": "business_workflow_research_completion", "workflow_run": workflow_run_after})
        if not business_payload["captured_research_dependency"].get("called"):
            failures.append({"check": "research_dependency_called", "captured": business_payload["captured_research_dependency"]})
        if research_job.get("status") != "completed":
            failures.append({"check": "tavily_research_completed", "research_job": research_job})
        if research_job.get("workflow_run_id") != workflow_run_after.get("workflow_run_id"):
            failures.append({"check": "research_linked_to_workflow", "research_job": research_job, "workflow_run": workflow_run_after})
        if research_job.get("topic_extraction_id") != after_plan.get("topic_extraction_id"):
            failures.append({"check": "research_linked_to_topic_extraction", "research_job": research_job, "workflow_plan": after_plan})
        if not research_job.get("quota_checked_at") or not research_job.get("quota_allowed"):
            failures.append({"check": "research_quota_checked_allowed", "research_job": research_job})
        if not research_packet.get("query") or not research_packet.get("search_depth"):
            failures.append({"check": "research_query_depth_present", "packet": research_packet})
        if not research_packet.get("sources") or not research_packet.get("claims"):
            failures.append({"check": "research_sources_claims_present", "packet": research_packet})
        if research_packet.get("provider") != "tavily":
            failures.append({"check": "research_provider_tavily", "packet": research_packet})
        if not research_packet.get("truth_policy", {}).get("requires_reconciliation"):
            failures.append({"check": "research_requires_reconciliation", "packet": research_packet})
        if _sanitize_packet(research_packet)["has_forbidden_secret_marker"]:
            failures.append({"check": "no_tavily_or_bearer_secret_in_packet", "summary": packet_summary})
        if not any(item.get("type") == "internal_context" for item in research_packet.get("evidence", [])):
            failures.append({"check": "packet_distinguishes_internal_context", "packet": research_packet})
        if not any(item.get("type") == "external_search" for item in research_packet.get("evidence", [])):
            failures.append({"check": "packet_distinguishes_tavily_evidence", "packet": research_packet})
        if business_payload["second_opinion"]["reference"].get("authority") != "external_user_provided_non_authoritative":
            failures.append({"check": "second_opinion_non_authoritative", "second_opinion": business_payload["second_opinion"]})
        if business_payload["second_opinion"]["packet"].get("packet", {}).get("truth_policy", {}).get("trusted_by_default") is not False:
            failures.append({"check": "second_opinion_truth_policy", "second_opinion": business_payload["second_opinion"]})
        if not vault_source_reconciliation["all_present"]:
            failures.append({"check": "vault_reconciles_primary_source_id", "vault_source_reconciliation": vault_source_reconciliation})

        result_summary = {
            "scenario": "m6_research_second_opinion",
            "primary_source_id": primary_source_id,
            "second_opinion_source_id": second_source_id,
            "business_workflow_run_id": workflow_run_after.get("workflow_run_id", ""),
            "workflow_plan_id": after_plan.get("workflow_plan_id", ""),
            "topic_extraction_id": after_plan.get("topic_extraction_id", ""),
            "research_id": research_job.get("research_id", ""),
            "research_status": research_job.get("status", ""),
            "research_source_count": packet_summary["source_count"],
            "research_claim_count": packet_summary["claim_count"],
            "research_query": packet_summary["query"],
            "research_search_depth": packet_summary["search_depth"],
            "quota_allowed": packet_summary["quota_allowed"],
            "selected_internal_source_ids": selected_source_ids,
            "second_opinion_authority": business_payload["second_opinion"]["reference"].get("authority", ""),
            "vault_primary_source_id_reconciled": vault_source_reconciliation["all_present"],
            "llm_trace_record_count": len(_load_jsonl(llm_trace_path)),
        }
        result_payload = {
            **result_summary,
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
            "research_store_db_path": str(runtime_dir / "research.sqlite"),
            "workflow_runs_db_path": str(runtime_dir / "workflow_runs.sqlite"),
            "chat_store_db_path": str(runtime_dir / "chat.sqlite"),
            "graph_db_path": str(graph_db_path),
            "foundation_checkpoint_db_path": str(runtime_dir / "foundation_langgraph_checkpoints.sqlite"),
            "business_checkpoint_db_path": str(runtime_dir / "business_planning_langgraph_checkpoints.sqlite"),
            "llm_trace_path": str(llm_trace_path),
        }
        evidence.write_json("source-records.json", {"primary": primary_source_record, "sources": all_sources}, generation_method="SourceStore source records after M6 run")
        evidence.write_json("source-store-jobs.json", source_jobs, generation_method="SourceStore.list_source_jobs after M6 run")
        evidence.write_json("canonical-markdown-packages.json", {"primary": primary_markdown_package, "second_opinion": business_payload["second_opinion"]["canonical_markdown_package"]}, generation_method="SourceStore markdown packages after M6 run")
        evidence.write_json("foundation-result.json", foundation_payload, generation_method="SourceFoundationGraph result state")
        evidence.write_json("second-opinion-reference.json", business_payload["second_opinion"]["reference"], generation_method="ResearchStore second-opinion reference")
        evidence.write_json("second-opinion-packet.json", business_payload["second_opinion"]["packet"], generation_method="ResearchStore second-opinion packet built by helper")
        evidence.write_json("business-graph-first-result.json", business_payload["first_result"], generation_method="BusinessPlanningGraph first interrupted result")
        evidence.write_json("business-graph-resumed-result.json", business_payload["resumed_result"], generation_method="BusinessPlanningGraph resumed result with dispatch_research=true")
        evidence.write_json("workflow-plan-before-confirmation.json", before_plan, generation_method="ChatStore workflow plan before BusinessPlanningGraph confirmation")
        evidence.write_json("workflow-plan-after-confirmation.json", after_plan, generation_method="ChatStore workflow plan after research confirmation")
        evidence.write_json("topic-extraction.json", business_payload["topic_extraction"], generation_method="ChatStore topic extraction linked to research")
        evidence.write_json("workflow-runs.json", [_run_payload(run) for run in workflow_runs], generation_method="WorkflowRunStore.list_runs after M6 run")
        evidence.write_json("workflow-run-history.json", business_payload["workflow_run_history"], generation_method="WorkflowRunStore.list_run_history for M6 BusinessPlanningGraph")
        evidence.write_json("checkpoint-summary.json", {"foundation": foundation_payload["checkpoint_summary"], "business": business_payload["checkpoint_summary"]}, generation_method="SQLite inspection of LangGraph checkpoint DBs")
        evidence.write_json("selected-context.json", {"context": business_payload["selected_context"], "nodes": business_payload["selected_context_nodes"]}, generation_method="GraphDB context selected for BusinessPlanningGraph")
        evidence.write_json("research-jobs.json", research_jobs, generation_method="ResearchStore.list_research_jobs after Tavily run")
        evidence.write_json("research-packet.json", research_job, generation_method="ResearchStore completed Tavily research packet")
        evidence.write_json("tavily-packet-summary.json", packet_summary, generation_method="M6 Tavily packet audit summary")
        evidence.write_json("vault-source-reconciliation.json", vault_source_reconciliation, generation_method="Obsidian vault source ID reconciliation")
        evidence.write_json("pre-post-vault-counts.json", vault_counts(vault_path), generation_method="Vault counts after M6 run")
        evidence.write_json("second-opinion-records.json", second_opinions, generation_method="ResearchStore.list_second_opinions after M6 run")
        evidence.write_jsonl("events.jsonl", events, generation_method="M6 captured runtime events")
        evidence.write_json("llm-trace-records.json", _load_jsonl(llm_trace_path), generation_method="LLM trace JSONL parsed from isolated runtime trace")
        evidence.write_json("result-summary.json", result_summary, generation_method="M6 result summary")
        exit_code = 0 if not failures else 1
    except Exception as exc:
        failures.append({"error": str(exc)})
        result_payload = {"error": str(exc)}
    finally:
        await graph_db.close()
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
