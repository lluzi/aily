from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from starlette.requests import HTTPConnection

from aily.ui.events import emit_ui_event, ui_event_hub
from aily.verify.run_registry import RunNotFoundError, RunRegistry
from aily.security.rate_limit import FixedWindowRateLimiter


def create_ui_router(
    *,
    upload_handler: Callable[[UploadFile, str], Awaitable[dict[str, Any]]] | None,
    batch_upload_handler: Callable[[list[tuple[UploadFile, str]], str], Awaitable[dict[str, Any]]] | None = None,
    status_provider: Callable[[], Awaitable[dict[str, Any]]],
    graph_provider: Callable[[], Awaitable[dict[str, Any]]],
    pipeline_provider: Callable[[str], Awaitable[dict[str, Any] | None]],
    url_handler: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    text_handler: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
    source_provider: Callable[[int, int], Awaitable[dict[str, Any]]] | None = None,
    source_detail_provider: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
    source_jobs_provider: Callable[[int, int, str | None], Awaitable[dict[str, Any]]] | None = None,
    workflow_trigger_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    workflow_provider: Callable[[int, int, str | None], Awaitable[dict[str, Any]]] | None = None,
    chat_thread_create_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    chat_thread_provider: Callable[[int, int], Awaitable[dict[str, Any]]] | None = None,
    chat_thread_detail_provider: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
    chat_message_handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    workflow_plan_provider: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
    workflow_plan_confirm_handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    research_jobs_provider: Callable[[int], Awaitable[dict[str, Any]]] | None = None,
    research_job_provider: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
    second_opinion_provider: Callable[[int], Awaitable[dict[str, Any]]] | None = None,
    business_plan_provider: Callable[[int], Awaitable[dict[str, Any]]] | None = None,
    business_plan_detail_provider: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
    proposal_provider: Callable[[int], Awaitable[dict[str, Any]]] | None = None,
    entrepreneurship_provider: Callable[[int], Awaitable[dict[str, Any]]] | None = None,
    vault_notes_provider: Callable[[str, int], Awaitable[dict[str, Any]]] | None = None,
    control_handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    rate_limiter: FixedWindowRateLimiter | None = None,
    run_registry: RunRegistry | None = None,
    enable_uploads: bool = True,
    max_files_per_request: int = 8,
    max_upload_bytes: int | None = None,
    auth_token: str = "",
    trust_proxy_headers: bool = False,
) -> APIRouter:
    def _request_authorized(request: HTTPConnection) -> bool:
        if not auth_token:
            return True
        bearer = request.headers.get("authorization", "")
        explicit_token = request.headers.get("x-aily-token", "")
        query_token = request.query_params.get("token", "")
        cookie_token = request.cookies.get("aily_ui_token", "")
        return (
            bearer == f"Bearer {auth_token}"
            or explicit_token == auth_token
            or query_token == auth_token
            or cookie_token == auth_token
        )

    async def _require_auth(request: HTTPConnection) -> None:
        if not _request_authorized(request):
            raise HTTPException(status_code=401, detail="Aily Studio authentication required")

    def _websocket_authorized(websocket: WebSocket) -> bool:
        if not auth_token:
            return True
        bearer = websocket.headers.get("authorization", "")
        explicit_token = websocket.headers.get("x-aily-token", "")
        query_token = websocket.query_params.get("token", "")
        cookie_token = websocket.cookies.get("aily_ui_token", "")
        return (
            bearer == f"Bearer {auth_token}"
            or explicit_token == auth_token
            or query_token == auth_token
            or cookie_token == auth_token
        )

    router = APIRouter(prefix="/api/ui", tags=["ui"], dependencies=[Depends(_require_auth)])

    def _client_key(request: HTTPConnection) -> str:
        if trust_proxy_headers:
            forwarded_for = request.headers.get("x-forwarded-for", "")
            if forwarded_for:
                return forwarded_for.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"

    def _check_rate_limit(request: HTTPConnection) -> None:
        if rate_limiter is None:
            return
        allowed, retry_after = rate_limiter.allow(_client_key(request))
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    if enable_uploads:
        @router.post("/uploads")
        async def upload_files(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
            _check_rate_limit(request)
            if upload_handler is None:
                raise HTTPException(status_code=503, detail="Upload handler unavailable")
            if not files:
                raise HTTPException(status_code=400, detail="No files uploaded")
            if len(files) > max_files_per_request:
                raise HTTPException(
                    status_code=400,
                    detail=f"Too many files uploaded; limit is {max_files_per_request}",
                )
            if max_upload_bytes is not None:
                oversized = [
                    file.filename or "unknown"
                    for file in files
                    if getattr(file, "size", None) is not None and file.size > max_upload_bytes
                ]
                if oversized:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds max upload size of {max_upload_bytes} bytes: {oversized[0]}",
                    )

            upload_refs = [(file, str(uuid4())) for file in files]
            if len(upload_refs) > 1 and batch_upload_handler is not None:
                batch_id = str(uuid4())
                await emit_ui_event(
                    "upload_batch_started",
                    batch_id=batch_id,
                    file_count=len(upload_refs),
                )
                return await batch_upload_handler(upload_refs, batch_id)

            accepted: list[dict[str, Any]] = []
            for file, upload_id in upload_refs:
                size_bytes = getattr(file, "size", None)
                await emit_ui_event(
                    "source_uploaded",
                    upload_id=upload_id,
                    filename=file.filename or "unknown",
                    content_type=file.content_type or "application/octet-stream",
                    size_bytes=size_bytes,
                )
                accepted.append(await upload_handler(file, upload_id))
            return {"uploads": accepted}
    else:
        @router.post("/uploads")
        async def upload_files_unavailable(request: Request) -> dict[str, Any]:
            _check_rate_limit(request)
            raise HTTPException(
                status_code=503,
                detail="File uploads require python-multipart to be installed",
            )

    @router.post("/sources/urls")
    async def submit_url(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        _check_rate_limit(request)
        if url_handler is None:
            raise HTTPException(status_code=503, detail="URL intake unavailable")
        url = str(payload.get("url", "")).strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(status_code=400, detail="Only http(s) URLs are supported")
        source = await url_handler(url)
        await emit_ui_event(
            "source_stored",
            source_id=source.get("source_id"),
            source_type="url",
            url=url,
            duplicate=source.get("duplicate"),
            sha256=source.get("sha256"),
        )
        return source

    @router.post("/sources/texts")
    async def submit_text(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        _check_rate_limit(request)
        if text_handler is None:
            raise HTTPException(status_code=503, detail="Text intake unavailable")
        title = str(payload.get("title", "")).strip()
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=400, detail="Text is required")
        source = await text_handler(title, text)
        await emit_ui_event(
            "source_stored",
            source_id=source.get("source_id"),
            source_type="text",
            title=title or source.get("title") or "Text Source",
            duplicate=source.get("duplicate"),
            sha256=source.get("sha256"),
        )
        return source

    @router.get("/status")
    async def ui_status() -> dict[str, Any]:
        return await status_provider()

    @router.get("/graph")
    async def graph_snapshot() -> dict[str, Any]:
        return await graph_provider()

    @router.get("/pipelines/{pipeline_id}")
    async def pipeline_trace(pipeline_id: str) -> dict[str, Any]:
        payload = await pipeline_provider(pipeline_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return payload

    @router.get("/uploads/{upload_id}")
    async def upload_trace(upload_id: str) -> dict[str, Any]:
        return {
            "upload_id": upload_id,
            "events": ui_event_hub.upload_trace(upload_id),
        }

    @router.get("/events/query")
    async def query_events(
        run_id: str | None = None,
        pipeline_id: str | None = None,
        upload_id: str | None = None,
        event_type: str | None = None,
        after_seq: int | None = None,
        before_seq: int | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        events = await ui_event_hub.query_persisted(
            run_id=run_id,
            pipeline_id=pipeline_id,
            upload_id=upload_id,
            event_type=event_type,
            after_seq=after_seq,
            before_seq=before_seq,
            limit=limit,
        )
        return {
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "upload_id": upload_id,
            "event_type": event_type,
            "after_seq": after_seq,
            "before_seq": before_seq,
            "events": events,
            "next_after_seq": events[-1].get("seq") if events else after_seq,
        }

    @router.get("/sources")
    async def list_sources(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        if source_provider is None:
            return {"total": 0, "sources": []}
        return await source_provider(limit, offset)

    @router.get("/sources/{source_id}")
    async def source_detail(source_id: str) -> dict[str, Any]:
        if source_detail_provider is None:
            raise HTTPException(status_code=404, detail="Source store unavailable")
        payload = await source_detail_provider(source_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return payload

    @router.get("/source-jobs")
    async def list_source_jobs(limit: int = 100, offset: int = 0, status: str | None = None) -> dict[str, Any]:
        if source_jobs_provider is None:
            return {"total": 0, "jobs": []}
        return await source_jobs_provider(limit, offset, status)

    @router.post("/workflows/iwi")
    async def trigger_iwi_workflow(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        _check_rate_limit(request)
        if workflow_trigger_handler is None:
            raise HTTPException(status_code=503, detail="Workflow trigger unavailable")
        motive = str(payload.get("motive", "")).strip()
        if not motive:
            raise HTTPException(status_code=400, detail="Workflow motive is required")
        return await workflow_trigger_handler(payload)

    @router.get("/workflows")
    async def list_workflows(limit: int = 50, offset: int = 0, status: str | None = None) -> dict[str, Any]:
        if workflow_provider is None:
            return {"total": 0, "workflows": []}
        return await workflow_provider(limit, offset, status)

    @router.post("/chat/threads")
    async def create_chat_thread(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        _check_rate_limit(request)
        if chat_thread_create_handler is None:
            raise HTTPException(status_code=503, detail="Chat store unavailable")
        return await chat_thread_create_handler(payload or {})

    @router.get("/chat/threads")
    async def list_chat_threads(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if chat_thread_provider is None:
            return {"total": 0, "threads": []}
        return await chat_thread_provider(limit, offset)

    @router.get("/chat/threads/{chat_thread_id}")
    async def chat_thread_detail(chat_thread_id: str) -> dict[str, Any]:
        if chat_thread_detail_provider is None:
            raise HTTPException(status_code=404, detail="Chat store unavailable")
        payload = await chat_thread_detail_provider(chat_thread_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Chat thread not found")
        return payload

    @router.post("/chat/threads/{chat_thread_id}/messages")
    async def add_chat_message(request: Request, chat_thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _check_rate_limit(request)
        if chat_message_handler is None:
            raise HTTPException(status_code=503, detail="Chat message handler unavailable")
        return await chat_message_handler(chat_thread_id, payload)

    @router.get("/workflow-plans/{workflow_plan_id}")
    async def workflow_plan_detail(workflow_plan_id: str) -> dict[str, Any]:
        if workflow_plan_provider is None:
            raise HTTPException(status_code=404, detail="Workflow plan store unavailable")
        payload = await workflow_plan_provider(workflow_plan_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Workflow plan not found")
        return payload

    @router.post("/workflow-plans/{workflow_plan_id}/confirm")
    async def confirm_workflow_plan(request: Request, workflow_plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _check_rate_limit(request)
        if workflow_plan_confirm_handler is None:
            raise HTTPException(status_code=503, detail="Workflow plan confirmation unavailable")
        return await workflow_plan_confirm_handler(workflow_plan_id, payload)

    @router.get("/research/jobs")
    async def list_research_jobs(limit: int = 50) -> dict[str, Any]:
        if research_jobs_provider is None:
            return {"total": 0, "items": []}
        return await research_jobs_provider(limit)

    @router.get("/research/jobs/{research_id}")
    async def research_job_detail(research_id: str) -> dict[str, Any]:
        if research_job_provider is None:
            raise HTTPException(status_code=404, detail="Research store unavailable")
        payload = await research_job_provider(research_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Research job not found")
        return payload

    @router.get("/second-opinions")
    async def list_second_opinions(limit: int = 50) -> dict[str, Any]:
        if second_opinion_provider is None:
            return {"total": 0, "items": []}
        return await second_opinion_provider(limit)

    @router.get("/business-plans")
    async def list_business_plans(limit: int = 50) -> dict[str, Any]:
        if business_plan_provider is None:
            return {"total": 0, "items": []}
        return await business_plan_provider(limit)

    @router.get("/business-plans/{business_plan_id}")
    async def business_plan_detail(business_plan_id: str) -> dict[str, Any]:
        if business_plan_detail_provider is None:
            raise HTTPException(status_code=404, detail="Business plan store unavailable")
        payload = await business_plan_detail_provider(business_plan_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Business plan not found")
        return payload

    @router.get("/proposals")
    async def list_proposals(limit: int = 50) -> dict[str, Any]:
        if proposal_provider is None:
            return {"total": 0, "items": []}
        return await proposal_provider(limit)

    @router.get("/entrepreneurship")
    async def list_entrepreneurship(limit: int = 50) -> dict[str, Any]:
        if entrepreneurship_provider is None:
            return {"total": 0, "items": []}
        return await entrepreneurship_provider(limit)

    @router.get("/vault-notes/{stage}")
    async def list_vault_notes(stage: str, limit: int = 25) -> dict[str, Any]:
        if vault_notes_provider is None:
            return {"stage": stage, "total": 0, "items": []}
        allowed = {
            "00-Chaos",
            "01-Data",
            "02-Information",
            "03-Knowledge",
            "04-Insight",
            "05-Wisdom",
            "06-Impact",
            "07-Proposal",
            "08-Entrepreneurship",
        }
        if stage not in allowed:
            raise HTTPException(status_code=400, detail="Unsupported vault stage")
        return await vault_notes_provider(stage, limit)

    @router.post("/control")
    async def control(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        _check_rate_limit(request)
        if control_handler is None:
            raise HTTPException(status_code=503, detail="Studio controls unavailable")
        action = str(payload.get("action", "")).strip()
        if not action:
            raise HTTPException(status_code=400, detail="Control action is required")
        return await control_handler(action, payload)

    @router.get("/logs")
    async def recent_logs(limit: int = 200) -> dict[str, Any]:
        return {"events": ui_event_hub.recent_events(limit=limit)}

    @router.get("/runs")
    async def list_runs(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if run_registry is None:
            return {"root_dir": "", "total": 0, "runs": []}
        return run_registry.list_runs(limit=limit, offset=offset)

    @router.get("/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, Any]:
        if run_registry is None:
            raise HTTPException(status_code=404, detail="Run registry unavailable")
        try:
            return run_registry.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, limit: int = 500) -> dict[str, Any]:
        if run_registry is None:
            raise HTTPException(status_code=404, detail="Run registry unavailable")
        try:
            return run_registry.get_events(run_id, limit=limit)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/llm-calls")
    async def run_llm_calls(run_id: str, limit: int = 500) -> dict[str, Any]:
        if run_registry is None:
            raise HTTPException(status_code=404, detail="Run registry unavailable")
        try:
            return run_registry.get_llm_calls(run_id, limit=limit)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.websocket("/events")
    async def ui_events_socket(websocket: WebSocket) -> None:
        if not _websocket_authorized(websocket):
            await websocket.close(code=1008, reason="Aily Studio authentication required")
            return
        await websocket.accept()
        queue = ui_event_hub.subscribe()
        try:
            for event in ui_event_hub.recent_events(limit=50):
                await websocket.send_json(event)
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            ui_event_hub.unsubscribe(queue)
            with contextlib.suppress(Exception):
                await websocket.close()

    return router
