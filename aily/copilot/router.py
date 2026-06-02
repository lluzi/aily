"""FastAPI router for Aily-Copilot product APIs."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from aily.config import SETTINGS
from aily.copilot.chat import ChatLLM, CopilotVaultChatService
from aily.copilot.context import CopilotContextEnvelopeBuilder
from aily.copilot.projects import CopilotProjectStore
from aily.copilot.proposals import CopilotProposalStore
from aily.copilot.vault import VaultSearchService
from aily.copilot.workflows import (
    CopilotWorkflowService,
    EndToEndValueWorkflowRequest,
    WORKFLOW_TOOL_NAME,
)
from aily.dossier import DossierBuildRequest, DossierService
from aily.llm.provider_routes import PrimaryLLMRoute
from aily.orchestration.runs import WorkflowRunStore
from aily.security.rate_limit import FixedWindowRateLimiter
from aily.writer.vault_layout import inspect_v1_vault_layout


class VaultSearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=10, ge=1, le=50)
    include_dirs: list[str] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(default_factory=list)


class ReadNoteRequest(BaseModel):
    path: str
    chunk_index: int = Field(default=0, ge=0)
    chunk_lines: int = Field(default=180, ge=25, le=500)


class NeighborhoodRequest(BaseModel):
    path: str
    limit: int = Field(default=20, ge=1, le=100)


class RelevantNotesRequest(BaseModel):
    query: str = ""
    seed_paths: list[str] = Field(default_factory=list)
    project_id: str = ""
    include_dirs: list[str] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(default_factory=list)
    limit: int = Field(default=12, ge=1, le=50)


class ContextEnvelopeRequest(BaseModel):
    user_message: str
    search_results: list[dict[str, Any]] = Field(default_factory=list)
    previous_context: list[dict[str, Any]] = Field(default_factory=list)
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str | None = None


class ChatRequest(BaseModel):
    message: str
    search_query: str = ""
    project_id: str = ""
    limit: int = Field(default=8, ge=1, le=20)
    include_dirs: list[str] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(default_factory=list)
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    use_llm: bool = True


class DossierGenerateRequest(BaseModel):
    topic: str
    project_id: str = ""
    query_terms: list[str] = Field(default_factory=list)
    seed_claims: list[str] = Field(default_factory=list)
    max_vault_evidence: int = Field(default=40, ge=5, le=120)
    max_tavily_evidence: int = Field(default=20, ge=0, le=80)


class ProjectUpsertRequest(BaseModel):
    name: str
    project_id: str = ""
    description: str = ""
    include_dirs: list[str] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(default_factory=list)
    source_terms: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    preferred_model: str = ""


class ProjectDeleteRequest(BaseModel):
    project_id: str


class ProposalCreateRequest(BaseModel):
    target_path: str = ""
    title: str
    content: str
    mode: str = "create"
    rationale: str = ""
    source_citations: list[dict[str, Any]] = Field(default_factory=list)


class ProposalActionRequest(BaseModel):
    proposal_id: str


class ProposalListRequest(BaseModel):
    status: str = ""


class CopilotConfigUpdateRequest(BaseModel):
    llm_provider: str | None = None
    copilot_chat_provider: str | None = None
    copilot_dossier_provider: str | None = None
    kimi_api_key: str | None = None
    kimi_model: str | None = None
    kimi_vision_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None
    tavily_api_key: str | None = None
    tavily_search_depth: str | None = None
    llm_timeout_seconds: float | None = Field(default=None, ge=1.0, le=600.0)
    llm_max_retries: int | None = Field(default=None, ge=0, le=10)
    llm_max_concurrency: int | None = Field(default=None, ge=1, le=16)
    llm_min_interval_seconds: float | None = Field(default=None, ge=0.0, le=120.0)


_FAILED_STATUSES = {"failed", "failed_retry_exhausted"}
_PENDING_STATUSES = {"queued", "retry_pending", "stored", "deferred", "extracting"}
_DONE_STATUSES = {"completed"}


def _stage_states(status: str, has_markdown: bool) -> dict[str, str]:
    """Best-effort Data/Information/Knowledge stage states.

    The source store tracks a single coarse status, not per-stage progress, so
    these are derived. They become exact once the pipeline persists per-stage
    state per source.
    """
    if status in _FAILED_STATUSES:
        return {"data": "failed", "information": "failed", "knowledge": "failed"}
    if status in _DONE_STATUSES:
        return {"data": "done", "information": "done", "knowledge": "done"}
    if status in {"processing"}:
        return {"data": "in_progress", "information": "pending", "knowledge": "pending"}
    if status == "extracted" or has_markdown:
        return {"data": "pending", "information": "pending", "knowledge": "pending"}
    return {"data": "pending", "information": "pending", "knowledge": "pending"}


def _next_action(status: str) -> str:
    if status in _FAILED_STATUSES:
        return "retry"
    if status in _DONE_STATUSES:
        return "none"
    if status in _PENDING_STATUSES or status == "processing":
        return "wait"
    return "none"


def source_status_from_row(row: dict[str, Any], package: dict[str, Any] | None) -> dict[str, Any]:
    """Map a raw source-store row (+ optional markdown package) to the
    Copilot-facing SourceStatus product contract."""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    status = str(row.get("status") or "")
    has_markdown = package is not None
    origin = str(row.get("normalized_source") or metadata.get("origin_path") or "")
    title = (
        str(row.get("filename") or "")
        or str(metadata.get("title") or "")
        or (origin.rsplit("/", 1)[-1] if origin else "")
        or str(row.get("source_id") or "")
    )
    if status in _FAILED_STATUSES:
        conversion_status = "failed"
    elif has_markdown or status in {"extracted", "processing", "completed"}:
        conversion_status = "done"
    else:
        conversion_status = "pending"
    last_error = str(metadata.get("retry_error") or metadata.get("error") or "") or None
    return {
        "source_id": str(row.get("source_id") or ""),
        "display_title": title,
        "source_type": str(row.get("kind") or ""),
        "origin": origin,
        "content_hash": row.get("sha256"),
        "canonical_markdown_hash": (package or {}).get("markdown_sha256"),
        "size_bytes": row.get("size_bytes"),
        "duplicate_of_source_id": metadata.get("duplicate_of"),
        "processing_status": status,
        "conversion_status": conversion_status,
        "stages": _stage_states(status, has_markdown),
        "artifact_paths": [p for p in [(package or {}).get("package_path")] if p],
        "last_error": last_error,
        "created_at": row.get("created_at"),
        "last_processed_at": row.get("updated_at"),
        "next_action": _next_action(status),
    }


def create_copilot_router(
    *,
    vault_path: Path,
    llm_client_factory: Callable[[], ChatLLM] | None = None,
    auth_token: str = "",
    rate_limiter: FixedWindowRateLimiter | None = None,
    trust_proxy_headers: bool = False,
    state_dir: Path | None = None,
    workflow_run_store: WorkflowRunStore | None = None,
    source_store: Any | None = None,
    source_retry_handler: Callable[[str], Any] | None = None,
) -> APIRouter:
    vault = vault_path.expanduser().resolve()
    state_root = (state_dir or SETTINGS.aily_data_dir).expanduser().resolve()
    search_service = VaultSearchService(vault)
    context_builder = CopilotContextEnvelopeBuilder()
    chat_service = CopilotVaultChatService(vault_search=search_service, context_builder=context_builder)
    dossier_service = DossierService()
    project_store = CopilotProjectStore(state_root / "copilot_projects.json")
    proposal_store = CopilotProposalStore(
        vault_path=vault,
        store_path=state_root / "copilot_proposals.json",
    )
    workflow_service = (
        CopilotWorkflowService(
            vault_search=search_service,
            workflow_run_store=workflow_run_store,
            tavily_api_key_configured=bool(str(SETTINGS.tavily_api_key or "").strip()),
            tavily_search_depth=SETTINGS.tavily_search_depth,
        )
        if workflow_run_store is not None
        else None
    )

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
            raise HTTPException(status_code=401, detail="Aily-Copilot authentication required")

    router = APIRouter(prefix="/api/copilot", tags=["copilot"], dependencies=[Depends(_require_auth)])

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

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return {
            "status": "ok",
            "vault_path": str(vault),
            "vault_exists": vault.exists(),
            "layout": inspect_v1_vault_layout(vault),
            "features": {
                "vault_search": True,
                "read_note": True,
                "context_envelope": True,
                "graph_neighborhood": True,
                "relevant_notes": True,
                "grounded_chat": True,
                "dossier_generation": True,
                "project_mode": True,
                "preview_writes": True,
                "comment_workflows": workflow_service is not None,
                "source_status": source_store is not None,
            },
        }

    @router.get("/config")
    async def get_config(request: Request) -> dict[str, Any]:
        _check_rate_limit(request)
        return _copilot_config_response()

    @router.post("/config")
    async def update_config(request: Request, payload: CopilotConfigUpdateRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            _apply_copilot_config_update(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _copilot_config_response()

    @router.post("/vault/search")
    async def search_vault(request: Request, payload: VaultSearchRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        return await asyncio.to_thread(
            search_service.search,
            payload.query,
            limit=payload.limit,
            include_dirs=payload.include_dirs,
            exclude_dirs=payload.exclude_dirs,
        )

    @router.post("/vault/read")
    async def read_note(request: Request, payload: ReadNoteRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return await asyncio.to_thread(
                search_service.read_note,
                payload.path,
                chunk_index=payload.chunk_index,
                chunk_lines=payload.chunk_lines,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/vault/neighborhood")
    async def graph_neighborhood(request: Request, payload: NeighborhoodRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return await asyncio.to_thread(search_service.neighborhood, payload.path, limit=payload.limit)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/vault/relevant")
    async def relevant_notes(request: Request, payload: RelevantNotesRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        project = project_store.get_project(payload.project_id)
        include_dirs, exclude_dirs, source_terms = _merge_scope(
            project=project,
            include_dirs=payload.include_dirs,
            exclude_dirs=payload.exclude_dirs,
        )
        query = " ".join([payload.query, *source_terms]).strip()
        return await asyncio.to_thread(
            search_service.relevant_notes,
            query=query,
            seed_paths=payload.seed_paths,
            include_dirs=include_dirs,
            exclude_dirs=exclude_dirs,
            limit=payload.limit,
        )

    @router.post("/context/envelope")
    async def context_envelope(request: Request, payload: ContextEnvelopeRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        return context_builder.build(
            user_message=payload.user_message,
            search_results=payload.search_results,
            previous_context=payload.previous_context,
            chat_history=payload.chat_history,
            system_prompt=payload.system_prompt,
        )

    @router.post("/chat")
    async def chat(request: Request, payload: ChatRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        project = project_store.get_project(payload.project_id)
        include_dirs, exclude_dirs, source_terms = _merge_scope(
            project=project,
            include_dirs=payload.include_dirs,
            exclude_dirs=payload.exclude_dirs,
        )
        search_query = payload.search_query or payload.message
        if source_terms:
            search_query = " ".join([search_query, *source_terms]).strip()
        llm_client = llm_client_factory() if payload.use_llm and llm_client_factory is not None else None
        return await chat_service.answer(
            message=payload.message,
            search_query=search_query,
            limit=payload.limit,
            include_dirs=include_dirs,
            exclude_dirs=exclude_dirs,
            chat_history=payload.chat_history,
            use_llm=payload.use_llm,
            llm_client=llm_client,
            system_prompt=str(project.get("system_prompt") or "") if project else "",
        )

    @router.get("/sources")
    async def list_sources(request: Request, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        _check_rate_limit(request)
        if source_store is None:
            raise HTTPException(status_code=503, detail="Source store unavailable")
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        result = await source_store.list_sources(limit=safe_limit, offset=safe_offset)
        rows = result.get("sources", []) if isinstance(result, dict) else []
        return {
            "total": int(result.get("total", len(rows))) if isinstance(result, dict) else len(rows),
            "limit": safe_limit,
            "offset": safe_offset,
            # List view omits the canonical markdown hash to avoid an N+1 lookup;
            # it is included in the per-source detail endpoint.
            "sources": [source_status_from_row(row, None) for row in rows],
        }

    @router.get("/sources/{source_id}")
    async def get_source_status(request: Request, source_id: str) -> dict[str, Any]:
        _check_rate_limit(request)
        if source_store is None:
            raise HTTPException(status_code=503, detail="Source store unavailable")
        row = await source_store.get_source(source_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Source not found")
        package = await source_store.get_markdown_package(source_id)
        return source_status_from_row(row, package)

    @router.post("/sources/{source_id}/retry")
    async def retry_source(request: Request, source_id: str) -> dict[str, Any]:
        _check_rate_limit(request)
        if source_retry_handler is None:
            raise HTTPException(status_code=503, detail="Source retry unavailable")
        result = await source_retry_handler(source_id)
        if isinstance(result, dict) and result.get("not_found"):
            raise HTTPException(status_code=404, detail="Source not found")
        return result

    @router.post("/dossiers/generate")
    async def generate_dossier(request: Request, payload: DossierGenerateRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        project = project_store.get_project(payload.project_id)
        query_terms = list(payload.query_terms or [payload.topic])
        if project:
            query_terms.extend(str(term) for term in project.get("source_terms", []) if str(term).strip())
        try:
            result = await asyncio.to_thread(
                dossier_service.build_and_write,
                DossierBuildRequest(
                    topic=payload.topic,
                    vault_path=vault,
                    query_terms=query_terms,
                    seed_claims=payload.seed_claims,
                    tavily_research_jobs=[],
                    max_vault_evidence=payload.max_vault_evidence,
                    max_tavily_evidence=payload.max_tavily_evidence,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Dossier generation failed: {exc}") from exc
        return {
            "dossier_id": result.draft.dossier_id,
            "topic": result.draft.topic,
            "title": result.draft.title,
            "output_path": str(result.output_path or ""),
            "relative_path": (
                str(result.output_path.relative_to(vault))
                if result.output_path is not None and _is_relative_to(result.output_path, vault)
                else ""
            ),
            "claim_count": len(result.draft.claims),
            "evidence_count": len(result.draft.evidence),
            "verification": result.draft.verification.__dict__ if result.draft.verification else None,
        }

    @router.get("/workflows/tools")
    async def workflow_tools(request: Request) -> dict[str, Any]:
        _check_rate_limit(request)
        return {
            "tools": [
                {
                    "name": WORKFLOW_TOOL_NAME,
                    "required_fields": ["topic", "comment"],
                    "optional_fields": ["scope", "requested_outputs", "max_vault_results", "use_tavily"],
                    "automation_policy": "explicit_user_instruction_only",
                    "local_search_first": True,
                    "external_research_policy": "placeholder_only_first_backend_slice",
                    "requested_outputs": [
                        "insight",
                        "wisdom",
                        "impact",
                        "research",
                        "evaluation",
                        "business_plan",
                        "dossier",
                    ],
                }
            ]
        }

    @router.get("/workflows")
    async def list_workflows(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return await _require_workflow_service().list_workflows(limit=limit, offset=offset, status=status)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/workflows/plan")
    async def plan_workflow(request: Request, payload: EndToEndValueWorkflowRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return await _require_workflow_service().plan_end_to_end_value_workflow(payload)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/workflows/run_end_to_end_value_workflow")
    async def run_end_to_end_value_workflow(
        request: Request,
        payload: EndToEndValueWorkflowRequest,
    ) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return await _require_workflow_service().run_end_to_end_value_workflow(payload)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/workflows/run")
    async def run_workflow(request: Request, payload: EndToEndValueWorkflowRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return await _require_workflow_service().run_end_to_end_value_workflow(payload)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/workflows/{workflow_run_id}")
    async def get_workflow(request: Request, workflow_run_id: str) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            workflow = await _require_workflow_service().get_workflow(workflow_run_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        return workflow

    @router.get("/workflows/{workflow_run_id}/events")
    async def get_workflow_events(request: Request, workflow_run_id: str) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            events = await _require_workflow_service().list_events(workflow_run_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if events is None:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        return events

    @router.get("/projects")
    async def list_projects(request: Request) -> dict[str, Any]:
        _check_rate_limit(request)
        return {"projects": project_store.list_projects()}

    @router.post("/projects/upsert")
    async def upsert_project(request: Request, payload: ProjectUpsertRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            project = project_store.upsert_project(
                project_id=payload.project_id,
                name=payload.name,
                description=payload.description,
                include_dirs=payload.include_dirs,
                exclude_dirs=payload.exclude_dirs,
                source_terms=payload.source_terms,
                system_prompt=payload.system_prompt,
                preferred_model=payload.preferred_model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"project": project}

    @router.post("/projects/delete")
    async def delete_project(request: Request, payload: ProjectDeleteRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        return {"deleted": project_store.delete_project(payload.project_id)}

    @router.post("/proposals/list")
    async def list_proposals(request: Request, payload: ProposalListRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        return {"proposals": proposal_store.list_proposals(payload.status)}

    @router.post("/proposals/create")
    async def create_proposal(request: Request, payload: ProposalCreateRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            proposal = proposal_store.create_proposal(
                target_path=payload.target_path,
                title=payload.title,
                content=payload.content,
                mode=payload.mode,
                rationale=payload.rationale,
                source_citations=payload.source_citations,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"proposal": proposal}

    @router.post("/proposals/apply")
    async def apply_proposal(request: Request, payload: ProposalActionRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return {"proposal": proposal_store.apply_proposal(payload.proposal_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/proposals/reject")
    async def reject_proposal(request: Request, payload: ProposalActionRequest) -> dict[str, Any]:
        _check_rate_limit(request)
        try:
            return {"proposal": proposal_store.reject_proposal(payload.proposal_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _require_workflow_service() -> CopilotWorkflowService:
        if workflow_service is None:
            raise HTTPException(status_code=503, detail="Copilot workflow store is not configured")
        return workflow_service

    return router


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _copilot_config_response() -> dict[str, Any]:
    routes = {
        "copilot_chat": _describe_route("copilot.chat"),
        "copilot_dossier": _describe_route("copilot.dossier"),
    }
    return {
        "llm_provider": SETTINGS.llm_provider,
        "llm_base_url": SETTINGS.llm_base_url,
        "llm_model": SETTINGS.llm_model,
        "kimi": {
            "model": SETTINGS.kimi_model,
            "vision_model": SETTINGS.kimi_vision_model,
            "api_key": _redacted_secret(SETTINGS.kimi_api_key),
        },
        "deepseek": {
            "model": SETTINGS.deepseek_model,
            "api_key": _redacted_secret(SETTINGS.deepseek_api_key),
        },
        "tavily": {
            "search_depth": SETTINGS.tavily_search_depth,
            "api_key": _redacted_secret(SETTINGS.tavily_api_key),
        },
        "runtime": {
            "timeout_seconds": SETTINGS.llm_timeout_seconds,
            "max_retries": SETTINGS.llm_max_retries,
            "max_concurrency": SETTINGS.llm_max_concurrency,
            "min_interval_seconds": SETTINGS.llm_min_interval_seconds,
        },
        "routes": routes,
        "workload_routes_json": _redacted_workload_routes_json(),
        "persistence": "env_file",
        "env_path": str(_settings_env_path()),
    }


def _describe_route(workload: str) -> dict[str, Any]:
    route = PrimaryLLMRoute.resolve_route(SETTINGS, workload=workload)
    return {
        "workload": route.workload,
        "provider": route.provider,
        "model": route.model,
        "base_url": route.base_url,
        "api_key_configured": bool(str(route.api_key or "").strip()),
    }


def _redacted_secret(value: str) -> dict[str, Any]:
    secret = str(value or "").strip()
    if not secret:
        return {"configured": False, "preview": ""}
    if len(secret) <= 8:
        preview = f"{secret[:2]}...{secret[-2:]}"
    else:
        preview = f"{secret[:4]}...{secret[-4:]}"
    return {"configured": True, "preview": preview}


def _redacted_workload_routes_json() -> str:
    raw = str(SETTINGS.llm_workload_routes_json or "").strip()
    if not raw:
        return ""
    try:
        routes = _load_workload_routes()
    except ValueError:
        return "<invalid json>"
    return json.dumps(_redact_mapping(routes), sort_keys=True)


def _redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower()
            if any(marker in normalized for marker in ("api_key", "token", "secret", "password")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_mapping(child)
        return redacted
    if isinstance(value, list):
        return [_redact_mapping(child) for child in value]
    return value


def _apply_copilot_config_update(payload: CopilotConfigUpdateRequest) -> None:
    env_updates: dict[str, str] = {}
    if payload.llm_provider is not None:
        SETTINGS.llm_provider = _validate_provider(payload.llm_provider)
        env_updates["LLM_PROVIDER"] = SETTINGS.llm_provider
    if payload.kimi_api_key is not None:
        SETTINGS.kimi_api_key = payload.kimi_api_key.strip()
        env_updates["KIMI_API_KEY"] = SETTINGS.kimi_api_key
    if payload.kimi_model is not None:
        SETTINGS.kimi_model = _required_text(payload.kimi_model, "kimi_model")
        env_updates["KIMI_MODEL"] = SETTINGS.kimi_model
    if payload.kimi_vision_model is not None:
        SETTINGS.kimi_vision_model = _required_text(payload.kimi_vision_model, "kimi_vision_model")
        env_updates["KIMI_VISION_MODEL"] = SETTINGS.kimi_vision_model
    if payload.deepseek_api_key is not None:
        SETTINGS.deepseek_api_key = payload.deepseek_api_key.strip()
        env_updates["DEEPSEEK_API_KEY"] = SETTINGS.deepseek_api_key
    if payload.deepseek_model is not None:
        SETTINGS.deepseek_model = _required_text(payload.deepseek_model, "deepseek_model")
        env_updates["DEEPSEEK_MODEL"] = SETTINGS.deepseek_model
    if payload.tavily_api_key is not None:
        SETTINGS.tavily_api_key = payload.tavily_api_key.strip()
        env_updates["TAVILY_API_KEY"] = SETTINGS.tavily_api_key
    if payload.tavily_search_depth is not None:
        depth = payload.tavily_search_depth.strip().lower()
        if depth not in {"basic", "advanced"}:
            raise ValueError("tavily_search_depth must be either 'basic' or 'advanced'")
        SETTINGS.tavily_search_depth = depth
        env_updates["TAVILY_SEARCH_DEPTH"] = SETTINGS.tavily_search_depth
    if payload.llm_timeout_seconds is not None:
        SETTINGS.llm_timeout_seconds = payload.llm_timeout_seconds
        env_updates["LLM_TIMEOUT_SECONDS"] = str(SETTINGS.llm_timeout_seconds)
    if payload.llm_max_retries is not None:
        SETTINGS.llm_max_retries = payload.llm_max_retries
        env_updates["LLM_MAX_RETRIES"] = str(SETTINGS.llm_max_retries)
    if payload.llm_max_concurrency is not None:
        SETTINGS.llm_max_concurrency = payload.llm_max_concurrency
        env_updates["LLM_MAX_CONCURRENCY"] = str(SETTINGS.llm_max_concurrency)
    if payload.llm_min_interval_seconds is not None:
        SETTINGS.llm_min_interval_seconds = payload.llm_min_interval_seconds
        env_updates["LLM_MIN_INTERVAL_SECONDS"] = str(SETTINGS.llm_min_interval_seconds)

    if payload.copilot_chat_provider is not None:
        _set_workload_provider("copilot.chat", payload.copilot_chat_provider)
    if payload.copilot_dossier_provider is not None:
        _set_workload_provider("copilot.dossier", payload.copilot_dossier_provider)

    _sync_primary_llm_settings()
    if payload.copilot_chat_provider is not None or payload.copilot_dossier_provider is not None:
        env_updates["LLM_WORKLOAD_ROUTES_JSON"] = SETTINGS.llm_workload_routes_json
    if payload.llm_provider is not None:
        env_updates["LLM_BASE_URL"] = SETTINGS.llm_base_url
        env_updates["LLM_MODEL"] = SETTINGS.llm_model
    if env_updates:
        _write_env_updates(env_updates)


def _validate_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized not in {"kimi", "deepseek"}:
        raise ValueError("Provider must be either 'kimi' or 'deepseek'")
    return normalized


def _required_text(value: str, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field_name} cannot be empty")
    return clean


def _set_workload_provider(workload: str, provider: str) -> None:
    normalized = _validate_provider(provider)
    routes = _load_workload_routes()
    route = dict(routes.get(workload, {}))
    route["provider"] = normalized
    routes[workload] = route
    SETTINGS.llm_workload_routes_json = json.dumps(routes, sort_keys=True)


def _load_workload_routes() -> dict[str, dict[str, Any]]:
    raw = str(SETTINGS.llm_workload_routes_json or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid llm_workload_routes_json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("llm_workload_routes_json must decode to an object")
    routes: dict[str, dict[str, Any]] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, dict):
            routes[key.strip().lower()] = dict(value)
    return routes


def _sync_primary_llm_settings() -> None:
    provider = _validate_provider(SETTINGS.llm_provider)
    SETTINGS.llm_provider = provider
    if provider == "kimi":
        SETTINGS.llm_base_url = "https://api.moonshot.cn/v1"
        SETTINGS.llm_model = SETTINGS.kimi_model or SETTINGS.llm_model
        SETTINGS.llm_api_key = SETTINGS.kimi_api_key or SETTINGS.llm_api_key
        return
    SETTINGS.llm_base_url = "https://api.deepseek.com"
    SETTINGS.llm_model = SETTINGS.deepseek_model or SETTINGS.llm_model
    SETTINGS.llm_api_key = SETTINGS.deepseek_api_key or SETTINGS.llm_api_key


def _settings_env_path() -> Path:
    env_file = SETTINGS.model_config.get("env_file", ".env")
    if isinstance(env_file, (list, tuple)):
        env_file = env_file[0] if env_file else ".env"
    path = Path(str(env_file))
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _write_env_updates(updates: dict[str, str]) -> None:
    env_path = _settings_env_path()
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated_keys: set[str] = set()
    rendered: list[str] = []
    assignment_pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in lines:
        match = assignment_pattern.match(line)
        key = match.group(1) if match else ""
        if key in updates:
            rendered.append(f"{key}={_format_env_value(updates[key])}")
            updated_keys.add(key)
        else:
            rendered.append(line)
    for key, value in updates.items():
        if key not in updated_keys:
            rendered.append(f"{key}={_format_env_value(value)}")
    env_path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")


def _format_env_value(value: str) -> str:
    clean = str(value)
    if clean == "":
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", clean):
        return clean
    return json.dumps(clean)


def _merge_scope(
    *,
    project: dict[str, Any] | None,
    include_dirs: list[str],
    exclude_dirs: list[str],
) -> tuple[list[str], list[str], list[str]]:
    if not project:
        return include_dirs, exclude_dirs, []
    merged_include = _merge_unique(include_dirs, list(project.get("include_dirs") or []))
    merged_exclude = _merge_unique(exclude_dirs, list(project.get("exclude_dirs") or []))
    source_terms = [str(term) for term in project.get("source_terms", []) if str(term).strip()]
    return merged_include, merged_exclude, source_terms


def _merge_unique(first: list[str], second: list[str]) -> list[str]:
    result: list[str] = []
    for value in [*first, *second]:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result
