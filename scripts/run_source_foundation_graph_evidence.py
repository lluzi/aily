#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aily.main as main
from aily.orchestration.runs import WorkflowRunStore
from aily.source_store import SourceStore
from aily.verify.evidence import EvidenceRun, make_run_id
from aily.writer.vault_layout import ensure_v1_vault_layout


DEFAULT_CONTENT = """# Aily SourceFoundationGraph Evidence

This local source proves the graph-backed intake adapter can process a real file
through SourceStore, ProcessingRouter, CanonicalMarkdownConverter, LangGraph
checkpoints, WorkflowRunStore, and UI event projection.

DIKIWI/LLM output is intentionally simulated in this evidence run so the
manifest must remain mocked=true and must not be used as product acceptance.
"""


def _development_source_header() -> str:
    return (
        "---\n"
        "origin_creator: evidence-runner\n"
        "origin_created_at: generated at runtime\n"
        "origin_generation_method: scripts/run_source_foundation_graph_evidence.py mocked development input\n"
        "origin_evidence_class: development\n"
        "origin_modified_by_lead_agent: false\n"
        "---\n\n"
    )


class OfflineFoundationMind:
    """Minimal DIKIWI boundary for graph-runtime evidence, not acceptance."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _drop_requests_full_dikiwi(drop: Any) -> bool:
        return bool((drop.metadata or {}).get("dikiwi_mode") == "full")

    async def process_input_foundation(self, drop: Any) -> Any:
        self.calls.append(
            {
                "drop_id": drop.id,
                "source_id": drop.source_id,
                "rain_type": drop.rain_type.name,
                "stream_type": drop.stream_type.name,
                "content_length": len(drop.content),
                "metadata": dict(drop.metadata or {}),
            }
        )
        return SimpleNamespace(
            input_id=drop.id,
            pipeline_id=f"offline-foundation-{len(self.calls)}",
            final_stage_reached=SimpleNamespace(name="KNOWLEDGE"),
            stage_results=[
                SimpleNamespace(
                    stage=SimpleNamespace(name="DATA"),
                    success=True,
                    error_message=None,
                    items_processed=1,
                    items_output=1,
                ),
                SimpleNamespace(
                    stage=SimpleNamespace(name="INFORMATION"),
                    success=True,
                    error_message=None,
                    items_processed=1,
                    items_output=1,
                ),
                SimpleNamespace(
                    stage=SimpleNamespace(name="KNOWLEDGE"),
                    success=True,
                    error_message=None,
                    items_processed=1,
                    items_output=1,
                ),
            ],
        )

    async def process_input(self, drop: Any) -> Any:
        return await self.process_input_foundation(drop)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local SourceFoundationGraph evidence with an explicitly mocked DIKIWI boundary."
    )
    parser.add_argument("--run-id", default="", help="Optional evidence run id.")
    parser.add_argument(
        "--content",
        default=DEFAULT_CONTENT,
        help="Text content to write into the evidence source file.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path.home() / ".aily" / "runs",
        help="Evidence root directory.",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    run_id = args.run_id or make_run_id("source_foundation_graph_offline")
    run_root = (repo_root / args.runs_root / run_id).resolve()
    runtime_dir = run_root / "runtime"
    source_path = run_root / "input" / "graph-source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(_development_source_header() + args.content.strip() + "\n", encoding="utf-8")
    vault_path = runtime_dir / "vault"
    graph_db_path = runtime_dir / "graph.db"
    ensure_v1_vault_layout(vault_path, include_legacy_compatibility=False)

    evidence = EvidenceRun(
        root_dir=repo_root / args.runs_root,
        run_id=run_id,
        scenario="source_foundation_graph_offline",
        vault_path=vault_path,
        graph_db_path=graph_db_path,
        source_paths=[source_path],
        mocked=True,
        fake_components=["dikiwi_mind", "llm", "graph_db", "obsidian_vault"],
        real_files=True,
        real_graph_db=False,
        real_vault=False,
        real_llm=False,
        command=sys.argv,
    )
    evidence.capture_before()

    source_store = SourceStore(
        runtime_dir / "source_store.sqlite",
        runtime_dir / "objects",
        runtime_dir / "canonical_markdown",
    )
    workflow_store = WorkflowRunStore(runtime_dir / "workflow_runs.sqlite")
    await source_store.initialize()
    await workflow_store.initialize()

    events: list[dict[str, Any]] = []
    original_source_store = main.source_store
    original_workflow_store = main.workflow_run_store
    original_dikiwi_mind = main.dikiwi_mind
    original_emit = main.emit_ui_event
    original_data_dir = main.SETTINGS.aily_data_dir
    original_orchestrator_enabled = main.SETTINGS.orchestrator_enabled
    original_orchestrator_shadow = main.SETTINGS.orchestrator_shadow_mode
    original_foundation_only = main.SETTINGS.dikiwi_foundation_only_ingestion
    offline_mind = OfflineFoundationMind()
    failures: list[dict[str, Any]] = []
    result_payload: dict[str, Any] = {}
    exit_code = 1

    async def capture_event(event_type: str, **payload: Any) -> None:
        events.append({"type": event_type, **payload})

    try:
        main.SETTINGS.aily_data_dir = runtime_dir
        main.SETTINGS.orchestrator_enabled = True
        main.SETTINGS.orchestrator_shadow_mode = False
        main.SETTINGS.dikiwi_foundation_only_ingestion = True
        main.source_store = source_store
        main.workflow_run_store = workflow_store
        main.dikiwi_mind = offline_mind
        main.emit_ui_event = capture_event

        source = await source_store.store_upload(
            upload_id="evidence-upload",
            filename=source_path.name,
            content_type="text/markdown; charset=utf-8",
            data=source_path.read_bytes(),
            metadata={"scenario": "source_foundation_graph_offline"},
        )
        job = await source_store.enqueue_source_job(
            source_id=source["source_id"],
            job_type="process_upload_source",
            payload={
                "upload_id": "evidence-upload",
                "filename": source_path.name,
                "content_type": "text/markdown; charset=utf-8",
            },
        )
        claimed = await source_store.claim_next_source_job(worker_id="evidence-runner")
        if claimed is None:
            raise RuntimeError("Evidence source job was not claimable")
        graph_enabled = main._source_foundation_graph_enabled(claimed)
        job_result = await main._process_source_job_with_foundation_graph(claimed)
        if job_result == "completed":
            await source_store.complete_source_job(job["job_id"])
        else:
            await source_store.fail_source_job(job["job_id"], error=job_result)

        source_record = await source_store.get_source(source["source_id"])
        markdown_package = await source_store.get_markdown_package(source["source_id"])
        workflow_runs = await workflow_store.list_runs()
        result_payload = {
            "job_result": job_result,
            "graph_enabled": graph_enabled,
            "source": source_record,
            "markdown_package": markdown_package,
            "workflow_runs": [run.__dict__ for run in workflow_runs],
            "offline_dikiwi_calls": offline_mind.calls,
            "checkpoint_db_path": str(main.SETTINGS.langgraph_checkpoint_db_path),
            "source_store_db_path": str(runtime_dir / "source_store.sqlite"),
        }
        evidence.write_json("workflow-run.json", result_payload["workflow_runs"])
        evidence.write_json("source-record.json", source_record)
        evidence.write_json("canonical-markdown-package.json", markdown_package)
        evidence.write_json("offline-dikiwi-calls.json", offline_mind.calls)
        if markdown_package:
            package_path = Path(str(markdown_package["package_path"]))
            if package_path.exists():
                evidence.write_text("samples/knowledge/canonical-package.md", package_path.read_text(encoding="utf-8"))
        exit_code = 0 if job_result == "completed" and graph_enabled else 1
        if exit_code != 0:
            failures.append({"error": "graph-backed source job did not complete", "result": result_payload})
    except Exception as exc:
        failures.append({"error": str(exc)})
        result_payload = {"error": str(exc)}
    finally:
        main.source_store = original_source_store
        main.workflow_run_store = original_workflow_store
        main.dikiwi_mind = original_dikiwi_mind
        main.emit_ui_event = original_emit
        main.SETTINGS.aily_data_dir = original_data_dir
        main.SETTINGS.orchestrator_enabled = original_orchestrator_enabled
        main.SETTINGS.orchestrator_shadow_mode = original_orchestrator_shadow
        main.SETTINGS.dikiwi_foundation_only_ingestion = original_foundation_only
        await workflow_store.close()
        await source_store.close()

    manifest = evidence.finalize(
        exit_code=exit_code,
        result=result_payload,
        failures=failures,
        ui_events=events,
        stderr_text="" if exit_code == 0 else json.dumps(failures, ensure_ascii=False),
        repo_root=repo_root,
    )
    print(json.dumps({"run_id": run_id, "manifest": str(evidence.path / "manifest.json"), "exit_code": exit_code}, indent=2))
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
