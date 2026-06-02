from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from aily.chaos.chaos_markdown import render_chaos_markdown
from aily.chaos.types import ExtractedContentMultimodal
from aily.gating.drainage import RainDrop, RainType, StreamType
from aily.orchestration.state import WorkflowState
from aily.writer.vault_layout import write_canonical_markdown_vault_artifact


@dataclass(frozen=True)
class SourceFoundationDependencies:
    source_store: Any
    processing_router_factory: Callable[[], Any]
    canonical_markdown_converter_factory: Callable[[], Any]
    dikiwi_ingestion: Callable[[RainDrop], Awaitable[Any]]
    emit_event: Callable[..., Awaitable[None]]
    browser_manager: Any = None
    workflow_run_store: Any | None = None
    vault_path: Any | None = None
    failed_stage: Callable[[Any], Any | None] | None = None


def _append_step(state: WorkflowState, step: str, **updates: Any) -> WorkflowState:
    steps = [*state.get("steps", []), step]
    return {
        **updates,
        "steps": steps,
        "current_node": step,
        "status": updates.get("status", "running"),
    }


def register_source(state: WorkflowState) -> WorkflowState:
    return _append_step(
        state,
        "register_source",
        source_id=state.get("source_id") or f"source:{state['workflow_run_id']}",
    )


def convert_to_markdown(state: WorkflowState) -> WorkflowState:
    return _append_step(
        state,
        "convert_to_markdown",
        canonical_document_id=state.get("canonical_document_id")
        or f"canonical:{state['workflow_run_id']}",
    )


def run_data(state: WorkflowState) -> WorkflowState:
    return _append_step(state, "run_data")


def run_information(state: WorkflowState) -> WorkflowState:
    return _append_step(state, "run_information")


def run_knowledge(state: WorkflowState) -> WorkflowState:
    return _append_step(state, "run_knowledge", status="completed")


def _metadata(state: WorkflowState) -> dict[str, Any]:
    return dict(state.get("metadata") or {})


def _job_payload(state: WorkflowState) -> dict[str, Any]:
    return dict(_metadata(state).get("job_payload") or {})


def _enum_name(value: Any) -> str:
    if isinstance(value, Enum):
        return value.name
    name = getattr(value, "name", None)
    return str(name or value or "")


def _stage_results_payload(result: Any) -> list[dict[str, Any]]:
    return [
        {
            "stage": _enum_name(getattr(stage_result, "stage", "")),
            "success": bool(getattr(stage_result, "success", False)),
            "error_message": getattr(stage_result, "error_message", None),
            "items_processed": int(getattr(stage_result, "items_processed", 0) or 0),
            "items_output": int(getattr(stage_result, "items_output", 0) or 0),
        }
        for stage_result in getattr(result, "stage_results", [])
    ]


def _stage_success(state: WorkflowState, stage_name: str) -> bool:
    return any(
        str(stage.get("stage") or "").upper() == stage_name and bool(stage.get("success"))
        for stage in state.get("stage_results", [])
    )


def _failed_stage(dependencies: SourceFoundationDependencies, result: Any) -> Any | None:
    if dependencies.failed_stage is not None:
        return dependencies.failed_stage(result)
    for stage_result in getattr(result, "stage_results", []):
        if not bool(getattr(stage_result, "success", False)):
            return stage_result
    return None


def _source_lineage_paths(state: WorkflowState) -> list[str]:
    paths: list[str] = []
    source_id = str(state.get("source_id") or "").strip()
    if source_id:
        paths.append(f"source_id:{source_id}")
    for key in (
        "origin_path",
        "storage_path",
        "canonical_markdown_path",
        "canonical_markdown_vault_path",
        "chaos_markdown_vault_path",
    ):
        value = str(state.get(key) or "").strip()
        if value and value not in paths:
            paths.append(value)
    return paths


def _source_lineage(state: WorkflowState) -> dict[str, Any]:
    return {
        "source_id": state.get("source_id", ""),
        "origin_path": state.get("origin_path", ""),
        "storage_path": state.get("storage_path", ""),
        "canonical_markdown_package_id": state.get("canonical_document_id", ""),
        "canonical_markdown_path": state.get("canonical_markdown_path", ""),
        "canonical_markdown_vault_path": state.get("canonical_markdown_vault_path", ""),
        "canonical_markdown_sha256": state.get("canonical_markdown_sha256", ""),
        "source_paths": _source_lineage_paths(state),
    }


