#!/usr/bin/env python3
"""Generate Gate 3 triggered I/W/I evidence.

Origin: Created by Codex evidence harness worker on 2026-05-17.
Role: Evidence-runner source code only; not acceptance evidence for any gate.

This runner is intended for independent execution. It uses a real PDF, real
SourceFoundationGraph intake, real DIKIWI foundation and triggered I/W/I paths,
real LLM routes, real GraphDB, and the configured Obsidian vault. It stops at
I/W/I output and records downstream delivery as not executed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aily.config import SETTINGS
from aily.graph.db import GraphDB
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.orchestration.chat_store import ChatStore, build_iwi_workflow_steps, extract_candidate_topics
from aily.orchestration.checkpoint import async_sqlite_checkpointer
from aily.orchestration.runs import WorkflowRunStore
from aily.orchestration.source_foundation_graph import SourceFoundationDependencies, build_source_foundation_graph
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
    parser = argparse.ArgumentParser(description="Generate Aily Gate 3 triggered I/W/I evidence.")
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


def _iwi_counts(counts: dict[str, int]) -> dict[str, int]:
    return {stage: int(counts.get(stage, 0)) for stage in IWI_STAGE_DIRS}


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


def _extract_graph_context(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in plan.get("knowledge_context", [])
        if item.get("context_type") == "graph_information_node" and str(item.get("node_id") or "").strip()
    ]


async def _emit(events: list[dict[str, Any]], event_type: str, **payload: Any) -> None:
    events.append({"type": event_type, **payload})


async def _ingest_foundation(
    *,
    pdf_path: Path,
    run_id: str,
    runtime_dir: Path,
    vault_path: Path,
    source_store: SourceStore,
    workflow_store: WorkflowRunStore,
    graph_db: GraphDB,
    mind: DikiwiMind,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    upload_id = f"gate3-{pdf_path.stem}"
    source = await source_store.store_upload(
        upload_id=upload_id,
        filename=pdf_path.name,
        content_type="application/pdf",
        data=pdf_path.read_bytes(),
        metadata={
            "intake": "gate3_triggered_iwi",
            "origin_path": str(pdf_path),
            "origin_name": pdf_path.name,
            "source_sha256": sha256_file(pdf_path),
            "selection_reason": "Gate 3 representative original PDF for triggered I/W/I",
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
            "source_kind": "gate3_pdf",
        },
    )
    claimed = await source_store.claim_next_source_job(worker_id="gate3-evidence-runner")
    if claimed is None:
        raise RuntimeError("Gate 3 source job was not claimable")

    foundation_run = await workflow_store.create_run(
        workflow_kind="source_foundation",
        input_summary=f"Gate 3 foundation PDF intake: {pdf_path.name}",
        metadata={
            "source_id": source["source_id"],
            "job_id": job["job_id"],
            "job_type": "process_upload_source",
            "pdf_path": str(pdf_path),
        },
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
    checkpoint_path = runtime_dir / "langgraph_checkpoints.sqlite"
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
    }


async def _fallback_plan(
    *,
    chat_store: ChatStore,
    graph_db: GraphDB,
    source_id: str,
    motive: str,
    existing_thread_id: str | None = None,
) -> dict[str, Any]:
    nodes = await graph_db.get_top_information_nodes_by_semantic_edge_count(limit=8)
    if len(nodes) < 2:
        nodes = await graph_db.get_nodes_by_type("information")
    selected = nodes[:8]
    if len(selected) < 2:
        raise RuntimeError("Gate 3 requires at least two information graph nodes for triggered I/W/I")
    thread = (
        await chat_store.get_thread(existing_thread_id)
        if existing_thread_id
        else await chat_store.create_thread(title="Gate 3 triggered I/W/I", metadata={"created_from": "gate3_runner_fallback"})
    )
    if thread is None:
        thread = await chat_store.create_thread(title="Gate 3 triggered I/W/I", metadata={"created_from": "gate3_runner_fallback"})
    message = await chat_store.add_message(
        thread["chat_thread_id"],
        role="user",
        content=motive,
        metadata={"source_ids": [source_id], "created_from": "gate3_runner_fallback"},
    )
    topics = extract_candidate_topics(motive)
    knowledge_context = [
        {
            "context_type": "graph_information_node",
            "node_id": node.get("id"),
            "label": node.get("label"),
            "source": node.get("source"),
            "source_id": source_id,
            "source_ids": [source_id],
            "source_paths": [f"source_id:{source_id}"],
            "selection_reason": "fallback_top_information_graph_node",
            "role": "triggered_iwi_context",
        }
        for node in selected
    ]
    topic_extraction = await chat_store.create_topic_extraction(
        chat_thread_id=thread["chat_thread_id"],
        message_id=message["message_id"],
        motive=motive,
        topics=topics,
        knowledge_context=knowledge_context,
        metadata={
            "source_ids": [source_id],
            "context_selection": "fallback_top_information_graph_nodes",
        },
    )
    workflow_plan = await chat_store.create_workflow_plan(
        chat_thread_id=thread["chat_thread_id"],
        message_id=message["message_id"],
        topic_extraction_id=topic_extraction["topic_extraction_id"],
        plan_type="triggered_iwi",
        motive=motive,
        topics=topics,
        knowledge_context=knowledge_context,
        proposed_steps=build_iwi_workflow_steps(research_required=False),
        metadata={
            "requires_confirmation": True,
            "source_ids": [source_id],
            "context_source_ids": [source_id],
            "context_count": len(knowledge_context),
            "created_by": "gate3_runner_fallback",
        },
    )
    assistant_message = await chat_store.add_message(
        thread["chat_thread_id"],
        role="assistant",
        content=(
            f"Workflow plan proposed with {len(topics)} topic(s) and "
            f"{len(knowledge_context)} context item(s). Awaiting confirmation."
        ),
        metadata={
            "workflow_plan_id": workflow_plan["workflow_plan_id"],
            "topic_extraction_id": topic_extraction["topic_extraction_id"],
            "notification_type": "workflow_plan_awaiting_confirmation",
            "created_from": "gate3_runner_fallback",
        },
    )
    return {
        "message": message,
        "assistant_message": assistant_message,
        "topic_extraction": topic_extraction,
        "workflow_plan": workflow_plan,
        "fallback_used": True,
    }


async def _run() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    pdf_path = _select_pdf(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Gate 3 PDF does not exist: {pdf_path}")

    run_id = args.run_id or make_run_id("gate3_triggered_iwi")
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
        scenario="gate3_triggered_iwi",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=[pdf_path],
        source_selector="explicit_gate3_pdf" if args.pdf else "default_pdf_dir_gate3",
        source_contexts={
            str(pdf_path): {
                "role": "gate3_triggered_iwi_pdf",
                "selection_reason": "Gate 3 representative original PDF for foundation and triggered I/W/I",
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

        import aily.main as main_app
        import aily.sessions.dikiwi_mind as dikiwi_mind_module

        async def emit_event(event_type: str, **payload: Any) -> None:
            await _emit(events, event_type, **payload)

        main_app.source_store = source_store
        main_app.workflow_run_store = workflow_store
        main_app.chat_store = chat_store
        main_app.graph_db = graph_db
        main_app.dikiwi_mind = mind
        main_app.emit_ui_event = emit_event
        dikiwi_mind_module.emit_ui_event = emit_event

        foundation_payload = await _ingest_foundation(
            pdf_path=pdf_path,
            run_id=run_id,
            runtime_dir=runtime_dir,
            vault_path=vault_path,
            source_store=source_store,
            workflow_store=workflow_store,
            graph_db=graph_db,
            mind=mind,
            events=events,
        )
        source_id = foundation_payload["source"]["source_id"]
        if foundation_payload["foundation_graph_result"].get("status") != "completed":
            failures.append({"check": "source_foundation_graph", "result": foundation_payload["foundation_graph_result"]})

        graph_nodes = await graph_db.get_top_information_nodes_by_semantic_edge_count(limit=8)
        if len(graph_nodes) < 2:
            graph_nodes = await graph_db.get_nodes_by_type("information")
        labels = [str(node.get("label") or "").strip() for node in graph_nodes[:3] if str(node.get("label") or "").strip()]
        motive = (
            "Assess whether the uploaded evidence suggests a concrete business opportunity. "
            f"Focus on: {', '.join(labels[:3]) or pdf_path.stem}."
        )

        thread = await main_app._ui_create_chat_thread({"title": "Gate 3 triggered I/W/I"})
        before_plan_counts = vault_counts(vault_path)
        plan_payload = await main_app._ui_chat_message_handler(
            thread["chat_thread_id"],
            {
                "role": "user",
                "content": motive,
                "source_ids": [source_id],
                "attachment_ids": [],
                "research_required": False,
            },
        )
        plan_payload["fallback_used"] = False
        graph_context = _extract_graph_context(plan_payload["workflow_plan"])
        if len(graph_context) < 2:
            plan_payload = await _fallback_plan(
                chat_store=chat_store,
                graph_db=graph_db,
                source_id=source_id,
                motive=motive,
                existing_thread_id=thread["chat_thread_id"],
            )
            graph_context = _extract_graph_context(plan_payload["workflow_plan"])
            await emit_event(
                "workflow_plan_context_fallback_used",
                workflow_plan_id=plan_payload["workflow_plan"]["workflow_plan_id"],
                graph_context_count=len(graph_context),
            )

        after_plan_counts = vault_counts(vault_path)
        pre_confirmation_iwi_delta = {
            stage: _iwi_counts(after_plan_counts)[stage] - _iwi_counts(before_plan_counts)[stage]
            for stage in IWI_STAGE_DIRS
        }
        if any(delta != 0 for delta in pre_confirmation_iwi_delta.values()):
            failures.append({"check": "pre_confirmation_no_iwi_side_effects", "delta": pre_confirmation_iwi_delta})
        if len(graph_context) < 2:
            failures.append({"check": "plan_graph_context", "graph_context_count": len(graph_context)})

        confirmed_payload = await main_app._ui_workflow_plan_confirm_handler(
            plan_payload["workflow_plan"]["workflow_plan_id"],
            {
                "approved": True,
                "decided_by": "gate3_evidence_runner",
                "dispatch": True,
                "run_inline": True,
            },
        )

        workflow_plan_after_confirmation = confirmed_payload.get("workflow_plan") or {}
        workflow_run_id = str((confirmed_payload.get("workflow_run") or {}).get("workflow_run_id") or "")
        workflow_run = await workflow_store.get_run(workflow_run_id) if workflow_run_id else None
        workflow_history = await workflow_store.list_run_history(workflow_run_id) if workflow_run_id else []
        workflow_runs = await workflow_store.list_runs(limit=100)
        source_jobs = await source_store.list_source_jobs(limit=20)
        source_record = await source_store.get_source(source_id)
        markdown_package = await source_store.get_markdown_package(source_id)
        chat_thread = await chat_store.get_thread(thread["chat_thread_id"])
        post_counts = vault_counts(vault_path)
        selected_node_ids = [str(item["node_id"]) for item in graph_context]
        selected_graph_nodes = await graph_db.get_nodes_by_ids(selected_node_ids)
        selected_graph_nodes_payload = [
            {
                **node,
                "role": "triggered_iwi_context",
                "selection_reason": "workflow_plan_graph_information_node",
            }
            for node in selected_graph_nodes
        ]
        llm_trace_records = _load_jsonl(llm_trace_path)

        if workflow_run is None:
            failures.append({"check": "triggered_iwi_workflow_run", "error": "No workflow run returned by confirmation"})
        elif workflow_run.status != "completed":
            failures.append({"check": "triggered_iwi_completion", "workflow_run": _run_payload(workflow_run)})
        if workflow_run and workflow_run.metadata.get("final_stage") != "IMPACT":
            failures.append({"check": "triggered_iwi_final_stage", "metadata": workflow_run.metadata})

        downstream_delivery_status = {
            "outbound_delivery_executed": False,
            "reason": "Gate 3 runner stops at triggered I/W/I output.",
        }
        result_summary = {
            "scenario": "gate3_triggered_iwi",
            "source_id": source_id,
            "workflow_plan_id": workflow_plan_after_confirmation.get("workflow_plan_id"),
            "workflow_run_id": workflow_run_id,
            "workflow_run_status": workflow_run.status if workflow_run else "",
            "final_stage": workflow_run.metadata.get("final_stage") if workflow_run else "",
            "selected_graph_node_count": len(selected_graph_nodes_payload),
            "pre_confirmation_iwi_delta": pre_confirmation_iwi_delta,
            "llm_trace_record_count": len(llm_trace_records),
            "downstream_delivery_status": downstream_delivery_status,
        }
        result_payload = {
            **result_summary,
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
            "workflow_runs_db_path": str(runtime_dir / "workflow_runs.sqlite"),
            "chat_store_db_path": str(runtime_dir / "chat.sqlite"),
            "graph_db_path": str(graph_db_path),
            "checkpoint_db_path": str(runtime_dir / "langgraph_checkpoints.sqlite"),
            "llm_trace_path": str(llm_trace_path),
            "fallback_plan_used": bool(plan_payload.get("fallback_used")),
        }

        evidence.write_json("source-record.json", source_record, generation_method="SourceStore.get_source after Gate 3 run")
        evidence.write_json("source-store-jobs.json", source_jobs, generation_method="SourceStore.list_source_jobs after Gate 3 run")
        evidence.write_json("canonical-markdown-package.json", markdown_package, generation_method="SourceStore.get_markdown_package after Gate 3 run")
        evidence.write_json("foundation-result.json", foundation_payload, generation_method="SourceFoundationGraph result state")
        evidence.write_json("chat-records.json", chat_thread, generation_method="ChatStore.get_thread after Gate 3 run")
        evidence.write_json("topic-extraction.json", plan_payload["topic_extraction"], generation_method="ChatStore topic extraction for Gate 3 motive")
        evidence.write_json("workflow-plan-before-confirmation.json", plan_payload["workflow_plan"], generation_method="ChatStore workflow plan before confirmation")
        evidence.write_json("workflow-plan-after-confirmation.json", workflow_plan_after_confirmation, generation_method="ChatStore workflow plan after confirmation and dispatch")
        evidence.write_json("workflow-runs.json", [_run_payload(run) for run in workflow_runs], generation_method="WorkflowRunStore.list_runs after Gate 3 run")
        evidence.write_json("workflow-run-history.json", workflow_history, generation_method="WorkflowRunStore.list_run_history for triggered I/W/I")
        evidence.write_json("pre-confirmation-vault-counts.json", {"before_plan": before_plan_counts, "after_plan": after_plan_counts, "iwi_delta": pre_confirmation_iwi_delta}, generation_method="Vault counts before and after plan creation")
        evidence.write_json("post-trigger-vault-counts.json", post_counts, generation_method="Vault counts after confirmed triggered I/W/I")
        evidence.write_json("selected-graph-nodes.json", selected_graph_nodes_payload, generation_method="GraphDB selected nodes from workflow plan context")
        evidence.write_json("llm-trace-records.json", llm_trace_records, generation_method="LLM trace JSONL parsed from isolated runtime trace")
        evidence.write_jsonl("events.jsonl", events, generation_method="Gate 3 captured runtime events")
        evidence.write_json("downstream-delivery-status.json", downstream_delivery_status, generation_method="Gate 3 boundary for downstream delivery")
        evidence.write_json("result-summary.json", result_summary, generation_method="Gate 3 result summary")

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
