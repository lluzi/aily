#!/usr/bin/env python3
"""Generate Gate 1 PDF intake-to-Knowledge evidence.

Origin: Created by Codex lead agent on 2026-05-17.
Role: Evidence-runner source code only; not acceptance evidence for any gate.

This runner is intended for independent execution. It uses real PDFs, real
processing, real LLM routes, real GraphDB, and the configured Obsidian vault.
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
from aily.orchestration.checkpoint import async_sqlite_checkpointer
from aily.orchestration.runs import WorkflowRunStore
from aily.orchestration.source_foundation_graph import SourceFoundationDependencies, build_source_foundation_graph
from aily.processing.canonical_markdown import CanonicalMarkdownConverter
from aily.processing.router import ProcessingRouter
from aily.sessions.dikiwi_mind import DikiwiMind
from aily.source_store import SourceStore
from aily.verify.evidence import EvidenceRun, make_run_id, sha256_file
from aily.verify.test_vaults import resolve_test_vault_path
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter


DEFAULT_PDF = Path("/Users/luzi/aily_chaos/pdf/wb7-02-ayyagari-pres-user.pdf")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Aily Gate 1 PDF intake evidence.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="Original PDF source path.")
    parser.add_argument("--run-id", default="", help="Optional evidence run id.")
    parser.add_argument("--runs-root", type=Path, default=SETTINGS.evidence_runs_dir, help="Evidence root directory.")
    parser.add_argument("--vault-path", type=Path, default=None, help="Visible Obsidian vault path. Defaults to configured iCloud Documents Aily vault.")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"Gate 1 PDF does not exist: {pdf_path}")

    run_id = args.run_id or make_run_id("gate1_pdf_intake")
    run_root = args.runs_root.expanduser().resolve() / run_id
    runtime_dir = run_root / "runtime"
    llm_trace_path = run_root / "runtime" / "llm-calls.jsonl"
    vault_path = resolve_test_vault_path(run_id, args.vault_path)
    graph_db_path = runtime_dir / "graph.db"
    source_store = SourceStore(
        runtime_dir / "source_store.sqlite",
        runtime_dir / "objects",
        runtime_dir / "canonical_markdown",
    )
    workflow_store = WorkflowRunStore(runtime_dir / "workflow_runs.sqlite")
    graph_db = GraphDB(graph_db_path)
    evidence = EvidenceRun(
        root_dir=args.runs_root,
        run_id=run_id,
        scenario="gate1_pdf_intake",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=[pdf_path],
        source_selector="explicit_gate1_pdf",
        source_contexts={
            str(pdf_path): {
                "role": "gate1_representative_pdf",
                "selection_reason": "Gate 1 representative original PDF",
            }
        },
        mocked=False,
        real_files=True,
        real_graph_db=True,
        real_vault=True,
        real_llm=True,
        claimed_components=["files", "graph_db", "vault", "llm"],
        command=sys.argv,
    )
    evidence.capture_before()

    events: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    result_payload: dict[str, Any] = {}
    exit_code = 1

    async def emit_event(event_type: str, **payload: Any) -> None:
        events.append({"type": event_type, **payload})

    await source_store.initialize()
    await workflow_store.initialize()
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

        upload_id = f"gate1-{pdf_path.stem}"
        source = await source_store.store_upload(
            upload_id=upload_id,
            filename=pdf_path.name,
            content_type="application/pdf",
            data=pdf_path.read_bytes(),
            metadata={
                "intake": "gate1_pdf_intake",
                "origin_path": str(pdf_path),
                "origin_name": pdf_path.name,
                "source_sha256": sha256_file(pdf_path),
                "selection_reason": "Gate 1 representative original PDF",
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
                "source_kind": "gate1_pdf",
            },
        )
        claimed = await source_store.claim_next_source_job(worker_id="gate1-evidence-runner")
        if claimed is None:
            raise RuntimeError("Gate 1 source job was not claimable")

        run = await workflow_store.create_run(
            workflow_kind="source_foundation",
            input_summary=f"Gate 1 PDF intake: {pdf_path.name}",
            metadata={
                "source_id": source["source_id"],
                "job_id": job["job_id"],
                "job_type": "process_upload_source",
                "pdf_path": str(pdf_path),
            },
        )
        dependencies = SourceFoundationDependencies(
            source_store=source_store,
            processing_router_factory=lambda: ProcessingRouter(),
            canonical_markdown_converter_factory=lambda: CanonicalMarkdownConverter(source_store=source_store),
            dikiwi_ingestion=mind.process_input_foundation,
            emit_event=emit_event,
            workflow_run_store=workflow_store,
            vault_path=vault_path,
        )
        state = {
            "workflow_run_id": run.workflow_run_id,
            "langgraph_thread_id": run.langgraph_thread_id,
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
        async with async_sqlite_checkpointer(runtime_dir / "langgraph_checkpoints.sqlite") as checkpointer:
            graph = build_source_foundation_graph(checkpointer, dependencies=dependencies)
            result = await graph.ainvoke(state, {"configurable": {"thread_id": run.langgraph_thread_id}})

        if result.get("status") == "completed":
            await source_store.complete_source_job(str(job["job_id"]))
            exit_code = 0
        else:
            await source_store.fail_source_job(str(job["job_id"]), error=str(result.get("status") or "failed"))
            failures.append({"check": "source_foundation_graph", "result": result})

        source_record = await source_store.get_source(source["source_id"])
        markdown_package = await source_store.get_markdown_package(source["source_id"])
        workflow_runs = await workflow_store.list_runs()
        source_jobs = await source_store.list_source_jobs(limit=20)
        result_payload = {
            "source": source_record,
            "source_job": job,
            "source_jobs": source_jobs,
            "markdown_package": markdown_package,
            "workflow_runs": [snapshot.__dict__ for snapshot in workflow_runs],
            "graph_result": result,
            "checkpoint_db_path": str(runtime_dir / "langgraph_checkpoints.sqlite"),
            "graph_db_path": str(graph_db_path),
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
        }
        evidence.write_json("source-record.json", source_record, generation_method="SourceStore.get_source after Gate 1 run")
        evidence.write_json("source-store-jobs.json", source_jobs, generation_method="SourceStore.list_source_jobs after Gate 1 run")
        evidence.write_json("canonical-markdown-package.json", markdown_package, generation_method="SourceStore.get_markdown_package after Gate 1 run")
        evidence.write_json("workflow-runs.json", result_payload["workflow_runs"], generation_method="WorkflowRunStore.list_runs after Gate 1 run")
        evidence.write_json("graph-result.json", result, generation_method="SourceFoundationGraph result state")
    except Exception as exc:
        failures.append({"error": str(exc)})
        result_payload = {"error": str(exc)}
    finally:
        await graph_db.close()
        await workflow_store.close()
        await source_store.close()

    manifest = evidence.finalize(
        exit_code=exit_code,
        result=result_payload,
        failures=failures,
        ui_events=events,
        llm_log_file=str(llm_trace_path),
        stderr_text="" if exit_code == 0 else json.dumps(failures, ensure_ascii=False),
        repo_root=repo_root,
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": manifest["exit_code"]}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