def _safe_chaos_base_name(filename: str) -> str:
    stem = Path(filename or "source").stem
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "-", stem).strip(" .-_")
    safe = re.sub(r"\s+", " ", safe)
    return safe[:90] or "source"


async def _write_pdf_chaos_artifact(
    *,
    dependencies: SourceFoundationDependencies,
    source_file: Path,
    extracted: Any,
    filename: str,
    source_id: str = "",
    batch_id: str = "",
    evidence_run_id: str = "",
) -> dict[str, Any]:
    vault = Path(dependencies.vault_path).expanduser().resolve()
    base_name = _safe_chaos_base_name(filename)
    target = vault / "00-Chaos" / f"{base_name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    multimodal = ExtractedContentMultimodal(
        text=str(getattr(extracted, "text", "") or ""),
        title=getattr(extracted, "title", None) or Path(filename).stem,
        source_type=str(getattr(extracted, "source_type", "") or "pdf"),
        source_path=source_file,
        metadata={
            **dict(getattr(extracted, "metadata", {}) or {}),
            "source_id": source_id,
            "batch_id": batch_id,
            "evidence_run_id": evidence_run_id,
        },
        processing_method="source_foundation_pdf_chaos",
    )
    extra_origin_fields = {
        key: value
        for key, value in {
            "source_id": source_id,
            "batch_id": batch_id,
            "evidence_run_id": evidence_run_id,
        }.items()
        if value
    }
    rendered = await render_chaos_markdown(
        extracted=multimodal,
        source_path=source_file,
        base_name=base_name,
        vault_path=vault,
        source_display_name=filename,
        extra_origin_fields=extra_origin_fields,
    )
    target.write_text(rendered.markdown, encoding="utf-8")
    return {
        "path": str(target),
        "relative_path": str(target.relative_to(vault)),
        "page_count": rendered.page_count,
        "screenshot_count": rendered.screenshot_count,
        "screenshot_renderer": rendered.screenshot_renderer,
        "screenshot_error": rendered.screenshot_error,
    }


async def _mark_workflow(
    dependencies: SourceFoundationDependencies,
    state: WorkflowState,
    *,
    status: str,
    current_node: str,
    metadata: dict[str, Any] | None = None,
    last_error: str | None = None,
) -> None:
    if dependencies.workflow_run_store is None:
        return
    await dependencies.workflow_run_store.update_status(
        state["workflow_run_id"],
        status=status,
        current_node=current_node,
        metadata=metadata,
        last_error=last_error,
    )


async def _register_source_node(
    state: WorkflowState,
    dependencies: SourceFoundationDependencies,
) -> WorkflowState:
    payload = _job_payload(state)
    source_id = str(state.get("source_id") or payload.get("source_id") or "")
    source = await dependencies.source_store.get_source(source_id)
    if source is None:
        raise FileNotFoundError(f"Source not found: {source_id}")

    job_id = str(state.get("job_id") or payload.get("job_id") or "")
    job_type = str(state.get("job_type") or payload.get("job_type") or "")
    upload_id = str(state.get("upload_id") or payload.get("upload_id") or f"source-job-{job_id}")
    batch_id = str(state.get("batch_id") or payload.get("batch_id") or "")
    filename = str(source.get("filename") or payload.get("filename") or source_id)
    content_type = str(source.get("content_type") or payload.get("content_type") or "application/octet-stream")
    source_metadata = dict(source.get("metadata") or {})
    evidence_run_id = str(
        state.get("evidence_run_id")
        or payload.get("evidence_run_id")
        or source_metadata.get("evidence_run_id")
        or ""
    )
    origin_path = str(source_metadata.get("origin_path") or payload.get("origin_path") or "")
    storage_path = str(source.get("storage_path") or "")
    is_url = job_type == "process_url_source"
    url = str(source.get("normalized_source") or payload.get("url") or "") if is_url else ""

    await _mark_workflow(
        dependencies,
        state,
        status="running",
        current_node="register_source",
        metadata={"source_id": source_id, "job_id": job_id, "job_type": job_type},
    )
    await dependencies.emit_event(
        "source_job_started",
        workflow_run_id=state.get("workflow_run_id"),
        job_id=job_id or None,
        source_id=source_id,
        upload_id=upload_id,
        batch_id=batch_id or None,
        filename=None if is_url else filename,
        url=url or None,
        job_type=job_type,
    )
    await dependencies.source_store.update_status(
        source_id,
        "fetching" if is_url else "extracting",
        {
            "workflow_run_id": state.get("workflow_run_id"),
            "job_id": job_id,
            "batch_id": batch_id,
            "upload_id": upload_id,
            "evidence_run_id": evidence_run_id,
        },
    )
    if is_url:
        await dependencies.emit_event(
            "url_fetch_started",
            workflow_run_id=state.get("workflow_run_id"),
            job_id=job_id or None,
            source_id=source_id,
            upload_id=upload_id,
            url=url,
        )
    return _append_step(
        state,
        "register_source",
        source_id=source_id,
        job_id=job_id,
        job_type=job_type,
        upload_id=upload_id,
        batch_id=batch_id,
        evidence_run_id=evidence_run_id,
        filename=filename,
        content_type=content_type,
        url=url,
        origin_path=origin_path,
        storage_path=storage_path,
        source_metadata=source_metadata,
    )


