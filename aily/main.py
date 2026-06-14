from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, UploadFile
from fastapi import Request
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from langgraph.types import Command

from aily.business import BusinessPlanStore, BusinessPlanSynthesizer
from aily.config import SETTINGS
from aily.copilot.router import create_copilot_router
from aily.queue.db import QueueDB
from aily.browser.fetcher import BrowserFetcher, FetchError
from aily.writer.obsidian import ObsidianWriter, ObsidianAPIError
from aily.parser import registry
from aily.parser.parsers import (
    parse_kimi,
    parse_monica,
    parse_arxiv,
    parse_github,
    parse_youtube,
)
from aily.graph.db import GraphDB
from aily.llm.client import LLMClient
from aily.sessions.dikiwi_mind import DikiwiMind
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.writer.dikiwi_obsidian import DikiwiObsidianWriter
from aily.gating.drainage import RainDrop, RainType, StreamType
from aily.processing.canonical_markdown import CanonicalMarkdownConverter
from aily.processing.router import ProcessingRouter
from aily.research import ResearchStore, TavilyResearchService
from aily.research.tavily_packets import build_second_opinion_packet
from aily.runtime.backpressure import provider_backpressure
from aily.inbox import WatchedInboxService
from aily.orchestration.checkpoint import async_sqlite_checkpointer
from aily.orchestration.chat_store import (
    ChatStore,
    extract_candidate_topics,
)
from aily.orchestration.business_planning_graph import (
    BusinessPlanningDependencies,
    build_business_planning_graph,
)
from aily.orchestration.runs import WorkflowRunStore
from aily.orchestration.source_foundation_graph import (
    SourceFoundationDependencies,
    build_source_foundation_graph,
)
from aily.source_store import SourceJobCapacityError, SourceStore
from aily.synthesis import SynthesisCandidateStore
from aily.ui.events import emit_ui_event, ui_event_hub
from aily.ui.router import create_ui_router
from aily.verify.run_registry import RunRegistry
from aily.security.audit import AuditLogger
from aily.security.backup import create_backup, restore_backup
from aily.security.rate_limit import FixedWindowRateLimiter

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
HAS_MULTIPART = importlib.util.find_spec("python_multipart") is not None

db = QueueDB(SETTINGS.queue_db_path)
graph_db = GraphDB(SETTINGS.graph_db_path)
source_store = SourceStore(
    SETTINGS.source_store_db_path,
    SETTINGS.source_object_dir,
    SETTINGS.canonical_markdown_dir,
)
workflow_run_store = WorkflowRunStore(SETTINGS.workflow_runs_db_path)
chat_store = ChatStore(SETTINGS.chat_store_db_path)
synthesis_store = SynthesisCandidateStore(SETTINGS.synthesis_candidate_db_path)
research_store = ResearchStore(SETTINGS.research_store_db_path)
business_plan_store = BusinessPlanStore(SETTINGS.business_plan_store_db_path)
fetcher = BrowserFetcher()
writer = ObsidianWriter(
    SETTINGS.obsidian_rest_api_key,
    SETTINGS.obsidian_vault_path,
    SETTINGS.obsidian_rest_api_port,
    queue_db=db,
)
llm_resolver = PrimaryLLMRoute.build_settings_resolver(SETTINGS)
llm_client = llm_resolver("default")
browser_manager_instance = None
ui_upload_tasks: dict[str, asyncio.Task[Any]] = {}
source_worker_tasks: list[asyncio.Task[Any]] = []
source_worker_stop: asyncio.Event | None = None
synthesis_detection_task: asyncio.Task[Any] | None = None
heartbeat_task: asyncio.Task[Any] | None = None
inbox_watcher: WatchedInboxService | None = None
workflow_tasks: dict[str, asyncio.Task[Any]] = {}
ui_upload_semaphore = asyncio.Semaphore(max(1, SETTINGS.ui_upload_concurrency))
ui_rate_limiter = FixedWindowRateLimiter(
    max_requests=max(1, SETTINGS.ui_rate_limit_requests),
    window_seconds=max(1.0, SETTINGS.ui_rate_limit_window_seconds),
) if SETTINGS.hosted_mode else None
audit_logger = AuditLogger(SETTINGS.resolved_audit_log_path)

# DIKIWI continuous knowledge processing
dikiwi_mind: DikiwiMind | None = None

ERROR_MESSAGES = {
    "FETCH_FAILED": "Could not fetch the page. The link may be expired or require login.",
    "PARSE_FAILED": "Could not extract content from this page type.",
    "OBSIDIAN_REJECTED": "Obsidian Local REST API plugin is not running. Please start Obsidian and enable the plugin.",
    "OBSIDIAN_TIMEOUT": "Obsidian did not respond. Please check that the vault is open.",
    "PUSH_FAILED": "Saved to Obsidian, but could not send confirmation.",
}


def _retry_delay_for_attempt(attempt_count: int) -> float:
    base = max(1.0, float(SETTINGS.source_retry_base_delay_seconds))
    cap = max(base, float(SETTINGS.source_retry_max_delay_seconds))
    return min(cap, base * (2 ** max(0, attempt_count - 1)))


def _terminal_status_for_result(result: Any) -> tuple[str, dict[str, Any]]:
    """Decide the honest terminal status for a finished ingestion.

    A source that produced zero notes (e.g. the DATA quality gate rejected thin
    or image-only content) must NOT be reported as a clean 'completed' — that
    hides failure. It becomes 'completed_empty' with a human-readable reason so
    the user can see *why* nothing landed.
    """
    stage_results = getattr(result, "stage_results", []) or []
    total_output = sum(int(getattr(r, "items_output", 0) or 0) for r in stage_results)
    meta: dict[str, Any] = {
        "pipeline_id": getattr(result, "pipeline_id", ""),
        "final_stage": result.final_stage_reached.name if getattr(result, "final_stage_reached", None) else "",
    }
    if total_output > 0:
        return "completed", meta
    reason = ""
    for r in stage_results:
        data = getattr(r, "data", {}) or {}
        if data.get("quality_assessment") == "low":
            reason = str(data.get("quality_reason") or "")
            break
    meta["empty_reason"] = (
        reason
        or "No notes were produced — the source had too little extractable text "
        "(image-only PDFs need a vision model)."
    )
    return "completed_empty", meta


def _is_retryable_processing_error(message: str) -> bool:
    lowered = (message or "").lower()
    retry_markers = (
        "timed out",
        "timeout",
        "llm failed",
        "provider",
        "rate limit",
        "temporarily",
        "connection",
        "network",
        "circuit breaker",
    )
    return any(marker in lowered for marker in retry_markers)


def _failed_stage(result: Any) -> Any | None:
    return next((stage for stage in getattr(result, "stage_results", []) if not stage.success), None)


def _stage_provider_model(result: Any, stage: Any | None) -> tuple[str, str]:
    data = getattr(stage, "data", {}) if stage else {}
    return str(data.get("provider", "")), str(data.get("model", ""))


def _source_queue_active_count(counts: dict[str, int]) -> int:
    return int(counts.get("queued", 0)) + int(counts.get("retry_pending", 0)) + int(counts.get("running", 0))


async def _ensure_source_queue_capacity(additional_jobs: int) -> None:
    max_pending = int(SETTINGS.source_job_max_pending)
    if max_pending <= 0 or additional_jobs <= 0:
        return
    counts = await source_store.get_source_job_counts()
    active = _source_queue_active_count(counts)
    if active + additional_jobs > max_pending:
        await emit_ui_event(
            "source_queue_rejected",
            active_jobs=active,
            requested_jobs=additional_jobs,
            max_pending=max_pending,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "source_queue_full",
                "active_jobs": active,
                "requested_jobs": additional_jobs,
                "max_pending": max_pending,
            },
        )


def _should_enqueue_upload_source(source_record: dict[str, Any]) -> bool:
    if not source_record.get("duplicate"):
        return True
    return str(source_record.get("status") or "") in {
        "stored",
        "deferred",
        "failed",
        "failed_retry_exhausted",
        "cancelled",
        "retry_pending",
    }


def _canonical_markdown_converter() -> CanonicalMarkdownConverter:
    return CanonicalMarkdownConverter(source_store=source_store)


async def _process_dikiwi_ingestion(drop: RainDrop) -> Any:
    if not dikiwi_mind:
        raise RuntimeError("DIKIWI Mind is not initialized")
    if (
        SETTINGS.dikiwi_foundation_only_ingestion
        and hasattr(dikiwi_mind, "process_input_foundation")
        and not getattr(dikiwi_mind, "_drop_requests_full_dikiwi", lambda _drop: False)(drop)
    ):
        return await dikiwi_mind.process_input_foundation(drop)
    return await dikiwi_mind.process_input(drop)


async def _process_source_job_with_foundation_graph(job: dict[str, Any]) -> str:
    payload = dict(job.get("payload") or {})
    source_id = str(job.get("source_id") or payload.get("source_id") or "")
    job_id = str(job.get("job_id") or payload.get("job_id") or "")
    job_type = str(job.get("job_type") or payload.get("job_type") or "")
    input_summary = source_id
    if job_type == "process_url_source":
        source = await source_store.get_source(source_id)
        input_summary = str((source or {}).get("normalized_source") or payload.get("url") or source_id)
    run = await workflow_run_store.create_run(
        workflow_kind="source_foundation",
        input_summary=input_summary[:500],
        metadata={
            "source_id": source_id,
            "job_id": job_id,
            "job_type": job_type,
            "job_payload": {
                **payload,
                "source_id": source_id,
                "job_id": job_id,
                "job_type": job_type,
            },
        },
    )
    await emit_ui_event(
        "workflow_run_queued",
        workflow_run_id=run.workflow_run_id,
        workflow_kind=run.workflow_kind,
        source_id=source_id,
        job_id=job_id,
        job_type=job_type,
    )
    await workflow_run_store.update_status(
        run.workflow_run_id,
        status="running",
        current_node="source_foundation",
        metadata={"source_id": source_id, "job_id": job_id, "job_type": job_type},
    )
    await emit_ui_event(
        "workflow_run_started",
        workflow_run_id=run.workflow_run_id,
        workflow_kind=run.workflow_kind,
        source_id=source_id,
        job_id=job_id,
        job_type=job_type,
    )
    dependencies = SourceFoundationDependencies(
        source_store=source_store,
        processing_router_factory=lambda: ProcessingRouter(browser_manager=browser_manager_instance),
        canonical_markdown_converter_factory=_canonical_markdown_converter,
        dikiwi_ingestion=_process_dikiwi_ingestion,
        emit_event=emit_ui_event,
        browser_manager=browser_manager_instance,
        workflow_run_store=workflow_run_store,
        vault_path=Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path),
        failed_stage=_failed_stage,
    )
    state = {
        "workflow_run_id": run.workflow_run_id,
        "langgraph_thread_id": run.langgraph_thread_id,
        "workflow_kind": "source_foundation",
        "status": "queued",
        "steps": [],
        "source_id": source_id,
        "job_id": job_id,
        "job_type": job_type,
        "metadata": {
            "source_id": source_id,
            "job_id": job_id,
            "job_type": job_type,
            "job_payload": {
                **payload,
                "source_id": source_id,
                "job_id": job_id,
                "job_type": job_type,
            },
        },
    }
    config = {"configurable": {"thread_id": run.langgraph_thread_id}}
    try:
        async with async_sqlite_checkpointer(SETTINGS.langgraph_checkpoint_db_path) as checkpointer:
            graph = build_source_foundation_graph(checkpointer, dependencies=dependencies)
            result = await graph.ainvoke(state, config)
    except Exception as exc:
        error = str(exc)
        await workflow_run_store.update_status(
            run.workflow_run_id,
            status="failed",
            current_node="source_foundation",
            last_error=error,
        )
        await emit_ui_event(
            "workflow_run_failed",
            workflow_run_id=run.workflow_run_id,
            workflow_kind=run.workflow_kind,
            source_id=source_id,
            job_id=job_id,
            job_type=job_type,
            error=error,
        )
        raise
    if result.get("status") == "completed":
        await emit_ui_event(
            "workflow_run_completed",
            workflow_run_id=run.workflow_run_id,
            workflow_kind=run.workflow_kind,
            source_id=source_id,
            job_id=job_id,
            job_type=job_type,
            pipeline_id=result.get("pipeline_id"),
            final_stage=result.get("final_stage"),
        )
        return "completed"
    error = str(result.get("error") or "source foundation graph failed")
    await workflow_run_store.update_status(
        run.workflow_run_id,
        status="failed",
        current_node=str(result.get("current_node") or "source_foundation"),
        last_error=error,
    )
    await emit_ui_event(
        "workflow_run_failed",
        workflow_run_id=run.workflow_run_id,
        workflow_kind=run.workflow_kind,
        source_id=source_id,
        job_id=job_id,
        job_type=job_type,
        error=error,
    )
    return f"failed:{error}"