async def _convert_to_markdown_node(
    state: WorkflowState,
    dependencies: SourceFoundationDependencies,
) -> WorkflowState:
    source_id = state["source_id"]
    job_id = state.get("job_id", "")
    upload_id = state.get("upload_id", "")
    batch_id = state.get("batch_id", "")
    url = state.get("url", "")
    source_record = await dependencies.source_store.get_source(source_id)
    source_metadata = dict((source_record or {}).get("metadata") or {})
    origin_path = str(state.get("origin_path") or source_metadata.get("origin_path") or "")
    storage_path = str(state.get("storage_path") or (source_record or {}).get("storage_path") or "")
    existing_package = None
    if hasattr(dependencies.source_store, "get_markdown_package"):
        existing_package = await dependencies.source_store.get_markdown_package(source_id)
    if existing_package is not None and hasattr(dependencies.source_store, "read_markdown_package"):
        markdown = await dependencies.source_store.read_markdown_package(source_id)
        await dependencies.emit_event(
            "canonical_markdown_reused",
            workflow_run_id=state.get("workflow_run_id"),
            job_id=job_id or None,
            source_id=source_id,
            upload_id=upload_id,
            package_id=existing_package["package_id"],
            markdown_sha256=existing_package["markdown_sha256"],
            package_path=existing_package["package_path"],
        )
        await _mark_workflow(
            dependencies,
            state,
            status="running",
            current_node="convert_to_markdown",
            metadata={"canonical_document_id": existing_package["package_id"]},
        )
        vault_artifact: dict[str, Any] | None = None
        if dependencies.vault_path:
            vault_artifact = write_canonical_markdown_vault_artifact(
                dependencies.vault_path,
                source_id=source_id,
                package_id=str(existing_package["package_id"]),
                markdown_sha256=str(existing_package["markdown_sha256"]),
                title=str(existing_package.get("title") or source_id),
                source_type=str(existing_package.get("source_type") or "unknown"),
                markdown=markdown,
                source_url=url,
                origin_path=origin_path,
                storage_path=storage_path,
            )
            await dependencies.emit_event(
                "canonical_markdown_vault_artifact_written",
                workflow_run_id=state.get("workflow_run_id"),
                job_id=job_id or None,
                source_id=source_id,
                upload_id=upload_id,
                package_id=existing_package["package_id"],
                vault_path=vault_artifact["path"],
                vault_relative_path=vault_artifact["relative_path"],
                created=vault_artifact["created"],
                changed=vault_artifact["changed"],
                reused=True,
            )
        chaos_artifact: dict[str, Any] | None = None
        if dependencies.vault_path and storage_path and str(state.get("content_type") or "").lower() == "application/pdf":
            source_file = Path(storage_path).expanduser().resolve()
            if source_file.exists():
                chaos_artifact = await _write_pdf_chaos_artifact(
                    dependencies=dependencies,
                    source_file=source_file,
                    extracted=ExtractedContentMultimodal(
                        text=markdown,
                        title=str(existing_package.get("title") or state.get("filename") or source_id),
                        source_type=str(existing_package.get("source_type") or "pdf"),
                        source_path=source_file,
                        metadata={"source_id": source_id, "reused_canonical_markdown": True},
                        processing_method="source_foundation_pdf_chaos_reused",
                    ),
                    filename=str(state.get("filename") or existing_package.get("title") or source_id),
                    source_id=source_id,
                    batch_id=str(state.get("batch_id") or ""),
                    evidence_run_id=str(state.get("evidence_run_id") or ""),
                )
                await dependencies.emit_event(
                    "source_equivalent_chaos_markdown_written",
                    workflow_run_id=state.get("workflow_run_id"),
                    job_id=job_id or None,
                    source_id=source_id,
                    upload_id=upload_id,
                    vault_path=chaos_artifact["path"],
                    vault_relative_path=chaos_artifact["relative_path"],
                    page_count=chaos_artifact["page_count"],
                    screenshot_count=chaos_artifact["screenshot_count"],
                    screenshot_renderer=chaos_artifact["screenshot_renderer"],
                    reused=True,
                )
        return _append_step(
            state,
            "convert_to_markdown",
            canonical_document_id=str(existing_package["package_id"]),
            canonical_markdown_path=str(existing_package["package_path"]),
            canonical_markdown_sha256=str(existing_package["markdown_sha256"]),
            canonical_markdown_vault_path="" if vault_artifact is None else vault_artifact["path"],
            canonical_markdown_vault_relative_path="" if vault_artifact is None else vault_artifact["relative_path"],
            chaos_markdown_vault_path="" if chaos_artifact is None else chaos_artifact["path"],
            chaos_markdown_vault_relative_path="" if chaos_artifact is None else chaos_artifact["relative_path"],
            markdown=markdown,
            source_type=str(existing_package.get("source_type") or "unknown"),
            origin_path=origin_path,
            storage_path=storage_path,
            source_metadata=source_metadata,
            evidence_run_id=state.get("evidence_run_id", ""),
            source_lineage={
                **_source_lineage(
                    {
                        **state,
                        "origin_path": origin_path,
                        "storage_path": storage_path,
                        "canonical_document_id": str(existing_package["package_id"]),
                        "canonical_markdown_path": str(existing_package["package_path"]),
                        "canonical_markdown_sha256": str(existing_package["markdown_sha256"]),
                    }
                )
            },
        )

    router = dependencies.processing_router_factory()
    if url:
        extracted = await router.process_url(url, browser_manager=dependencies.browser_manager)
        if not extracted.text or str(extracted.text).startswith("[Failed to fetch"):
            raise RuntimeError(extracted.text or "URL extraction returned no text")
        fallback_title = url
    else:
        data = await dependencies.source_store.read_stored_object(source_id)
        extracted = await router.process(
            data,
            filename=state.get("filename", source_id),
            http_content_type=state.get("content_type", "application/octet-stream"),
        )
        fallback_title = state.get("filename", source_id)

    await dependencies.source_store.update_status(
        source_id,
        "extracted",
        {
            "workflow_run_id": state.get("workflow_run_id"),
            "source_type": extracted.source_type,
            "extracted_chars": len(extracted.text or ""),
            "title": extracted.title or fallback_title,
            "job_id": job_id,
            "upload_id": upload_id,
            "batch_id": batch_id,
            "evidence_run_id": state.get("evidence_run_id", ""),
        },
    )
    await dependencies.emit_event(
        "chaos_note_created",
        workflow_run_id=state.get("workflow_run_id"),
        job_id=job_id or None,
        source_id=source_id,
        upload_id=upload_id,
        batch_id=batch_id or None,
        source_type=extracted.source_type,
        filename=None if url else state.get("filename"),
        url=url or None,
        title=extracted.title or fallback_title,
        text_length=len(extracted.text or ""),
    )
    markdown_package = await dependencies.canonical_markdown_converter_factory().convert_extracted(
        source_id=source_id,
        extracted=extracted,
        fallback_title=fallback_title,
        source_url=url,
        metadata={
            "workflow_run_id": state.get("workflow_run_id"),
            "job_id": job_id,
            "upload_id": upload_id,
            "batch_id": batch_id,
            "evidence_run_id": state.get("evidence_run_id", ""),
            "filename": state.get("filename", ""),
            "content_type": state.get("content_type", ""),
            "url": url,
            "origin_path": origin_path,
            "storage_path": storage_path,
            "source_lineage": {
                "source_id": source_id,
                "origin_path": origin_path,
                "storage_path": storage_path,
            },
        },
    )
    await dependencies.emit_event(
        "canonical_markdown_created",
        workflow_run_id=state.get("workflow_run_id"),
        job_id=job_id or None,
        batch_id=batch_id or None,
        upload_id=upload_id,
        source_id=source_id,
        package_id=markdown_package.package_id,
        markdown_sha256=markdown_package.markdown_sha256,
        package_path=markdown_package.package_path,
        title=markdown_package.title,
        text_length=len(markdown_package.markdown),
        url=url or None,
    )
    chaos_artifact: dict[str, Any] | None = None
    if dependencies.vault_path and storage_path and str(state.get("content_type") or "").lower() == "application/pdf":
        source_file = Path(storage_path).expanduser().resolve()
        if source_file.exists():
            chaos_artifact = await _write_pdf_chaos_artifact(
                dependencies=dependencies,
                source_file=source_file,
                extracted=extracted,
                filename=str(state.get("filename") or fallback_title),
                source_id=source_id,
                batch_id=batch_id,
                evidence_run_id=str(state.get("evidence_run_id") or ""),
            )
            await dependencies.emit_event(
                "source_equivalent_chaos_markdown_written",
                workflow_run_id=state.get("workflow_run_id"),
                job_id=job_id or None,
                source_id=source_id,
                upload_id=upload_id,
                vault_path=chaos_artifact["path"],
                vault_relative_path=chaos_artifact["relative_path"],
                page_count=chaos_artifact["page_count"],
                screenshot_count=chaos_artifact["screenshot_count"],
                screenshot_renderer=chaos_artifact["screenshot_renderer"],
            )
    vault_artifact: dict[str, Any] | None = None
    if dependencies.vault_path:
        vault_artifact = write_canonical_markdown_vault_artifact(
            dependencies.vault_path,
            source_id=source_id,
            package_id=markdown_package.package_id,
            markdown_sha256=markdown_package.markdown_sha256,
            title=markdown_package.title,
            source_type=markdown_package.source_type,
            markdown=markdown_package.markdown,
            source_url=url,
            origin_path=origin_path,
            storage_path=storage_path,
        )
        await dependencies.emit_event(
            "canonical_markdown_vault_artifact_written",
            workflow_run_id=state.get("workflow_run_id"),
            job_id=job_id or None,
            source_id=source_id,
            upload_id=upload_id,
            package_id=markdown_package.package_id,
            vault_path=vault_artifact["path"],
            vault_relative_path=vault_artifact["relative_path"],
            created=vault_artifact["created"],
            changed=vault_artifact["changed"],
        )
    await _mark_workflow(
        dependencies,
        state,
        status="running",
        current_node="convert_to_markdown",
        metadata={
            "canonical_document_id": markdown_package.package_id,
            "canonical_markdown_vault_path": "" if vault_artifact is None else vault_artifact["path"],
            "chaos_markdown_vault_path": "" if chaos_artifact is None else chaos_artifact["path"],
        },
    )
    return _append_step(
        state,
        "convert_to_markdown",
        canonical_document_id=markdown_package.package_id,
        canonical_markdown_path=markdown_package.package_path,
        canonical_markdown_sha256=markdown_package.markdown_sha256,
        markdown=markdown_package.markdown,
        source_type=markdown_package.source_type,
        origin_path=origin_path,
        storage_path=storage_path,
        source_metadata=source_metadata,
        evidence_run_id=state.get("evidence_run_id", ""),
        canonical_markdown_vault_path="" if vault_artifact is None else vault_artifact["path"],
        canonical_markdown_vault_relative_path="" if vault_artifact is None else vault_artifact["relative_path"],
        chaos_markdown_vault_path="" if chaos_artifact is None else chaos_artifact["path"],
        chaos_markdown_vault_relative_path="" if chaos_artifact is None else chaos_artifact["relative_path"],
        source_lineage=_source_lineage(
            {
                **state,
                "source_id": source_id,
                "origin_path": origin_path,
                "storage_path": storage_path,
                "canonical_document_id": markdown_package.package_id,
                "canonical_markdown_path": markdown_package.package_path,
                "canonical_markdown_sha256": markdown_package.markdown_sha256,
            }
        ),
    )


async def _run_data_node(
    state: WorkflowState,
    dependencies: SourceFoundationDependencies,
) -> WorkflowState:
    source_id = state["source_id"]
    await _mark_workflow(dependencies, state, status="running", current_node="run_data")
    await dependencies.source_store.update_status(
        source_id,
        "processing",
        {
            "workflow_run_id": state.get("workflow_run_id"),
            "job_id": state.get("job_id", ""),
            "upload_id": state.get("upload_id", ""),
            "batch_id": state.get("batch_id", ""),
        },
    )
    is_url = bool(state.get("url"))
    drop = RainDrop(
        id="",
        rain_type=RainType.URL if is_url else RainType.DOCUMENT,
        content=state["markdown"],
        raw_bytes=state["markdown"].encode("utf-8"),
        source="source_foundation_graph_url" if is_url else "source_foundation_graph_upload",
        source_id=source_id,
        stream_type=StreamType.FETCH_ANALYZE if is_url else StreamType.EXTRACT_ANALYZE,
        metadata={
            "workflow_run_id": state.get("workflow_run_id"),
            "langgraph_thread_id": state.get("langgraph_thread_id"),
            "source_id": source_id,
            "job_id": state.get("job_id", ""),
            "upload_id": state.get("upload_id", ""),
            "batch_id": state.get("batch_id", ""),
            "evidence_run_id": state.get("evidence_run_id", ""),
            "url": state.get("url", ""),
            "filename": state.get("filename", ""),
            "content_type": state.get("content_type", ""),
            "source_type": state.get("source_type", ""),
            "processing_method": "source_foundation_graph",
            "canonical_markdown_package_id": state.get("canonical_document_id", ""),
            "canonical_markdown_path": state.get("canonical_markdown_path", ""),
            "canonical_markdown_vault_path": state.get("canonical_markdown_vault_path", ""),
            "canonical_markdown_sha256": state.get("canonical_markdown_sha256", ""),
            "origin_path": state.get("origin_path", ""),
            "storage_path": state.get("storage_path", ""),
            "source_paths": _source_lineage_paths(state),
            "source_lineage": _source_lineage(state),
        },
    )
    result = await dependencies.dikiwi_ingestion(drop)
    failed_stage = _failed_stage(dependencies, result)
    final_stage = _enum_name(getattr(result, "final_stage_reached", ""))
    stage_results = _stage_results_payload(result)
    pipeline_id = str(getattr(result, "pipeline_id", ""))
    if failed_stage is not None:
        stage_name = _enum_name(getattr(failed_stage, "stage", ""))
        error = getattr(failed_stage, "error_message", None) or f"{stage_name} failed"
        await dependencies.source_store.update_status(
            source_id,
            "failed",
            {
                "workflow_run_id": state.get("workflow_run_id"),
                "pipeline_id": pipeline_id,
                "error": error,
                "last_failed_stage": stage_name,
            },
        )
        await dependencies.emit_event(
            "pipeline_failed",
            workflow_run_id=state.get("workflow_run_id"),
            job_id=state.get("job_id") or None,
            source_id=source_id,
            upload_id=state.get("upload_id") or None,
            batch_id=state.get("batch_id") or None,
            url=state.get("url") or None,
            pipeline_id=pipeline_id,
            error=error,
        )
        await _mark_workflow(
            dependencies,
            state,
            status="failed",
            current_node=stage_name or "run_data",
            metadata={"pipeline_id": pipeline_id, "final_stage": final_stage, "stage_count": len(stage_results)},
            last_error=error,
        )
        return _append_step(
            state,
            "run_data",
            status="failed",
            error=error,
            pipeline_id=pipeline_id,
            final_stage=final_stage,
            stage_count=len(stage_results),
            stage_results=stage_results,
        )
    return _append_step(
        state,
        "run_data",
        pipeline_id=pipeline_id,
        final_stage=final_stage,
        stage_count=len(stage_results),
        stage_results=stage_results,
    )