async def _process_dikiwi_batch_ingestion(drops: list[RainDrop]) -> Any:
    if not dikiwi_mind:
        raise RuntimeError("DIKIWI Mind is not initialized")
    try:
        return await dikiwi_mind.process_inputs_batched(
            drops,
            foundation_only=SETTINGS.dikiwi_foundation_only_ingestion,
        )
    except TypeError as exc:
        if "foundation_only" not in str(exc):
            raise
        return await dikiwi_mind.process_inputs_batched(drops)


def _source_foundation_graph_enabled(job: dict[str, Any]) -> bool:
    return (
        SETTINGS.orchestrator_enabled
        and not SETTINGS.orchestrator_shadow_mode
        and job.get("job_type") in {"process_upload_source", "process_url_source"}
    )


def _workflow_snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "workflow_run_id": snapshot.workflow_run_id,
        "langgraph_thread_id": snapshot.langgraph_thread_id,
        "workflow_kind": snapshot.workflow_kind,
        "status": snapshot.status,
        "current_node": snapshot.current_node,
        "input_summary": snapshot.input_summary,
        "metadata": snapshot.metadata,
        "created_at": snapshot.created_at,
        "updated_at": snapshot.updated_at,
        "completed_at": snapshot.completed_at,
        "last_error": snapshot.last_error,
    }


def _extract_source_ids_from_context_items(items: list[dict[str, Any]]) -> list[str]:
    source_ids: list[str] = []
    for item in items:
        raw_source_id = str(item.get("source_id") or "").strip()
        if raw_source_id and raw_source_id not in source_ids:
            source_ids.append(raw_source_id)
        source_paths = item.get("source_paths", [])
        if isinstance(source_paths, list):
            for raw_path in source_paths:
                value = str(raw_path)
                if value.startswith("source_id:"):
                    source_id = value.removeprefix("source_id:")
                    if source_id and source_id not in source_ids:
                        source_ids.append(source_id)
    return source_ids