async def _run_information_node(
    state: WorkflowState,
    dependencies: SourceFoundationDependencies,
) -> WorkflowState:
    if not _stage_success(state, "INFORMATION"):
        error = "Foundation workflow did not produce INFORMATION"
        await _mark_workflow(dependencies, state, status="failed", current_node="run_information", last_error=error)
        return _append_step(state, "run_information", status="failed", error=error)
    await _mark_workflow(dependencies, state, status="running", current_node="run_information")
    return _append_step(state, "run_information")


async def _run_knowledge_node(
    state: WorkflowState,
    dependencies: SourceFoundationDependencies,
) -> WorkflowState:
    source_id = state["source_id"]
    if not _stage_success(state, "KNOWLEDGE"):
        error = "Foundation workflow did not produce KNOWLEDGE"
        await dependencies.source_store.update_status(
            source_id,
            "failed",
            {
                "workflow_run_id": state.get("workflow_run_id"),
                "pipeline_id": state.get("pipeline_id", ""),
                "error": error,
            },
        )
        await _mark_workflow(dependencies, state, status="failed", current_node="run_knowledge", last_error=error)
        return _append_step(state, "run_knowledge", status="failed", error=error)

    await dependencies.source_store.update_status(
        source_id,
        "completed",
        {
            "workflow_run_id": state.get("workflow_run_id"),
            "job_id": state.get("job_id", ""),
            "upload_id": state.get("upload_id", ""),
            "batch_id": state.get("batch_id", ""),
            "pipeline_id": state.get("pipeline_id", ""),
            "final_stage": state.get("final_stage", ""),
        },
    )
    await dependencies.emit_event(
        "source_ingest_completed",
        workflow_run_id=state.get("workflow_run_id"),
        job_id=state.get("job_id") or None,
        upload_id=state.get("upload_id") or None,
        source_id=source_id,
        filename=None if state.get("url") else state.get("filename"),
        url=state.get("url") or None,
        batch_id=state.get("batch_id") or None,
        pipeline_id=state.get("pipeline_id", ""),
        final_stage=state.get("final_stage", ""),
        stage_count=state.get("stage_count", 0),
    )
    await _mark_workflow(
        dependencies,
        state,
        status="completed",
        current_node="run_knowledge",
        metadata={
            "pipeline_id": state.get("pipeline_id", ""),
            "final_stage": state.get("final_stage", ""),
            "stage_count": state.get("stage_count", 0),
        },
    )
    return _append_step(state, "run_knowledge", status="completed")


def _after_status(state: WorkflowState) -> str:
    return "end" if state.get("status") in {"failed", "cancelled"} else "continue"


def build_source_foundation_graph(
    checkpointer: Any | None = None,
    dependencies: SourceFoundationDependencies | None = None,
) -> Any:
    graph = StateGraph(WorkflowState)
    if dependencies is None:
        graph.add_node("register_source", register_source)
        graph.add_node("convert_to_markdown", convert_to_markdown)
        graph.add_node("run_data", run_data)
        graph.add_node("run_information", run_information)
        graph.add_node("run_knowledge", run_knowledge)
    else:
        async def register_source_runtime(state: WorkflowState) -> WorkflowState:
            return await _register_source_node(state, dependencies)

        async def convert_to_markdown_runtime(state: WorkflowState) -> WorkflowState:
            return await _convert_to_markdown_node(state, dependencies)

        async def run_data_runtime(state: WorkflowState) -> WorkflowState:
            return await _run_data_node(state, dependencies)

        async def run_information_runtime(state: WorkflowState) -> WorkflowState:
            return await _run_information_node(state, dependencies)

        async def run_knowledge_runtime(state: WorkflowState) -> WorkflowState:
            return await _run_knowledge_node(state, dependencies)

        graph.add_node("register_source", register_source_runtime)
        graph.add_node("convert_to_markdown", convert_to_markdown_runtime)
        graph.add_node("run_data", run_data_runtime)
        graph.add_node("run_information", run_information_runtime)
        graph.add_node("run_knowledge", run_knowledge_runtime)
    graph.add_edge(START, "register_source")
    graph.add_edge("register_source", "convert_to_markdown")
    graph.add_edge("convert_to_markdown", "run_data")
    graph.add_conditional_edges("run_data", _after_status, {"continue": "run_information", "end": END})
    graph.add_conditional_edges("run_information", _after_status, {"continue": "run_knowledge", "end": END})
    graph.add_edge("run_knowledge", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