def _parse_simple_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    frontmatter: dict[str, Any] = {}
    current_key = ""
    for raw_line in parts[1].splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            frontmatter.setdefault(current_key, []).append(line[4:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        current_key = key.strip()
        value = raw_value.strip()
        if value == "":
            frontmatter[current_key] = []
        elif value.startswith("[") and value.endswith("]"):
            frontmatter[current_key] = [
                item.strip().strip('"').strip("'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        else:
            frontmatter[current_key] = value.strip('"').strip("'")
    return frontmatter, parts[2]


def _context_terms(motive: str, topics: list[dict[str, Any]]) -> list[str]:
    terms: list[str] = []
    for topic in topics:
        label = str(topic.get("label") or "").strip().lower()
        if label and label not in terms:
            terms.append(label)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", motive.lower()):
        if token not in terms:
            terms.append(token)
    return terms[:12]


def _search_vault_knowledge_context(
    *,
    motive: str,
    topics: list[dict[str, Any]],
    required_source_ids: list[str],
    limit: int = 6,
) -> list[dict[str, Any]]:
    vault_path = _studio_vault_path()
    knowledge_dir = vault_path / "03-Knowledge"
    if not knowledge_dir.exists():
        return []
    terms = _context_terms(motive, topics)
    required = {source_id for source_id in required_source_ids if source_id}
    results: list[dict[str, Any]] = []
    for note_path in sorted(knowledge_dir.rglob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            content = note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, body = _parse_simple_frontmatter(content)
        haystack = f"{note_path.name}\n{content}".lower()
        source_id = str(frontmatter.get("source_id") or "").strip()
        source_paths_raw = frontmatter.get("source_paths", [])
        source_paths = source_paths_raw if isinstance(source_paths_raw, list) else [str(source_paths_raw)]
        note_source_ids = {source_id} if source_id else set()
        for raw_path in source_paths:
            value = str(raw_path)
            if value.startswith("source_id:"):
                note_source_ids.add(value.removeprefix("source_id:"))
        if required and not (required & note_source_ids):
            continue
        matched_terms = [term for term in terms if term and term in haystack]
        if terms and not matched_terms and not (required & note_source_ids):
            continue
        results.append(
            {
                "context_type": "vault_knowledge_note",
                "relative_path": str(note_path.relative_to(vault_path)),
                "note_path": str(note_path),
                "title": note_path.stem,
                "source_id": source_id,
                "source_paths": source_paths,
                "semantic_topics": frontmatter.get("semantic_topics", []),
                "matched_terms": matched_terms,
                "excerpt": body.strip()[:1200],
            }
        )
        if len(results) >= limit:
            break
    return results


async def _select_workflow_knowledge_context(
    *,
    motive: str,
    topics: list[dict[str, Any]],
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    required_source_ids = [str(source_id).strip() for source_id in source_ids or [] if str(source_id).strip()]
    vault_context = await asyncio.to_thread(
        _search_vault_knowledge_context,
        motive=motive,
        topics=topics,
        required_source_ids=required_source_ids,
    )
    context: list[dict[str, Any]] = [*vault_context]
    terms = _context_terms(motive, topics)
    try:
        graph_nodes = await graph_db.search_information_nodes(terms, limit=12)
    except Exception:
        graph_nodes = []
    for node in graph_nodes:
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        source_paths = props.get("source_paths", [])
        source_paths = source_paths if isinstance(source_paths, list) else [str(source_paths)]
        node_source_ids = _extract_source_ids_from_context_items(
            [{"source_paths": source_paths, "source_id": props.get("source_id", "")}]
        )
        if required_source_ids and not (set(required_source_ids) & set(node_source_ids)):
            continue
        context.append(
            {
                "context_type": "graph_information_node",
                "node_id": node.get("id"),
                "label": node.get("label"),
                "source": node.get("source"),
                "source_paths": source_paths,
                "source_ids": node_source_ids,
                "source_evidence": props.get("source_evidence", []),
                "pipeline_id": props.get("pipeline_id", ""),
            }
        )
        if len(context) >= 18:
            break
    return context


async def _process_ui_upload(
    upload_id: str,
    source_id: str,
    filename: str,
    content_type: str,
    data: bytes,
) -> None:
    await emit_ui_event(
        "source_ingest_started",
        upload_id=upload_id,
        source_id=source_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
    )
    try:
        async with ui_upload_semaphore:
            await source_store.update_status(source_id, "extracting")
            router = ProcessingRouter(browser_manager=browser_manager_instance)
            extracted = await router.process(data, filename=filename, http_content_type=content_type)
            await source_store.update_status(
                source_id,
                "extracted",
                {
                    "source_type": extracted.source_type,
                    "extracted_chars": len(extracted.text or ""),
                },
            )
            await emit_ui_event(
                "chaos_note_created",
                upload_id=upload_id,
                source_id=source_id,
                filename=filename,
                source_type=extracted.source_type,
                title=extracted.title or Path(filename).stem,
                text_length=len(extracted.text or ""),
            )

            title = extracted.title or Path(filename).stem
            content = extracted.text or ""
            if title:
                content = f"# {title}\n\n{content}".strip()

            drop = RainDrop(
                id="",
                rain_type=RainType.DOCUMENT,
                content=content,
                raw_bytes=b"",
                source="web_upload",
                source_id=source_id,
                stream_type=StreamType.EXTRACT_ANALYZE,
                metadata={
                    "ui_upload_id": upload_id,
                    "source_id": source_id,
                    "filename": filename,
                    "content_type": content_type,
                    "source_type": extracted.source_type,
                    **(extracted.metadata or {}),
                },
            )
            await source_store.update_status(source_id, "processing")
            result = await _process_dikiwi_ingestion(drop)
            terminal_status, terminal_meta = _terminal_status_for_result(result)
            await source_store.update_status(source_id, terminal_status, terminal_meta)
            await emit_ui_event(
                "source_ingest_completed",
                upload_id=upload_id,
                source_id=source_id,
                filename=filename,
                pipeline_id=result.pipeline_id,
                final_stage=result.final_stage_reached.name if result.final_stage_reached else "",
                stage_count=len(result.stage_results),
            )
            if upload_id.startswith("retry-"):
                await emit_ui_event(
                    "source_retry_completed",
                    upload_id=upload_id,
                    source_id=source_id,
                    filename=filename,
                    pipeline_id=result.pipeline_id,
                    final_stage=result.final_stage_reached.name if result.final_stage_reached else "",
                )
    except asyncio.CancelledError:
        await emit_ui_event(
            "upload_cancelled",
            upload_id=upload_id,
            source_id=source_id,
            filename=filename,
        )
        await source_store.update_status(source_id, "cancelled")
        raise
    except Exception as exc:
        logger.exception("UI upload processing failed for %s", filename)
        await emit_ui_event(
            "pipeline_failed",
            upload_id=upload_id,
            source_id=source_id,
            error=str(exc),
        )
        if upload_id.startswith("retry-"):
            await emit_ui_event(
                "source_retry_failed",
                upload_id=upload_id,
                source_id=source_id,
                filename=filename,
                error=str(exc),
            )
        await source_store.update_status(source_id, "failed", {"error": str(exc)})
    finally:
        ui_upload_tasks.pop(upload_id, None)


async def _process_upload_source_job(job: dict[str, Any]) -> str:
    payload = dict(job.get("payload") or {})
    source_id = str(job.get("source_id") or payload.get("source_id") or "")
    upload_id = str(payload.get("upload_id") or f"source-job-{job.get('job_id', '')}")
    batch_id = str(payload.get("batch_id") or "")
    source = await source_store.get_source(source_id)
    if source is None:
        return "failed:source_not_found"
    filename = str(source.get("filename") or payload.get("filename") or source_id)
    content_type = str(source.get("content_type") or payload.get("content_type") or "application/octet-stream")

    await emit_ui_event(
        "source_job_started",
        job_id=job.get("job_id"),
        source_id=source_id,
        upload_id=upload_id,
        batch_id=batch_id or None,
        filename=filename,
        job_type=job.get("job_type"),
        attempt_count=job.get("attempt_count", 0),
    )
    await source_store.update_status(
        source_id,
        "extracting",
        {"job_id": job.get("job_id"), "batch_id": batch_id, "upload_id": upload_id},
    )
    data = await source_store.read_stored_object(source_id)
    router = ProcessingRouter(browser_manager=browser_manager_instance)
    extracted = await router.process(data, filename=filename, http_content_type=content_type)
    await source_store.update_status(
        source_id,
        "extracted",
        {
            "source_type": extracted.source_type,
            "extracted_chars": len(extracted.text or ""),
            "batch_id": batch_id,
            "job_id": job.get("job_id"),
        },
    )
    await emit_ui_event(
        "chaos_note_created",
        batch_id=batch_id or None,
        upload_id=upload_id,
        source_id=source_id,
        filename=filename,
        source_type=extracted.source_type,
        title=extracted.title or Path(filename).stem,
        text_length=len(extracted.text or ""),
    )
    markdown_package = await _canonical_markdown_converter().convert_extracted(
        source_id=source_id,
        extracted=extracted,
        fallback_title=filename,
        metadata={
            "job_id": job.get("job_id"),
            "upload_id": upload_id,
            "batch_id": batch_id,
            "filename": filename,
            "content_type": content_type,
        },
    )
    await emit_ui_event(
        "canonical_markdown_created",
        job_id=job.get("job_id"),
        batch_id=batch_id or None,
        upload_id=upload_id,
        source_id=source_id,
        package_id=markdown_package.package_id,
        markdown_sha256=markdown_package.markdown_sha256,
        package_path=markdown_package.package_path,
        title=markdown_package.title,
        text_length=len(markdown_package.markdown),
    )

    drop = RainDrop(
        id="",
        rain_type=RainType.DOCUMENT,
        content=markdown_package.markdown,
        raw_bytes=markdown_package.markdown.encode("utf-8"),
        source="web_upload_source_job",
        source_id=source_id,
        stream_type=StreamType.EXTRACT_ANALYZE,
        metadata={
            "ui_upload_id": upload_id,
            "batch_id": batch_id,
            "source_id": source_id,
            "filename": filename,
            "content_type": content_type,
            "source_type": extracted.source_type,
            "processing_method": "durable_source_job",
            "canonical_markdown_package_id": markdown_package.package_id,
            "canonical_markdown_path": markdown_package.package_path,
            "canonical_markdown_sha256": markdown_package.markdown_sha256,
            **(extracted.metadata or {}),
        },
    )
    await source_store.update_status(source_id, "processing", {"job_id": job.get("job_id"), "batch_id": batch_id})
    result = await _process_dikiwi_ingestion(drop)
    failed_stage = _failed_stage(result)
    if failed_stage is not None:
        error = failed_stage.error_message or f"{failed_stage.stage.name} failed"
        stage_name = getattr(getattr(failed_stage, "stage", None), "name", "")
        provider, model = _stage_provider_model(result, failed_stage)
        attempt_count = int(job.get("attempt_count") or 0)
        if _is_retryable_processing_error(error) and attempt_count < SETTINGS.source_max_retry_attempts:
            delay = _retry_delay_for_attempt(attempt_count)
            await source_store.mark_retry_pending(
                source_id,
                error=error,
                stage=stage_name,
                provider=provider,
                model=model,
                pipeline_id=result.pipeline_id,
                retry_delay_seconds=delay,
            )
            await emit_ui_event(
                "source_retry_scheduled",
                job_id=job.get("job_id"),
                source_id=source_id,
                upload_id=upload_id,
                batch_id=batch_id or None,
                pipeline_id=result.pipeline_id,
                stage=stage_name,
                error=error,
                retry_delay_seconds=delay,
                attempt_count=attempt_count,
            )
            return "retry"
        await source_store.update_status(
            source_id,
            "failed_retry_exhausted" if _is_retryable_processing_error(error) else "failed",
            {
                "job_id": job.get("job_id"),
                "batch_id": batch_id,
                "pipeline_id": result.pipeline_id,
                "error": error,
                "last_failed_stage": stage_name,
            },
        )
        await emit_ui_event(
            "pipeline_failed",
            job_id=job.get("job_id"),
            source_id=source_id,
            upload_id=upload_id,
            batch_id=batch_id or None,
            pipeline_id=result.pipeline_id,
            error=error,
        )
        return "failed"

    terminal_status, terminal_meta = _terminal_status_for_result(result)
    await source_store.update_status(
        source_id,
        terminal_status,
        {"job_id": job.get("job_id"), "batch_id": batch_id, **terminal_meta},
    )
    await emit_ui_event(
        "source_ingest_completed" if terminal_status == "completed" else "source_ingest_empty",
        job_id=job.get("job_id"),
        upload_id=upload_id,
        source_id=source_id,
        filename=filename,
        batch_id=batch_id or None,
        pipeline_id=result.pipeline_id,
        final_stage=result.final_stage_reached.name if result.final_stage_reached else "",
        stage_count=len(result.stage_results),
    )
    return "completed"


async def _process_url_source_job(job: dict[str, Any]) -> str:
    payload = dict(job.get("payload") or {})
    source_id = str(job.get("source_id") or payload.get("source_id") or "")
    upload_id = str(payload.get("upload_id") or f"source-job-{job.get('job_id', '')}")
    source = await source_store.get_source(source_id)
    if source is None:
        return "failed:source_not_found"
    url = str(source.get("normalized_source") or payload.get("url") or "")
    if not url:
        await source_store.update_status(source_id, "failed", {"error": "URL source has no normalized URL"})
        return "failed:missing_url"

    await emit_ui_event(
        "source_job_started",
        job_id=job.get("job_id"),
        source_id=source_id,
        upload_id=upload_id,
        url=url,
        job_type=job.get("job_type"),
        attempt_count=job.get("attempt_count", 0),
    )
    await source_store.update_status(source_id, "fetching", {"job_id": job.get("job_id"), "upload_id": upload_id})
    await emit_ui_event(
        "url_fetch_started",
        job_id=job.get("job_id"),
        source_id=source_id,
        upload_id=upload_id,
        url=url,
    )

    router = ProcessingRouter(browser_manager=browser_manager_instance)
    extracted = await router.process_url(url, browser_manager=browser_manager_instance)
    if not extracted.text or extracted.text.startswith("[Failed to fetch"):
        raise RuntimeError(extracted.text or "URL extraction returned no text")

    await source_store.update_status(
        source_id,
        "extracted",
        {
            "source_type": extracted.source_type,
            "extracted_chars": len(extracted.text or ""),
            "title": extracted.title or url,
            "job_id": job.get("job_id"),
            "upload_id": upload_id,
        },
    )
    await emit_ui_event(
        "chaos_note_created",
        job_id=job.get("job_id"),
        source_id=source_id,
        upload_id=upload_id,
        source_type=extracted.source_type,
        url=url,
        title=extracted.title or url,
        text_length=len(extracted.text or ""),
    )
    markdown_package = await _canonical_markdown_converter().convert_extracted(
        source_id=source_id,
        extracted=extracted,
        fallback_title=url,
        source_url=url,
        metadata={
            "job_id": job.get("job_id"),
            "upload_id": upload_id,
            "url": url,
        },
    )
    await emit_ui_event(
        "canonical_markdown_created",
        job_id=job.get("job_id"),
        source_id=source_id,
        upload_id=upload_id,
        package_id=markdown_package.package_id,
        markdown_sha256=markdown_package.markdown_sha256,
        package_path=markdown_package.package_path,
        title=markdown_package.title,
        text_length=len(markdown_package.markdown),
        url=url,
    )

    drop = RainDrop(
        id="",
        rain_type=RainType.URL,
        content=markdown_package.markdown,
        raw_bytes=markdown_package.markdown.encode("utf-8"),
        source="studio_url_source_job",
        source_id=source_id,
        stream_type=StreamType.FETCH_ANALYZE,
        metadata={
            "source_id": source_id,
            "upload_id": upload_id,
            "url": url,
            "source_type": extracted.source_type,
            "processing_method": "durable_studio_url_fetch_extract_dikiwi",
            "canonical_markdown_package_id": markdown_package.package_id,
            "canonical_markdown_path": markdown_package.package_path,
            "canonical_markdown_sha256": markdown_package.markdown_sha256,
            **(extracted.metadata or {}),
        },
    )
    await source_store.update_status(source_id, "processing", {"job_id": job.get("job_id"), "upload_id": upload_id})
    result = await _process_dikiwi_ingestion(drop)
    failed_stage = _failed_stage(result)
    if failed_stage is not None:
        error = failed_stage.error_message or f"{failed_stage.stage.name} failed"
        stage_name = getattr(getattr(failed_stage, "stage", None), "name", "")
        provider, model = _stage_provider_model(result, failed_stage)
        attempt_count = int(job.get("attempt_count") or 0)
        if _is_retryable_processing_error(error) and attempt_count < SETTINGS.source_max_retry_attempts:
            delay = _retry_delay_for_attempt(attempt_count)
            await source_store.mark_retry_pending(
                source_id,
                error=error,
                stage=stage_name,
                provider=provider,
                model=model,
                pipeline_id=result.pipeline_id,
                retry_delay_seconds=delay,
            )
            await emit_ui_event(
                "source_retry_scheduled",
                job_id=job.get("job_id"),
                source_id=source_id,
                upload_id=upload_id,
                url=url,
                pipeline_id=result.pipeline_id,
                stage=stage_name,
                error=error,
                retry_delay_seconds=delay,
                attempt_count=attempt_count,
            )
            return "retry"
        await source_store.update_status(
            source_id,
            "failed_retry_exhausted" if _is_retryable_processing_error(error) else "failed",
            {
                "job_id": job.get("job_id"),
                "upload_id": upload_id,
                "pipeline_id": result.pipeline_id,
                "error": error,
                "last_failed_stage": stage_name,
            },
        )
        await emit_ui_event(
            "pipeline_failed",
            job_id=job.get("job_id"),
            source_id=source_id,
            upload_id=upload_id,
            url=url,
            pipeline_id=result.pipeline_id,
            error=error,
        )
        return "failed"

    await source_store.update_status(
        source_id,
        "completed",
        {
            "job_id": job.get("job_id"),
            "upload_id": upload_id,
            "pipeline_id": result.pipeline_id,
            "final_stage": result.final_stage_reached.name if result.final_stage_reached else "",
        },
    )
    await emit_ui_event(
        "source_ingest_completed",
        job_id=job.get("job_id"),
        upload_id=upload_id,
        source_id=source_id,
        url=url,
        pipeline_id=result.pipeline_id,
        final_stage=result.final_stage_reached.name if result.final_stage_reached else "",
        stage_count=len(result.stage_results),
    )
    return "completed"


async def _source_worker_loop(worker_id: str) -> None:
    logger.info("Source worker %s started", worker_id)
    stale_lock_seconds = max(60.0, float(SETTINGS.source_job_stale_lock_seconds))
    while source_worker_stop is None or not source_worker_stop.is_set():
        try:
            recovered = await source_store.requeue_stale_running_source_jobs(
                stale_after_seconds=stale_lock_seconds
            )
            if recovered:
                await emit_ui_event(
                    "source_jobs_requeued_after_stale_lock",
                    worker_id=worker_id,
                    count=recovered,
                    stale_after_seconds=stale_lock_seconds,
                )
            job = await source_store.claim_next_source_job(worker_id=worker_id)
            if job is None:
                await asyncio.sleep(2)
                continue
            result = "failed"
            try:
                if _source_foundation_graph_enabled(job):
                    result = await _process_source_job_with_foundation_graph(job)
                elif job.get("job_type") == "process_upload_source":
                    result = await _process_upload_source_job(job)
                elif job.get("job_type") == "process_url_source":
                    result = await _process_url_source_job(job)
                else:
                    result = f"failed:unknown_job_type:{job.get('job_type')}"
                if result == "completed":
                    await source_store.complete_source_job(str(job["job_id"]))
                elif result == "retry":
                    delay = _retry_delay_for_attempt(int(job.get("attempt_count") or 1))
                    await source_store.retry_source_job(str(job["job_id"]), error="retryable processing failure", delay_seconds=delay)
                else:
                    await source_store.fail_source_job(str(job["job_id"]), error=result)
                    payload = dict(job.get("payload") or {})
                    upload_id = str(payload.get("upload_id") or "")
                    if upload_id.startswith("retry-"):
                        await emit_ui_event(
                            "source_retry_failed",
                            upload_id=upload_id,
                            source_id=str(job.get("source_id") or ""),
                            source_type="url" if job.get("job_type") == "process_url_source" else "upload",
                            error=result,
                        )
            except Exception as exc:
                logger.exception("Source job %s failed", job.get("job_id"))
                attempt_count = int(job.get("attempt_count") or 0)
                error = str(exc)
                payload = dict(job.get("payload") or {})
                upload_id = str(payload.get("upload_id") or "")
                if _is_retryable_processing_error(error) and attempt_count < SETTINGS.source_max_retry_attempts:
                    delay = _retry_delay_for_attempt(attempt_count)
                    await source_store.mark_retry_pending(
                        str(job.get("source_id")),
                        error=error,
                        retry_delay_seconds=delay,
                    )
                    await source_store.retry_source_job(str(job["job_id"]), error=error, delay_seconds=delay)
                else:
                    await source_store.update_status(str(job.get("source_id")), "failed", {"error": error})
                    await source_store.fail_source_job(str(job["job_id"]), error=error)
                    await emit_ui_event(
                        "pipeline_failed",
                        job_id=job.get("job_id"),
                        source_id=str(job.get("source_id") or ""),
                        upload_id=upload_id or None,
                        url=str(payload.get("url") or ""),
                        error=error,
                    )
                    if upload_id.startswith("retry-"):
                        await emit_ui_event(
                            "source_retry_failed",
                            upload_id=upload_id,
                            source_id=str(job.get("source_id") or ""),
                            source_type="url" if job.get("job_type") == "process_url_source" else "upload",
                            error=error,
                        )
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Source worker %s loop error", worker_id)
            await asyncio.sleep(5)
    logger.info("Source worker %s stopped", worker_id)


async def _run_candidate_generation(candidate_id: str, node_ids: list[str], label: str, workflow_run_id: str) -> None:
    """Background task: run approved Insight→Wisdom→Impact over a candidate scope."""
    try:
        if dikiwi_mind is None:
            await synthesis_store.set_status(candidate_id, "approved")
            return
        result = await dikiwi_mind.process_triggered_iwi(
            motive=label or "approved synthesis candidate",
            workflow_run_id=workflow_run_id,
            node_ids=node_ids,
        )
        succeeded = any(getattr(r, "success", False) for r in getattr(result, "stage_results", []))
        await synthesis_store.set_status(
            candidate_id, "generated" if succeeded else "approved", workflow_run_id=workflow_run_id
        )
        logger.info("[SYNTHESIS] generation for %s -> success=%s", candidate_id, succeeded)
    except Exception:
        logger.exception("[SYNTHESIS] candidate generation failed for %s", candidate_id)
        await synthesis_store.set_status(candidate_id, "approved", workflow_run_id=workflow_run_id)


async def _copilot_generate_from_candidate(candidate_id: str) -> dict[str, Any]:
    """Approval hook: kick off generation for an approved candidate in the background.

    Generation (Insight/Wisdom/Impact) is slow, so we return immediately with a
    workflow_run_id; the candidate transitions to 'generated' when it completes.
    """
    candidate = await synthesis_store.get(candidate_id)
    if candidate is None:
        return {"not_found": True, "candidate_id": candidate_id}
    workflow_run_id = f"wf_cand_{candidate_id}"
    node_ids = candidate.get("node_ids") or []
    task = asyncio.create_task(
        _run_candidate_generation(candidate_id, node_ids, candidate.get("scope_label") or "", workflow_run_id)
    )
    workflow_tasks[workflow_run_id] = task
    return {"workflow_run_id": workflow_run_id, "status": "started", "node_count": len(node_ids)}


async def _copilot_retry_source(source_id: str) -> dict[str, Any]:
    """Copilot-facing retry: look up the source, then re-enqueue it.

    Returns ``{"not_found": True}`` when the source id is unknown so the router
    can surface a 404.
    """
    source = await source_store.get_source(source_id)
    if source is None:
        return {"not_found": True, "source_id": source_id}
    return await _retry_source(source)


async def _retry_source(source: dict[str, Any]) -> dict[str, Any]:
    source_id = str(source.get("source_id") or "")
    previous_status = str(source.get("status") or "")
    kind = str(source.get("kind") or "")
    retry_id = f"retry-{source_id.split(':', 1)[-1][:12]}-{len(ui_upload_tasks) + 1}"
    await emit_ui_event(
        "source_retry_started",
        upload_id=retry_id,
        source_id=source_id,
        source_type=kind,
        previous_status=previous_status,
    )
    if kind == "url":
        url = str(source.get("normalized_source") or "")
        if not url:
            reason = "URL source has no normalized URL to retry."
            await source_store.update_status(source_id, "failed", {"retry_error": reason, "retry_upload_id": retry_id})
            await emit_ui_event(
                "source_retry_failed",
                upload_id=retry_id,
                source_id=source_id,
                source_type=kind,
                error=reason,
            )
            return {"source_id": source_id, "retry_id": retry_id, "started": False, "reason": reason}
        await source_store.update_status(source_id, "queued", {"retry_upload_id": retry_id})
        job = await source_store.enqueue_source_job(
            source_id=source_id,
            job_type="process_url_source",
            payload={"upload_id": retry_id, "url": url, "retry_of_status": previous_status},
            priority=50,
        )
        await emit_ui_event(
            "source_job_queued",
            upload_id=retry_id,
            source_id=source_id,
            job_id=job["job_id"],
            job_type=job["job_type"],
            url=url,
        )
        return {"source_id": source_id, "retry_id": retry_id, "started": True, "job_id": job["job_id"]}

    if kind != "upload":
        reason = "Only stored upload and URL sources can be retried through the current Studio processing path."
        await source_store.update_status(source_id, "failed", {"retry_error": reason, "retry_upload_id": retry_id})
        await emit_ui_event(
            "source_retry_failed",
            upload_id=retry_id,
            source_id=source_id,
            source_type=kind,
            error=reason,
        )
        return {"source_id": source_id, "retry_id": retry_id, "started": False, "reason": reason}

    try:
        await source_store.read_stored_object(source_id)
    except Exception as exc:
        await source_store.update_status(source_id, "failed", {"retry_error": str(exc), "retry_upload_id": retry_id})
        await emit_ui_event(
            "source_retry_failed",
            upload_id=retry_id,
            source_id=source_id,
            source_type=kind,
            error=str(exc),
        )
        return {"source_id": source_id, "retry_id": retry_id, "started": False, "reason": str(exc)}

    filename = str(source.get("filename") or f"retry-{retry_id}")
    content_type = str(source.get("content_type") or "application/octet-stream")
    await source_store.update_status(source_id, "queued", {"retry_upload_id": retry_id})
    job = await source_store.enqueue_source_job(
        source_id=source_id,
        job_type="process_upload_source",
        payload={
            "upload_id": retry_id,
            "filename": filename,
            "content_type": content_type,
            "retry_of_status": previous_status,
        },
        priority=50,
    )
    await emit_ui_event(
        "source_job_queued",
        upload_id=retry_id,
        source_id=source_id,
        job_id=job["job_id"],
        job_type=job["job_type"],
        filename=filename,
    )
    return {"source_id": source_id, "retry_id": retry_id, "started": True}


async def _process_ui_url(source_id: str, url: str, *, retry_id: str | None = None) -> None:
    await emit_ui_event("url_fetch_started", source_id=source_id, url=url)
    try:
        await source_store.update_status(source_id, "fetching")
        router = ProcessingRouter(browser_manager=browser_manager_instance)
        extracted = await router.process_url(url, browser_manager=browser_manager_instance)
        if not extracted.text or extracted.text.startswith("[Failed to fetch"):
            raise RuntimeError(extracted.text or "URL extraction returned no text")

        await source_store.update_status(
            source_id,
            "extracted",
            {
                "source_type": extracted.source_type,
                "extracted_chars": len(extracted.text or ""),
                "title": extracted.title or url,
            },
        )
        await emit_ui_event(
            "chaos_note_created",
            source_id=source_id,
            source_type=extracted.source_type,
            url=url,
            title=extracted.title or url,
            text_length=len(extracted.text or ""),
        )

        title = extracted.title or url
        content = extracted.text or ""
        if title:
            content = f"# {title}\n\n{content}".strip()

        drop = RainDrop(
            id="",
            rain_type=RainType.URL,
            content=content,
            raw_bytes=(extracted.text or "").encode("utf-8"),
            source="studio_url",
            source_id=source_id,
            stream_type=StreamType.FETCH_ANALYZE,
            metadata={
                "source_id": source_id,
                "url": url,
                "source_type": extracted.source_type,
                "processing_method": "studio_url_fetch_extract_dikiwi",
                **(extracted.metadata or {}),
            },
        )
        await source_store.update_status(source_id, "processing")
        result = await _process_dikiwi_ingestion(drop)
        await source_store.update_status(
            source_id,
            "completed",
            {
                "pipeline_id": result.pipeline_id,
                "final_stage": result.final_stage_reached.name if result.final_stage_reached else "",
            },
        )
        await emit_ui_event(
            "source_ingest_completed",
            upload_id=retry_id,
            source_id=source_id,
            url=url,
            pipeline_id=result.pipeline_id,
            final_stage=result.final_stage_reached.name if result.final_stage_reached else "",
            stage_count=len(result.stage_results),
        )
        if retry_id:
            await emit_ui_event(
                "source_retry_completed",
                upload_id=retry_id,
                source_id=source_id,
                source_type="url",
                url=url,
                pipeline_id=result.pipeline_id,
                final_stage=result.final_stage_reached.name if result.final_stage_reached else "",
            )
    except asyncio.CancelledError:
        await emit_ui_event("url_processing_cancelled", source_id=source_id, url=url)
        await source_store.update_status(source_id, "cancelled")
        raise
    except Exception as exc:
        logger.exception("UI URL processing failed for %s", url)
        await emit_ui_event("pipeline_failed", source_id=source_id, url=url, error=str(exc))
        if retry_id:
            await emit_ui_event(
                "source_retry_failed",
                upload_id=retry_id,
                source_id=source_id,
                source_type="url",
                url=url,
                error=str(exc),
            )
        await source_store.update_status(source_id, "failed", {"error": str(exc)})
    finally:
        ui_upload_tasks.pop(retry_id or f"url-{source_id}", None)


async def _read_upload_bytes(file: UploadFile, max_bytes: int) -> bytes:
    chunk_size = 1024 * 1024
    total = 0
    buffer = bytearray()

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds max upload size of {max_bytes} bytes",
            )
        buffer.extend(chunk)

    await file.seek(0)
    return bytes(buffer)


async def _handle_ui_upload(file: UploadFile, upload_id: str) -> dict[str, Any]:
    await _ensure_source_queue_capacity(1)
    data = await _read_upload_bytes(file, SETTINGS.max_file_size)
    filename = file.filename or f"upload-{upload_id}"
    content_type = file.content_type or "application/octet-stream"
    source_record = await source_store.store_upload(
        upload_id=upload_id,
        filename=filename,
        content_type=content_type,
        data=data,
        metadata={"intake": "studio_upload"},
    )
    await emit_ui_event(
        "source_stored",
        upload_id=upload_id,
        source_id=source_record["source_id"],
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        duplicate=source_record["duplicate"],
        sha256=source_record["sha256"],
    )
    should_enqueue = _should_enqueue_upload_source(source_record)
    if should_enqueue:
        await source_store.update_status(
            source_record["source_id"],
            "queued",
            {"upload_id": upload_id, "intake": "studio_upload"},
        )
        try:
            job = await source_store.enqueue_source_job(
                source_id=source_record["source_id"],
                job_type="process_upload_source",
                payload={
                    "upload_id": upload_id,
                    "filename": filename,
                    "content_type": content_type,
                },
                max_pending=SETTINGS.source_job_max_pending,
            )
        except SourceJobCapacityError as exc:
            await source_store.update_status(source_record["source_id"], "deferred", {"queue_error": str(exc)})
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        await emit_ui_event(
            "source_job_queued",
            upload_id=upload_id,
            source_id=source_record["source_id"],
            job_id=job["job_id"],
            job_type=job["job_type"],
            filename=filename,
        )
    return {
        "upload_id": upload_id,
        "source_id": source_record["source_id"],
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "sha256": source_record["sha256"],
        "duplicate": source_record["duplicate"],
        "status": "queued" if should_enqueue else "duplicate",
    }


async def _handle_ui_upload_batch(files: list[tuple[UploadFile, str]], batch_id: str) -> dict[str, Any]:
    if not dikiwi_mind:
        raise HTTPException(status_code=503, detail="DIKIWI Mind is not initialized")
    await _ensure_source_queue_capacity(len(files))

    accepted: list[dict[str, Any]] = []
    stored_items: list[dict[str, Any]] = []
    for file, upload_id in files:
        data = await _read_upload_bytes(file, SETTINGS.max_file_size)
        filename = file.filename or f"upload-{upload_id}"
        content_type = file.content_type or "application/octet-stream"
        await emit_ui_event(
            "source_uploaded",
            batch_id=batch_id,
            upload_id=upload_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
        )
        source_record = await source_store.store_upload(
            upload_id=upload_id,
            filename=filename,
            content_type=content_type,
            data=data,
            metadata={"intake": "studio_upload", "batch_id": batch_id},
        )
        await emit_ui_event(
            "source_stored",
            batch_id=batch_id,
            upload_id=upload_id,
            source_id=source_record["source_id"],
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            duplicate=source_record["duplicate"],
            sha256=source_record["sha256"],
        )
        should_enqueue = _should_enqueue_upload_source(source_record)
        item = {
            "upload_id": upload_id,
            "source_id": source_record["source_id"],
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(data),
            "sha256": source_record["sha256"],
            "duplicate": source_record["duplicate"],
            "status": "queued" if should_enqueue else "duplicate",
        }
        if should_enqueue:
            await source_store.update_status(
                source_record["source_id"],
                "queued",
                {"upload_id": upload_id, "batch_id": batch_id, "intake": "studio_upload"},
            )
            try:
                job = await source_store.enqueue_source_job(
                    source_id=source_record["source_id"],
                    job_type="process_upload_source",
                    payload={
                        "upload_id": upload_id,
                        "batch_id": batch_id,
                        "filename": filename,
                        "content_type": content_type,
                    },
                    max_pending=SETTINGS.source_job_max_pending,
                )
            except SourceJobCapacityError as exc:
                await source_store.update_status(source_record["source_id"], "deferred", {"queue_error": str(exc)})
                raise HTTPException(status_code=429, detail=str(exc)) from exc
            await emit_ui_event(
                "source_job_queued",
                batch_id=batch_id,
                upload_id=upload_id,
                source_id=source_record["source_id"],
                job_id=job["job_id"],
                job_type=job["job_type"],
                filename=filename,
            )
        stored_items.append(item)
        accepted.append(dict(item))

    await emit_ui_event(
        "upload_batch_queued",
        batch_id=batch_id,
        queued=sum(1 for item in accepted if item["status"] == "queued"),
        duplicates=sum(1 for item in stored_items if item["duplicate"]),
    )
    return {"batch_id": batch_id, "uploads": accepted, "status": "queued"}


async def _process_ui_upload_batch(batch_id: str, items: list[dict[str, Any]]) -> None:
    process_items = [item for item in items if not item["duplicate"]]
    try:
        if not process_items:
            await emit_ui_event("upload_batch_completed", batch_id=batch_id, processed=0, duplicates=len(items))
            return

        router = ProcessingRouter(browser_manager=browser_manager_instance)
        drops: list[RainDrop] = []
        item_by_source: dict[str, dict[str, Any]] = {}

        await emit_ui_event(
            "batch_chaos_started",
            batch_id=batch_id,
            source_count=len(process_items),
        )
        for item in process_items:
            await source_store.update_status(item["source_id"], "extracting")
            data = await source_store.read_stored_object(item["source_id"])
            extracted = await router.process(
                data,
                filename=item["filename"],
                http_content_type=item["content_type"],
            )
            await source_store.update_status(
                item["source_id"],
                "extracted",
                {
                    "source_type": extracted.source_type,
                    "extracted_chars": len(extracted.text or ""),
                    "batch_id": batch_id,
                },
            )
            await emit_ui_event(
                "chaos_note_created",
                batch_id=batch_id,
                upload_id=item["upload_id"],
                source_id=item["source_id"],
                filename=item["filename"],
                source_type=extracted.source_type,
                title=extracted.title or Path(item["filename"]).stem,
                text_length=len(extracted.text or ""),
            )
            markdown_package = await _canonical_markdown_converter().convert_extracted(
                source_id=item["source_id"],
                extracted=extracted,
                fallback_title=item["filename"],
                metadata={
                    "batch_id": batch_id,
                    "upload_id": item["upload_id"],
                    "filename": item["filename"],
                    "content_type": item["content_type"],
                },
            )
            await emit_ui_event(
                "canonical_markdown_created",
                batch_id=batch_id,
                upload_id=item["upload_id"],
                source_id=item["source_id"],
                package_id=markdown_package.package_id,
                markdown_sha256=markdown_package.markdown_sha256,
                package_path=markdown_package.package_path,
                title=markdown_package.title,
                text_length=len(markdown_package.markdown),
            )

            drop = RainDrop(
                id="",
                rain_type=RainType.DOCUMENT,
                content=markdown_package.markdown,
                raw_bytes=markdown_package.markdown.encode("utf-8"),
                source="web_upload_batch",
                source_id=item["source_id"],
                stream_type=StreamType.EXTRACT_ANALYZE,
                metadata={
                    "ui_upload_id": item["upload_id"],
                    "batch_id": batch_id,
                    "source_id": item["source_id"],
                    "filename": item["filename"],
                    "content_type": item["content_type"],
                    "source_type": extracted.source_type,
                    "canonical_markdown_package_id": markdown_package.package_id,
                    "canonical_markdown_path": markdown_package.package_path,
                    "canonical_markdown_sha256": markdown_package.markdown_sha256,
                    **(extracted.metadata or {}),
                },
            )
            drops.append(drop)
            item_by_source[item["source_id"]] = item

        await emit_ui_event(
            "batch_chaos_completed",
            batch_id=batch_id,
            source_count=len(drops),
        )
        for item in process_items:
            await source_store.update_status(item["source_id"], "processing", {"batch_id": batch_id})

        batch = await _process_dikiwi_batch_ingestion(drops)
        for drop, result in zip(drops, batch.results):
            item = item_by_source.get(drop.source_id)
            if item is None:
                continue
            final_stage = result.final_stage_reached.name if result.final_stage_reached else ""
            failed_stage = next((stage for stage in result.stage_results if not stage.success), None)
            if failed_stage is None:
                await source_store.update_status(
                    item["source_id"],
                    "completed",
                    {
                        "batch_id": batch_id,
                        "pipeline_id": result.pipeline_id,
                        "final_stage": final_stage,
                    },
                )
            else:
                await source_store.update_status(
                    item["source_id"],
                    "failed",
                    {
                        "batch_id": batch_id,
                        "pipeline_id": result.pipeline_id,
                        "error": failed_stage.error_message or f"{failed_stage.stage.name} failed",
                    },
                )

        # Value generation (Insight/Wisdom/Impact, business artifacts) is no
        # longer auto-triggered on ingestion. It runs only via the explicit,
        # approval-gated value workflow. Foundation ingestion stops at Knowledge.
        business_result = {"ran": False, "reason": "auto_business_flow_disabled", "proposal_count": 0}
        await emit_ui_event(
            "upload_batch_completed",
            batch_id=batch_id,
            processed=len(process_items),
            duplicates=len(items) - len(process_items),
            incremental_ratio=batch.incremental_ratio,
            incremental_threshold=batch.incremental_threshold,
            higher_order_triggered=batch.higher_order_triggered,
            pipeline_ids=[result.pipeline_id for result in batch.results],
            business_flow=business_result,
        )
    except asyncio.CancelledError:
        await emit_ui_event("upload_batch_cancelled", batch_id=batch_id, source_count=len(process_items))
        for item in process_items:
            await source_store.update_status(item["source_id"], "cancelled", {"batch_id": batch_id})
        raise
    except Exception as exc:
        logger.exception("UI upload batch processing failed for %s", batch_id)
        await emit_ui_event("pipeline_failed", batch_id=batch_id, error=str(exc))
        for item in process_items:
            await source_store.update_status(item["source_id"], "failed", {"batch_id": batch_id, "error": str(exc)})
    finally:
        ui_upload_tasks.pop(batch_id, None)


async def _handle_ui_url(url: str) -> dict[str, Any]:
    await _ensure_source_queue_capacity(1)
    source = await source_store.store_url(url=url, metadata={"intake": "studio_url"})
    should_enqueue = (not source["duplicate"]) or str(source.get("status") or "") in {
        "stored",
        "deferred",
        "failed",
        "failed_retry_exhausted",
        "cancelled",
        "retry_pending",
    }
    job: dict[str, Any] | None = None
    if should_enqueue:
        await source_store.update_status(source["source_id"], "queued", {"intake": "studio_url", "url": url})
        try:
            job = await source_store.enqueue_source_job(
                source_id=source["source_id"],
                job_type="process_url_source",
                payload={"upload_id": f"url-{source['source_id']}", "url": url},
                max_pending=SETTINGS.source_job_max_pending,
            )
        except SourceJobCapacityError as exc:
            await source_store.update_status(source["source_id"], "deferred", {"queue_error": str(exc)})
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        await emit_ui_event(
            "source_job_queued",
            upload_id=f"url-{source['source_id']}",
            source_id=source["source_id"],
            job_id=job["job_id"],
            job_type=job["job_type"],
            url=url,
        )
    return {
        **source,
        "status": "queued" if should_enqueue else "duplicate",
        "processing": should_enqueue,
        "job_id": job["job_id"] if job else None,
    }


async def _handle_ui_text(title: str, text: str) -> dict[str, Any]:
    await _ensure_source_queue_capacity(1)
    source = await source_store.store_text(text=text, title=title, metadata={"intake": "studio_text"})
    text_id = f"text-{source['source_id']}"
    should_enqueue = (not source["duplicate"]) or str(source.get("status") or "") in {
        "stored",
        "deferred",
        "failed",
        "failed_retry_exhausted",
        "cancelled",
        "retry_pending",
    }
    job: dict[str, Any] | None = None
    if should_enqueue:
        await source_store.update_status(
            source["source_id"],
            "queued",
            {"intake": "studio_text", "title": source["title"], "upload_id": text_id},
        )
        try:
            job = await source_store.enqueue_source_job(
                source_id=source["source_id"],
                job_type="process_upload_source",
                payload={
                    "upload_id": text_id,
                    "filename": source["filename"],
                    "content_type": source["content_type"],
                    "source_kind": "text",
                },
                max_pending=SETTINGS.source_job_max_pending,
            )
        except SourceJobCapacityError as exc:
            await source_store.update_status(source["source_id"], "deferred", {"queue_error": str(exc)})
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        await emit_ui_event(
            "source_job_queued",
            upload_id=text_id,
            source_id=source["source_id"],
            job_id=job["job_id"],
            job_type=job["job_type"],
            source_type="text",
            title=source["title"],
        )
    return {
        **source,
        "status": "queued" if should_enqueue else "duplicate",
        "processing": should_enqueue,
        "job_id": job["job_id"] if job else None,
    }


async def _ui_status_provider() -> dict[str, Any]:
    queue_counts = await db.get_job_counts()
    source_job_counts = await source_store.get_source_job_counts()
    provider_pressure = await provider_backpressure.snapshot()
    graph_counts = {}
    for node_type in [
        "data",
        "information",
        "knowledge",
        "insight",
        "wisdom",
        "impact",
        "proposal",
        "business",
    ]:
        try:
            graph_counts[node_type] = await graph_db.count_nodes_by_type(node_type)
        except Exception:
            graph_counts[node_type] = 0

    source_workers_running = sum(1 for task in source_worker_tasks if not task.done())
    inbox_snapshot = inbox_watcher.snapshot() if inbox_watcher else {
        "running": False,
        "inbox_path": str(SETTINGS.resolved_inbox_path),
    }
    workflow_counts = {
        "active": sum(1 for task in workflow_tasks.values() if not task.done()),
    }
    return {
        "queue": queue_counts,
        "source_jobs": source_job_counts,
        "provider_pressure": provider_pressure,
        "graph": graph_counts,
        "active_pipelines": [
            pipeline_id
            for pipeline_id in ui_event_hub.active_pipeline_ids()
            if source_job_counts.get("running", 0) > 0
        ],
        "active_uploads": sorted(ui_upload_tasks.keys()),
        "daemons": {
            "source_workers": source_workers_running,
            "inbox_watcher": bool(inbox_watcher and inbox_watcher.running),
            "workflow_runner": workflow_counts["active"],
        },
        "minds": {
            "dikiwi": dikiwi_mind is not None,
        },
        "inbox": inbox_snapshot,
        "workflows": workflow_counts,
    }


async def _ui_graph_provider() -> dict[str, Any]:
    return await graph_db.get_graph_snapshot()


async def _ui_pipeline_provider(pipeline_id: str) -> dict[str, Any] | None:
    events = ui_event_hub.pipeline_trace(pipeline_id)
    if not events:
        return None
    last = events[-1]
    return {
        "pipeline_id": pipeline_id,
        "status": last.get("type"),
        "events": events,
    }


async def _ui_sources_provider(limit: int, offset: int) -> dict[str, Any]:
    return await source_store.list_sources(limit=limit, offset=offset)


async def _ui_source_detail_provider(source_id: str) -> dict[str, Any] | None:
    return await source_store.get_source(source_id)


async def _ui_source_jobs_provider(limit: int, offset: int, status: str | None = None) -> dict[str, Any]:
    return await source_store.list_source_jobs(limit=limit, offset=offset, status=status)


async def _ui_workflows_provider(limit: int, offset: int, status: str | None = None) -> dict[str, Any]:
    safe_status = status if status in {"queued", "running", "interrupted", "completed", "failed", "cancelled"} else None
    runs = await workflow_run_store.list_runs(limit=limit, offset=offset, status=safe_status)
    return {
        "total": len(runs),
        "workflows": [_workflow_snapshot_payload(run) for run in runs],
    }


async def _ui_create_chat_thread(payload: dict[str, Any]) -> dict[str, Any]:
    thread = await chat_store.create_thread(
        title=str(payload.get("title") or "").strip(),
        metadata={"created_from": "ui_chat"},
    )
    await emit_ui_event("chat_thread_created", chat_thread_id=thread["chat_thread_id"])
    return thread


async def _ui_chat_threads_provider(limit: int, offset: int) -> dict[str, Any]:
    return await chat_store.list_threads(limit=limit, offset=offset)


async def _ui_chat_thread_detail_provider(chat_thread_id: str) -> dict[str, Any] | None:
    return await chat_store.get_thread(chat_thread_id)


async def _business_graph_select_context(
    motive: str,
    topics: list[dict[str, Any]],
    source_ids: list[str],
) -> list[dict[str, Any]]:
    return await _select_workflow_knowledge_context(
        motive=motive,
        topics=topics,
        source_ids=source_ids,
    )


async def _business_graph_run_iwi(motive: str, workflow_run_id: str, node_ids: list[str]) -> Any:
    if dikiwi_mind is None or not hasattr(dikiwi_mind, "process_triggered_iwi"):
        raise RuntimeError("DIKIWI triggered I/W/I runner is unavailable")
    return await dikiwi_mind.process_triggered_iwi(
        motive=motive,
        workflow_run_id=workflow_run_id,
        node_ids=node_ids,
    )


async def _business_graph_run_research(state: dict[str, Any]) -> dict[str, Any]:
    topics = state.get("topics", [])
    topic = ""
    if topics and isinstance(topics[0], dict):
        topic = str(topics[0].get("label") or "")
    if not topic:
        topic = str(state.get("motive") or "")[:120]
    query = f"{topic} market technology competitors risks"
    service = TavilyResearchService(store=research_store)
    return await service.create_and_run_packet(
        workflow_run_id=str(state.get("workflow_run_id") or ""),
        topic=topic,
        trigger="chat_motive",
        query=query,
        topic_extraction_id=str(state.get("topic_extraction_id") or ""),
        model="mini",
        internal_context=list(state.get("knowledge_context", [])),
        max_results=5,
    )


async def _business_graph_run_business_plan(state: dict[str, Any]) -> dict[str, Any]:
    synthesizer = BusinessPlanSynthesizer(
        store=business_plan_store,
        vault_path=_studio_vault_path(),
    )
    research_ids = [str(item) for item in state.get("research_ids", []) if str(item)]
    research_jobs = [
        job
        for research_id in research_ids
        if (job := await research_store.get_research_job(research_id)) is not None
    ]
    evaluations = await synthesizer.run_team_evaluations(
        workflow_run_id=str(state.get("workflow_run_id") or ""),
        workflow_plan_id=str(state.get("workflow_plan_id") or ""),
        motive=str(state.get("motive") or ""),
        knowledge_context=list(state.get("knowledge_context", [])),
        iwi_result=dict(state.get("iwi_result", {})),
        research_jobs=research_jobs,
        second_opinion_packets=list(state.get("second_opinion_packets", [])),
    )
    business_plan = await synthesizer.synthesize_business_plan(
        workflow_run_id=str(state.get("workflow_run_id") or ""),
        workflow_plan_id=str(state.get("workflow_plan_id") or ""),
        motive=str(state.get("motive") or ""),
        evaluations=evaluations,
        knowledge_context=list(state.get("knowledge_context", [])),
        research_jobs=research_jobs,
        second_opinion_packets=list(state.get("second_opinion_packets", [])),
    )
    return {
        "evaluations": evaluations,
        "business_plan": business_plan,
    }


async def _ui_chat_message_handler(chat_thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    content = str(payload.get("content") or payload.get("motive") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Chat message content is required")
    role = str(payload.get("role") or "user").strip()
    if role != "user":
        raise HTTPException(status_code=400, detail="Only user chat messages can trigger workflow planning")
    source_ids = [str(item).strip() for item in payload.get("source_ids", []) if str(item).strip()]
    run = await workflow_run_store.create_run(
        workflow_kind="business_planning",
        input_summary=content[:500],
        metadata={
            "motive": content,
            "chat_thread_id": chat_thread_id,
            "source_ids": source_ids,
            "attachment_ids": payload.get("attachment_ids", []),
            "trigger": "ui_chat_message",
        },
    )
    await emit_ui_event(
        "workflow_run_queued",
        workflow_run_id=run.workflow_run_id,
        workflow_kind=run.workflow_kind,
        chat_thread_id=chat_thread_id,
        motive=content,
        source_ids=source_ids,
    )
    dependencies = BusinessPlanningDependencies(
        chat_store=chat_store,
        workflow_run_store=workflow_run_store,
        select_context=_business_graph_select_context,
        run_iwi=_business_graph_run_iwi,
        run_research=_business_graph_run_research,
        run_business_plan=_business_graph_run_business_plan,
        emit_event=emit_ui_event,
    )
    async with async_sqlite_checkpointer(SETTINGS.langgraph_checkpoint_db_path) as checkpointer:
        graph = build_business_planning_graph(checkpointer, dependencies=dependencies)
        graph_result = await graph.ainvoke(
            {
                "workflow_run_id": run.workflow_run_id,
                "langgraph_thread_id": run.langgraph_thread_id,
                "workflow_kind": "business_planning",
                "status": "queued",
                "steps": [],
                "motive": content,
                "chat_thread_id": chat_thread_id,
                "source_ids": source_ids,
                "research_required": bool(payload.get("research_required", False)),
            },
            {"configurable": {"thread_id": run.langgraph_thread_id}},
        )
    interrupt_payload = {}
    if graph_result.get("__interrupt__"):
        interrupt_payload = graph_result["__interrupt__"][0].value
    workflow_plan_id = str(interrupt_payload.get("workflow_plan_id") or graph_result.get("workflow_plan_id") or "")
    workflow_plan = await chat_store.get_workflow_plan(workflow_plan_id) if workflow_plan_id else None
    if workflow_plan is None:
        raise HTTPException(status_code=500, detail="Business planning graph did not create a workflow plan")
    message = await chat_store.get_message(workflow_plan["message_id"])
    topic_extraction = await chat_store.get_topic_extraction(workflow_plan["topic_extraction_id"])
    chat_thread = await chat_store.get_thread(chat_thread_id)
    messages = chat_thread.get("messages", []) if chat_thread else []
    assistant_message = next(
        (
            item
            for item in reversed(messages)
            if item.get("role") == "assistant"
            and item.get("metadata", {}).get("workflow_plan_id") == workflow_plan_id
        ),
        None,
    )
    return {
        "message": message,
        "assistant_message": assistant_message,
        "topic_extraction": topic_extraction,
        "workflow_plan": workflow_plan,
        "workflow_run": _workflow_snapshot_payload(await workflow_run_store.get_run(run.workflow_run_id) or run),
        "graph_interrupt": interrupt_payload,
    }


async def _ui_workflow_plan_provider(workflow_plan_id: str) -> dict[str, Any] | None:
    return await chat_store.get_workflow_plan(workflow_plan_id)


async def _ui_research_jobs_provider(limit: int) -> dict[str, Any]:
    items = await research_store.list_research_jobs(limit=limit)
    return {"total": len(items), "items": items}


async def _ui_research_job_provider(research_id: str) -> dict[str, Any] | None:
    return await research_store.get_research_job(research_id)


async def _ui_second_opinion_provider(limit: int) -> dict[str, Any]:
    items = await research_store.list_second_opinions(limit=limit)
    return {"total": len(items), "items": items}


async def _ui_business_plan_provider(limit: int) -> dict[str, Any]:
    items = await business_plan_store.list_business_plans(limit=limit)
    return {"total": len(items), "items": items}


async def _ui_business_plan_detail_provider(business_plan_id: str) -> dict[str, Any] | None:
    return await business_plan_store.get_business_plan(business_plan_id)


async def _dispatch_approved_workflow_plan(
    workflow_plan: dict[str, Any],
    *,
    run_inline: bool = False,
) -> dict[str, Any]:
    if workflow_plan.get("status") not in {"approved", "dispatched"}:
        raise HTTPException(status_code=409, detail="Workflow plan must be approved before dispatch")
    node_ids = [
        str(item.get("node_id")).strip()
        for item in workflow_plan.get("knowledge_context", [])
        if item.get("context_type") == "graph_information_node" and str(item.get("node_id") or "").strip()
    ]
    if not node_ids:
        raise HTTPException(
            status_code=409,
            detail="Workflow plan has no graph-backed Knowledge context for triggered I/W/I",
        )
    source_ids = _extract_source_ids_from_context_items(workflow_plan.get("knowledge_context", []))
    run = await workflow_run_store.create_run(
        workflow_kind="triggered_iwi",
        input_summary=str(workflow_plan.get("motive") or "")[:500],
        metadata={
            "motive": workflow_plan.get("motive", ""),
            "node_ids": node_ids,
            "source_ids": source_ids,
            "trigger": "confirmed_workflow_plan",
            "workflow_plan_id": workflow_plan["workflow_plan_id"],
            "topic_extraction_id": workflow_plan["topic_extraction_id"],
            "chat_thread_id": workflow_plan["chat_thread_id"],
            "message_id": workflow_plan["message_id"],
        },
    )
    await chat_store.mark_workflow_plan_dispatched(
        workflow_plan["workflow_plan_id"],
        workflow_run_id=run.workflow_run_id,
    )
    await chat_store.add_message(
        workflow_plan["chat_thread_id"],
        role="assistant",
        content="Confirmed workflow plan dispatched for I/W/I synthesis.",
        metadata={
            "workflow_plan_id": workflow_plan["workflow_plan_id"],
            "workflow_run_id": run.workflow_run_id,
            "notification_type": "workflow_plan_dispatched",
        },
    )
    await emit_ui_event(
        "workflow_run_queued",
        workflow_run_id=run.workflow_run_id,
        workflow_kind=run.workflow_kind,
        workflow_plan_id=workflow_plan["workflow_plan_id"],
        chat_thread_id=workflow_plan["chat_thread_id"],
        motive=workflow_plan.get("motive", ""),
        node_ids=node_ids,
        source_ids=source_ids,
    )
    if run_inline:
        await _run_iwi_workflow(
            run.workflow_run_id,
            motive=str(workflow_plan.get("motive") or ""),
            node_ids=node_ids,
        )
    else:
        workflow_tasks[run.workflow_run_id] = asyncio.create_task(
            _run_iwi_workflow(
                run.workflow_run_id,
                motive=str(workflow_plan.get("motive") or ""),
                node_ids=node_ids,
            )
        )
    updated = await workflow_run_store.get_run(run.workflow_run_id)
    return {
        "workflow_plan": await chat_store.get_workflow_plan(workflow_plan["workflow_plan_id"]),
        "workflow_run": _workflow_snapshot_payload(updated or run),
    }


async def _ui_workflow_plan_confirm_handler(workflow_plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    approved = payload.get("approved") is True
    plan = await chat_store.get_workflow_plan(workflow_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Workflow plan not found")

    plan_metadata = plan.get("metadata", {})
    graph_workflow_run_id = str(plan_metadata.get("workflow_run_id") or "").strip()
    graph_created = plan_metadata.get("created_from") == "business_planning_graph"
    graph_result: dict[str, Any] | None = None
    business_workflow_run = None

    if graph_created and plan.get("status") == "awaiting_confirmation":
        if not graph_workflow_run_id:
            raise HTTPException(status_code=409, detail="Workflow plan is missing its business-planning workflow run")
        business_workflow_run = await workflow_run_store.get_run(graph_workflow_run_id)
        if business_workflow_run is None:
            raise HTTPException(status_code=404, detail="Business-planning workflow run not found")
        dependencies = BusinessPlanningDependencies(
            chat_store=chat_store,
            workflow_run_store=workflow_run_store,
            select_context=_business_graph_select_context,
            run_iwi=_business_graph_run_iwi,
            run_research=_business_graph_run_research,
            run_business_plan=_business_graph_run_business_plan,
            emit_event=emit_ui_event,
        )
        async with async_sqlite_checkpointer(SETTINGS.langgraph_checkpoint_db_path) as checkpointer:
            graph = build_business_planning_graph(checkpointer, dependencies=dependencies)
            graph_result = await graph.ainvoke(
                Command(
                    resume={
                        "approved": approved,
                        "decided_by": str(payload.get("decided_by") or "user"),
                        "dispatch_iwi": payload.get("dispatch", True) is not False,
                        "dispatch_research": payload.get("dispatch_research") is True,
                        "dispatch_business_plan": payload.get("dispatch_business_plan") is True,
                    }
                ),
                {"configurable": {"thread_id": business_workflow_run.langgraph_thread_id}},
            )
        plan = await chat_store.get_workflow_plan(workflow_plan_id)
        business_workflow_run = await workflow_run_store.get_run(graph_workflow_run_id)
    else:
        plan = await chat_store.set_workflow_plan_decision(
            workflow_plan_id,
            approved=approved,
            decided_by=str(payload.get("decided_by") or "user"),
            metadata={"decision_source": "ui_workflow_plan_confirm"},
        )
        await emit_ui_event(
            "workflow_plan_confirmed" if approved else "workflow_plan_rejected",
            workflow_plan_id=workflow_plan_id,
            chat_thread_id=plan["chat_thread_id"],
            status=plan["status"],
        )

    if plan is None:
        raise HTTPException(status_code=500, detail="Workflow plan confirmation failed")
    if not approved or payload.get("dispatch", True) is False:
        return {
            "workflow_plan": plan,
            "business_workflow_run": (
                _workflow_snapshot_payload(business_workflow_run)
                if business_workflow_run is not None
                else None
            ),
            "graph_result": graph_result,
        }
    if graph_created:
        business_workflow_run = (
            await workflow_run_store.get_run(graph_workflow_run_id)
            if graph_workflow_run_id
            else business_workflow_run
        )
        return {
            "workflow_plan": plan,
            "business_workflow_run": (
                _workflow_snapshot_payload(business_workflow_run)
                if business_workflow_run is not None
                else None
            ),
            "graph_result": graph_result,
        }
    return await _dispatch_approved_workflow_plan(plan, run_inline=bool(payload.get("run_inline", False)))


async def _run_iwi_workflow(workflow_run_id: str, *, motive: str, node_ids: list[str]) -> None:
    await workflow_run_store.update_status(
        workflow_run_id,
        status="running",
        current_node="trigger_iwi",
        metadata={"node_ids": node_ids},
    )
    await emit_ui_event(
        "workflow_run_started",
        workflow_run_id=workflow_run_id,
        workflow_kind="triggered_iwi",
        motive=motive,
        node_ids=node_ids,
    )
    try:
        if dikiwi_mind is None or not hasattr(dikiwi_mind, "process_triggered_iwi"):
            raise RuntimeError("DIKIWI triggered I/W/I runner is unavailable")
        result = await dikiwi_mind.process_triggered_iwi(
            motive=motive,
            workflow_run_id=workflow_run_id,
            node_ids=node_ids,
        )
        failed_stage = _failed_stage(result)
        final_stage = result.final_stage_reached.name if result.final_stage_reached else ""
        if failed_stage is not None:
            error = failed_stage.error_message or f"{failed_stage.stage.name} failed"
            await workflow_run_store.update_status(
                workflow_run_id,
                status="failed",
                current_node=failed_stage.stage.name,
                metadata={
                    "pipeline_id": result.pipeline_id,
                    "final_stage": final_stage,
                    "stage_count": len(result.stage_results),
                },
                last_error=error,
            )
            await emit_ui_event(
                "workflow_run_failed",
                workflow_run_id=workflow_run_id,
                workflow_kind="triggered_iwi",
                pipeline_id=result.pipeline_id,
                final_stage=final_stage,
                error=error,
            )
            return
        await workflow_run_store.update_status(
            workflow_run_id,
            status="completed",
            current_node=final_stage or "completed",
            metadata={
                "pipeline_id": result.pipeline_id,
                "final_stage": final_stage,
                "stage_count": len(result.stage_results),
            },
        )
        await emit_ui_event(
            "workflow_run_completed",
            workflow_run_id=workflow_run_id,
            workflow_kind="triggered_iwi",
            pipeline_id=result.pipeline_id,
            final_stage=final_stage,
            stage_count=len(result.stage_results),
        )
    except Exception as exc:
        logger.exception("Triggered I/W/I workflow failed: %s", workflow_run_id)
        await workflow_run_store.update_status(
            workflow_run_id,
            status="failed",
            current_node="trigger_iwi",
            last_error=str(exc),
        )
        await emit_ui_event(
            "workflow_run_failed",
            workflow_run_id=workflow_run_id,
            workflow_kind="triggered_iwi",
            error=str(exc),
        )
    finally:
        workflow_tasks.pop(workflow_run_id, None)


async def _ui_iwi_workflow_trigger(payload: dict[str, Any]) -> dict[str, Any]:
    motive = str(payload.get("motive", "")).strip()
    workflow_plan_id = str(payload.get("workflow_plan_id") or "").strip()
    if workflow_plan_id:
        workflow_plan = await chat_store.get_workflow_plan(workflow_plan_id)
        if workflow_plan is None:
            raise HTTPException(status_code=404, detail="Workflow plan not found")
        return await _dispatch_approved_workflow_plan(
            workflow_plan,
            run_inline=bool(payload.get("run_inline", False)),
        )

    chat_thread_id = str(payload.get("chat_thread_id") or "").strip()
    if not chat_thread_id:
        thread = await chat_store.create_thread(
            title=motive[:80],
            metadata={"created_from": "ui_iwi_workflow_trigger"},
        )
        chat_thread_id = thread["chat_thread_id"]
    plan_payload = await _ui_chat_message_handler(
        chat_thread_id,
        {
            "role": "user",
            "content": motive,
            "source_ids": payload.get("source_ids", []),
            "attachment_ids": payload.get("attachment_ids", []),
            "research_required": payload.get("research_required", False),
        },
    )
    return {
        "status": "awaiting_confirmation",
        "chat_thread_id": chat_thread_id,
        "message": plan_payload["message"],
        "topic_extraction": plan_payload["topic_extraction"],
        "workflow_plan": plan_payload["workflow_plan"],
    }


def _studio_vault_path() -> Path:
    return Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path).expanduser()


def _read_vault_notes(stage_dir: str, limit: int) -> dict[str, Any]:
    safe_limit = min(max(1, limit), 200)
    directory = _studio_vault_path() / stage_dir
    if not directory.exists():
        return {"stage": stage_dir, "total": 0, "items": []}
    files = sorted(directory.rglob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    items: list[dict[str, Any]] = []
    for path in files[:safe_limit]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        title = path.stem
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip() or title
                break
        items.append(
            {
                "title": title,
                "note_path": str(path),
                "relative_path": str(path.relative_to(_studio_vault_path())),
                "size_bytes": path.stat().st_size,
                "updated_at": path.stat().st_mtime,
                "preview": content[:1200],
            }
        )
    return {"stage": stage_dir, "total": len(files), "items": items}


async def _ui_proposals_provider(limit: int) -> dict[str, Any]:
    payload = await asyncio.to_thread(_read_vault_notes, "07-Proposal", limit)
    try:
        graph_count = await graph_db.count_nodes_by_type("reactor_proposal")
    except Exception:
        graph_count = 0
    payload["graph_reactor_proposal_count"] = graph_count
    return payload


async def _ui_entrepreneurship_provider(limit: int) -> dict[str, Any]:
    payload = await asyncio.to_thread(_read_vault_notes, "08-Entrepreneurship", limit)
    try:
        business_count = await graph_db.count_nodes_by_type("business")
    except Exception:
        business_count = 0
    payload["graph_business_count"] = business_count
    return payload


async def _ui_vault_notes_provider(stage: str, limit: int) -> dict[str, Any]:
    return await asyncio.to_thread(_read_vault_notes, stage, limit)


async def _ui_control_handler(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action in {"cancel_upload", "cancel_batch"}:
        target_id = str(payload.get("upload_id") or payload.get("batch_id") or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="upload_id or batch_id is required")
        task = ui_upload_tasks.get(target_id)
        if task is None:
            return {"action": action, "target_id": target_id, "cancelled": False, "reason": "not_active"}
        task.cancel()
        await emit_ui_event("upload_cancel_requested", upload_id=target_id)
        return {"action": action, "target_id": target_id, "cancelled": True}

    if action == "cancel_all_uploads":
        target_ids = sorted(ui_upload_tasks)
        for task in list(ui_upload_tasks.values()):
            task.cancel()
        source_job_ids = await source_store.cancel_running_source_jobs()
        await emit_ui_event("uploads_cancel_requested", upload_ids=target_ids, count=len(target_ids))
        return {
            "action": action,
            "cancelled": len(target_ids),
            "upload_ids": target_ids,
            "source_job_ids": source_job_ids,
        }

    if action == "retry_failed_sources":
        failed = await source_store.list_failed_sources(limit=500)
        await emit_ui_event(
            "retry_failed_sources_requested",
            source_ids=[source["source_id"] for source in failed],
            count=len(failed),
        )
        retries = [await _retry_source(source) for source in failed]
        return {
            "action": action,
            "retry_requested": len(failed),
            "retry_started": sum(1 for retry in retries if retry.get("started")),
            "source_ids": [source["source_id"] for source in failed],
            "retries": retries,
        }

    if action == "create_backup":
        backup_path_raw = str(payload.get("backup_path") or "").strip()
        backup_path = Path(backup_path_raw).expanduser() if backup_path_raw else SETTINGS.aily_data_dir / "backups" / "aily-backup.zip"
        manifest = await asyncio.to_thread(
            create_backup,
            vault_path=_studio_vault_path(),
            graph_db_path=SETTINGS.graph_db_path,
            source_store_db_path=SETTINGS.source_store_db_path,
            source_object_dir=SETTINGS.source_object_dir,
            output_path=backup_path,
        )
        await audit_logger.log("backup_created", backup_path=str(backup_path), manifest=manifest.to_dict())
        return {"action": action, "backup_path": str(backup_path), "manifest": manifest.to_dict()}

    if action == "restore_backup_dry_run":
        backup_path = Path(str(payload.get("backup_path") or "")).expanduser()
        if not backup_path.exists():
            raise HTTPException(status_code=400, detail="backup_path does not exist")
        restore_dir = SETTINGS.aily_data_dir / "restore-dry-run"
        restored = await asyncio.to_thread(restore_backup, backup_path=backup_path, restore_dir=restore_dir)
        await audit_logger.log("backup_restore_dry_run", backup_path=str(backup_path), restore_dir=str(restore_dir))
        return {"action": action, **restored}

    raise HTTPException(status_code=400, detail=f"Unsupported control action: {action}")


def _validate_runtime_security_config() -> None:
    errors = SETTINGS.validate_runtime_security()
    if errors:
        raise RuntimeError("; ".join(errors))


def _render_status_note(snapshot: dict[str, Any]) -> str:
    """Pure: render the vault-visible Aily Status note from a snapshot.

    This is the user's "is my brain alive / what's new / what needs me" view on
    Obsidian Publish — no terminal required.
    """
    counts = snapshot.get("counts", {})
    recent = snapshot.get("recent", [])
    attention = snapshot.get("attention", [])
    candidates = snapshot.get("candidates", [])
    updated = snapshot.get("updated", "")
    lines = [
        "---",
        "aily_generated: true",
        "note_type: system",
        f"updated: {updated}",
        "---",
        "",
        "# Aily Status",
        "",
        f"🟢 Engine running — last heartbeat {updated}",
        "",
        "## Queue",
        f"- queued: {counts.get('queued', 0)} · processing: {counts.get('processing', 0)} "
        f"· completed: {counts.get('completed', 0)} · empty: {counts.get('completed_empty', 0)} "
        f"· failed: {counts.get('failed', 0)}",
        f"- pending synthesis candidates: {len(candidates)}",
        "",
        "## Recently refined",
    ]
    if recent:
        lines += [f"- {r['title']} — {r['status']} ({r['updated']})" for r in recent]
    else:
        lines.append("- (nothing yet — drop a file into the inbox)")
    if attention:
        lines += ["", "## Needs your attention"]
        lines += [f"- {a}" for a in attention]
    if candidates:
        lines += ["", "## Synthesis candidates awaiting approval"]
        lines += [f"- {c['scope_label']} (readiness {c['readiness_score']:.1f})" for c in candidates[:10]]
    lines.append("")
    return "\n".join(lines)


async def _write_status_note() -> None:
    """Gather a status snapshot and write it to <vault>/99-System/Aily Status.md."""
    vault = SETTINGS.resolved_vault_path
    if not vault:
        return
    listing = await source_store.list_sources(limit=200, offset=0)
    rows = listing.get("sources", []) if isinstance(listing, dict) else []
    counts: dict[str, int] = {}
    for r in rows:
        counts[str(r.get("status") or "")] = counts.get(str(r.get("status") or ""), 0) + 1

    def _title(r: dict[str, Any]) -> str:
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        return str(r.get("filename") or md.get("title") or r.get("normalized_source") or r.get("source_id") or "source")

    recent = [{"title": _title(r), "status": str(r.get("status") or ""), "updated": str(r.get("updated_at") or "")} for r in rows[:10]]
    attention: list[str] = []
    for r in rows:
        st = str(r.get("status") or "")
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        if st == "completed_empty":
            attention.append(f"⚠️ {_title(r)} produced no notes — {md.get('empty_reason', 'needs review')}")
        elif st in {"failed", "failed_retry_exhausted"}:
            attention.append(f"❌ {_title(r)} failed — {md.get('error', 'see logs')}")
    candidates = []
    try:
        candidates = await synthesis_store.list(status="pending", limit=20)
    except Exception:
        pass
    snapshot = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "recent": recent,
        "attention": attention[:15],
        "candidates": candidates,
    }
    note = _render_status_note(snapshot)
    target = Path(vault).expanduser() / "99-System" / "Aily Status.md"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, note, "utf-8")
    except OSError as exc:
        logger.warning("Failed to write status note: %s", exc)


def _prune_backups(backup_dir: Path, *, keep: int = 7) -> None:
    try:
        zips = sorted(backup_dir.glob("aily-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in zips[keep:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


async def _run_daily_backup() -> bool:
    """Snapshot derived state (vault + sources + graph + source store) to a dated zip."""
    vault = SETTINGS.resolved_vault_path
    if not vault:
        return False
    out = SETTINGS.aily_data_dir / "backups" / f"aily-backup-{datetime.now().strftime('%Y-%m-%d')}.zip"
    try:
        await asyncio.to_thread(
            create_backup,
            vault_path=Path(vault).expanduser(),
            graph_db_path=SETTINGS.graph_db_path,
            source_store_db_path=SETTINGS.source_store_db_path,
            source_object_dir=SETTINGS.source_object_dir,
            output_path=out,
        )
        _prune_backups(out.parent, keep=7)
        logger.info("Daily backup written: %s", out)
        return True
    except Exception:
        logger.exception("Daily backup failed")
        return False


async def _heartbeat_loop(stop_event: asyncio.Event) -> None:
    """Refresh the vault-visible Aily Status note (every 60s) and take one
    backup per day (the heartbeat tick doubles as the daily-backup scheduler)."""
    last_backup_date = None
    while not stop_event.is_set():
        try:
            await _write_status_note()
            today = datetime.now().date()
            if last_backup_date != today and await _run_daily_backup():
                last_backup_date = today
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[HEARTBEAT] refresh/backup failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60.0)
        except TimeoutError:
            continue


async def _synthesis_detection_loop(stop_event: asyncio.Event) -> None:
    """Automatic, cheap detection: enqueue ripe synthesis candidates.

    Two triggers feed the one candidate queue: a knowledge-growth threshold
    (burst responder, checked each tick) and a daily routine (slow-drip catcher).
    Generation stays approval-gated — this only recommends.
    """
    from aily.synthesis import SynthesisDetector

    detector = SynthesisDetector(
        graph_db,
        synthesis_store,
        trigger_score=SETTINGS.dikiwi_network_trigger_score,
        min_nodes=SETTINGS.dikiwi_network_min_nodes,
        max_candidate_nodes=SETTINGS.dikiwi_network_max_candidate_nodes,
    )
    tick_seconds = 300.0
    last_daily_date = None
    while not stop_event.is_set():
        try:
            if await detector.should_run_threshold(SETTINGS.synthesis_knowledge_growth_threshold):
                await detector.detect(detected_via="threshold")
            now = datetime.now()
            due = (now.hour, now.minute) >= (SETTINGS.synthesis_daily_hour, SETTINGS.synthesis_daily_minute)
            if due and last_daily_date != now.date():
                await detector.detect(detected_via="daily")
                last_daily_date = now.date()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[SYNTHESIS] detection loop error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    global dikiwi_mind
    global browser_manager_instance
    global source_worker_stop, source_worker_tasks, inbox_watcher
    global synthesis_detection_task, heartbeat_task
    _validate_runtime_security_config()
    await db.initialize()
    await graph_db.initialize()
    await source_store.initialize()
    # Cold-start recovery: requeue any source jobs left 'running' by a prior
    # crash/reboot so they don't stall until the stale-lock timeout.
    try:
        recovered = await source_store.requeue_stale_running_source_jobs(stale_after_seconds=0)
        if recovered:
            logger.info("Cold-start recovered %d stranded source job(s)", recovered)
    except Exception:
        logger.exception("Cold-start source-job recovery failed")
    await workflow_run_store.initialize()
    await chat_store.initialize()
    await synthesis_store.initialize()
    await research_store.initialize()
    await business_plan_store.initialize()
    ui_event_hub.configure_persistence(SETTINGS.ui_event_log_path)
    loaded_ui_events = await ui_event_hub.load_persisted(limit=SETTINGS.ui_event_trace_limit)
    if loaded_ui_events:
        logger.info("Loaded %d persisted UI events", loaded_ui_events)

    # Initialize browser manager for JS-rendered pages (Monica, etc.)
    browser_manager = None
    try:
        from aily.browser.manager import BrowserUseManager
        browser_manager = BrowserUseManager()
        await browser_manager.start()
    except Exception:
        logger.warning("Browser manager failed to start, continuing without JS rendering support")
    browser_manager_instance = browser_manager

    # Initialize Three-Mind System FIRST (needed for WebSocket routing)
    try:
        dikiwi_writer = None
        vault_path_for_dikiwi = SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path
        if vault_path_for_dikiwi:
            dikiwi_writer = DikiwiObsidianWriter(
                vault_path=vault_path_for_dikiwi,
                folder_prefix="",
                zettelkasten_only=True,
            )

        # DIKIWI Mind - continuous knowledge processing
        dikiwi_mind = DikiwiMind(
            llm_client=llm_resolver("dikiwi"),
            llm_client_resolver=llm_resolver,
            graph_db=graph_db,
            enabled=SETTINGS.minds.dikiwi_enabled,
            obsidian_writer=writer,
            dikiwi_obsidian_writer=dikiwi_writer,
            queue_db=db,
            browser_manager=browser_manager,
        )
        logger.info("DIKIWI Mind initialized (enabled=%s)", SETTINGS.minds.dikiwi_enabled)
    except Exception:
        logger.exception("Failed to initialize DIKIWI Mind")
        dikiwi_mind = None

    logger.info("DIKIWI continuous processing active; foundation ingestion stops at Knowledge")

    # URL parser registrations (used by URL ingestion path)
    registry.register(r"^https://(www\.)?kimi\.(moonshot\.cn|com)/share/", parse_kimi)
    registry.register(r"^https://monica\.im/", parse_monica)
    registry.register(r"^https://arxiv\.org/abs/", parse_arxiv)
    registry.register(r"^https://github\.com/", parse_github)
    registry.register(r"^https://(www\.)?youtube\.com/watch", parse_youtube)

    source_worker_stop = asyncio.Event()
    source_worker_tasks = [
        asyncio.create_task(_source_worker_loop(f"source-worker-{index + 1}"))
        for index in range(max(0, int(SETTINGS.source_worker_count)))
    ]
    if SETTINGS.synthesis_detection_enabled:
        synthesis_detection_task = asyncio.create_task(
            _synthesis_detection_loop(source_worker_stop)
        )
        logger.info("Synthesis detection loop started (recommend-only, approval-gated)")
    heartbeat_task = asyncio.create_task(_heartbeat_loop(source_worker_stop))
    logger.info("Heartbeat loop started (writes 99-System/Aily Status.md)")
    if SETTINGS.inbox_watcher_enabled:
        inbox_watcher = WatchedInboxService(
            source_store=source_store,
            inbox_path=SETTINGS.resolved_inbox_path,
            poll_interval_seconds=SETTINGS.inbox_poll_interval_seconds,
            file_stable_seconds=SETTINGS.inbox_file_stable_seconds,
            max_pending_jobs=SETTINGS.source_job_max_pending,
            archive_processed=SETTINGS.inbox_archive_processed,
            emit_event=emit_ui_event,
        )
        await inbox_watcher.start()
    logger.info("Aily startup complete")
    yield
    if source_worker_stop:
        source_worker_stop.set()
    if synthesis_detection_task is not None:
        synthesis_detection_task.cancel()
        await asyncio.gather(synthesis_detection_task, return_exceptions=True)
        synthesis_detection_task = None
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        heartbeat_task = None
    for task in workflow_tasks.values():
        task.cancel()
    if workflow_tasks:
        await asyncio.gather(*workflow_tasks.values(), return_exceptions=True)
        workflow_tasks.clear()
    if inbox_watcher:
        await inbox_watcher.stop()
        inbox_watcher = None
    for task in source_worker_tasks:
        task.cancel()
    if source_worker_tasks:
        await asyncio.gather(*source_worker_tasks, return_exceptions=True)
        source_worker_tasks = []
    await business_plan_store.close()
    await research_store.close()
    await synthesis_store.close()
    await chat_store.close()
    await workflow_run_store.close()
    await source_store.close()
    await fetcher.stop()
    if browser_manager:
        try:
            await browser_manager.stop()
        except Exception:
            pass
    logger.info("Aily shutdown complete")


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://obsidian.md", "capacitor://localhost", "http://localhost", "http://127.0.0.1"],
    allow_origin_regex=r"^app://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def hosted_mode_guard(request: Request, call_next):
    response = await call_next(request)
    if SETTINGS.hosted_mode and SETTINGS.ui_auth_token and request.query_params.get("token") == SETTINGS.ui_auth_token:
        response.set_cookie(
            "aily_ui_token",
            SETTINGS.ui_auth_token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
    if request.url.path.startswith("/api/ui"):
        await audit_logger.log(
            "ui_request",
            path=request.url.path,
            method=request.method,
            status_code=response.status_code,
            client=request.client.host if request.client else "unknown",
        )
    return response


@app.post("/api/ui/logout", include_in_schema=False)
async def ui_logout(request: Request) -> JSONResponse:
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(
        "aily_ui_token",
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    await audit_logger.log(
        "ui_logout",
        client=request.client.host if request.client else "unknown",
    )
    return response


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, Any]:
    return {"status": "ok", "hosted_mode": SETTINGS.hosted_mode}


@app.get("/ready", include_in_schema=False)
async def ready() -> dict[str, Any]:
    issues: list[str] = []
    vault = SETTINGS.resolved_vault_path
    if not vault:
        issues.append("No vault configured. Set OBSIDIAN_VAULT_PATH (or DIKIWI_VAULT_PATH).")
    elif not Path(vault).expanduser().exists():
        issues.append(f"Vault path does not exist: {vault}")
    llm_key_configured = bool(SETTINGS.active_llm_key)
    if SETTINGS.minds.dikiwi_enabled and not llm_key_configured:
        issues.append(
            f"No LLM API key configured for provider '{SETTINGS.llm_provider}'. "
            "Set LLM_API_KEY or the provider-specific key (e.g. KIMI_API_KEY) — "
            "required for the Data/Information/Knowledge pipeline."
        )
    issues.extend(SETTINGS.validate_runtime_security())
    return {
        "status": "ready" if not issues else "not_ready",
        "ready": not issues,
        "issues": issues,
        "vault_path": vault,
        "vault_configured": bool(vault),
        "llm_provider": SETTINGS.llm_provider,
        "llm_key_configured": llm_key_configured,
        "inbox_path": str(SETTINGS.resolved_inbox_path),
        "graph_db_configured": bool(SETTINGS.graph_db_path),
        "source_store_configured": bool(SETTINGS.source_store_db_path),
        "studio_auth_required": SETTINGS.hosted_mode or SETTINGS.ui_auth_enabled,
    }

app.include_router(
    create_copilot_router(
        vault_path=_studio_vault_path(),
        llm_client_factory=lambda: llm_resolver("copilot.chat"),
        auth_token=SETTINGS.ui_auth_token if (SETTINGS.ui_auth_enabled or SETTINGS.hosted_mode) else "",
        rate_limiter=ui_rate_limiter,
        trust_proxy_headers=SETTINGS.trusted_proxy_headers,
        workflow_run_store=workflow_run_store,
        source_store=source_store,
        source_retry_handler=_copilot_retry_source,
        synthesis_store=synthesis_store,
        candidate_generate_handler=_copilot_generate_from_candidate,
        candidate_cooldown_hours=SETTINGS.synthesis_candidate_cooldown_hours,
    )
)
app.include_router(
    create_ui_router(
        upload_handler=_handle_ui_upload,
        batch_upload_handler=_handle_ui_upload_batch,
        url_handler=_handle_ui_url,
        text_handler=_handle_ui_text,
        status_provider=_ui_status_provider,
        graph_provider=_ui_graph_provider,
        pipeline_provider=_ui_pipeline_provider,
        source_provider=_ui_sources_provider,
        source_detail_provider=_ui_source_detail_provider,
        source_jobs_provider=_ui_source_jobs_provider,
        workflow_trigger_handler=_ui_iwi_workflow_trigger,
        workflow_provider=_ui_workflows_provider,
        chat_thread_create_handler=_ui_create_chat_thread,
        chat_thread_provider=_ui_chat_threads_provider,
        chat_thread_detail_provider=_ui_chat_thread_detail_provider,
        chat_message_handler=_ui_chat_message_handler,
        workflow_plan_provider=_ui_workflow_plan_provider,
        workflow_plan_confirm_handler=_ui_workflow_plan_confirm_handler,
        research_jobs_provider=_ui_research_jobs_provider,
        research_job_provider=_ui_research_job_provider,
        second_opinion_provider=_ui_second_opinion_provider,
        business_plan_provider=_ui_business_plan_provider,
        business_plan_detail_provider=_ui_business_plan_detail_provider,
        proposal_provider=_ui_proposals_provider,
        entrepreneurship_provider=_ui_entrepreneurship_provider,
        vault_notes_provider=_ui_vault_notes_provider,
        control_handler=_ui_control_handler,
        run_registry=RunRegistry(SETTINGS.evidence_runs_dir),
        enable_uploads=HAS_MULTIPART,
        max_files_per_request=SETTINGS.ui_max_upload_files,
        max_upload_bytes=SETTINGS.max_file_size,
        auth_token=SETTINGS.ui_auth_token if (SETTINGS.ui_auth_enabled or SETTINGS.hosted_mode) else "",
        rate_limiter=ui_rate_limiter,
        trust_proxy_headers=SETTINGS.trusted_proxy_headers,
    )
)


if __name__ == "__main__":
    uvicorn.run("aily.main:app", host="127.0.0.1", port=8000, reload=False)
