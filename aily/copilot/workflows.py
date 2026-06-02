"""Comment-based workflow orchestration primitives for Aily-Copilot."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from aily.copilot.vault import VaultSearchService
from aily.orchestration.runs import WorkflowRunStore
from aily.orchestration.state import WorkflowRunSnapshot

WORKFLOW_TOOL_NAME = "run_end_to_end_value_workflow"
WORKFLOW_KIND = "copilot_value_workflow"
ALLOWED_OUTPUTS = {
    "insight",
    "wisdom",
    "impact",
    "research",
    "evaluation",
    "business_plan",
    "dossier",
}
DEFAULT_OUTPUTS = [
    "insight",
    "wisdom",
    "impact",
    "research",
    "evaluation",
    "business_plan",
    "dossier",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CopilotWorkflowScope(BaseModel):
    note_paths: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    project_id: str = ""
    source_ids: list[str] = Field(default_factory=list)

    @field_validator("note_paths", "folders", "tags", "source_ids")
    @classmethod
    def _clean_list(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value or []:
            text = str(item or "").strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned


class EndToEndValueWorkflowRequest(BaseModel):
    tool: Literal["run_end_to_end_value_workflow"] = WORKFLOW_TOOL_NAME
    topic: str = Field(min_length=1, max_length=300)
    comment: str = Field(min_length=1, max_length=5000)
    scope: CopilotWorkflowScope = Field(default_factory=CopilotWorkflowScope)
    requested_outputs: list[str] = Field(default_factory=lambda: list(DEFAULT_OUTPUTS))
    max_vault_results: int = Field(default=12, ge=1, le=50)
    use_tavily: bool = False

    @field_validator("topic", "comment")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("value cannot be empty")
        return clean

    @field_validator("requested_outputs")
    @classmethod
    def _normalize_outputs(cls, value: list[str]) -> list[str]:
        outputs: list[str] = []
        for item in value or DEFAULT_OUTPUTS:
            normalized = str(item or "").strip().lower().replace("-", "_")
            if not normalized:
                continue
            if normalized not in ALLOWED_OUTPUTS:
                raise ValueError(f"unsupported requested output: {item}")
            if normalized not in outputs:
                outputs.append(normalized)
        return outputs or list(DEFAULT_OUTPUTS)


class CopilotWorkflowService:
    """First backend slice for post-Knowledge Copilot workflow runs.

    This service is intentionally conservative: it persists the explicit user
    comment and run metadata, performs local vault/Knowledge retrieval first,
    and records placeholders for expensive downstream work without calling
    external research or generation providers.
    """

    def __init__(
        self,
        *,
        vault_search: VaultSearchService,
        workflow_run_store: WorkflowRunStore,
        tavily_api_key_configured: bool = False,
        tavily_search_depth: str = "basic",
    ) -> None:
        self.vault_search = vault_search
        self.workflow_run_store = workflow_run_store
        self.tavily_api_key_configured = tavily_api_key_configured
        self.tavily_search_depth = tavily_search_depth or "basic"

    async def plan_end_to_end_value_workflow(
        self,
        request: EndToEndValueWorkflowRequest,
    ) -> dict[str, Any]:
        plan = await self._build_plan(request)
        run = await self.workflow_run_store.create_run(
            workflow_kind=WORKFLOW_KIND,  # type: ignore[arg-type]
            input_summary=f"{request.topic}: {request.comment}"[:500],
            metadata={
                "tool": request.tool,
                "topic": request.topic,
                "comment": request.comment,
                "scope": request.scope.model_dump(),
                "requested_outputs": request.requested_outputs,
                "knowledge_search_query": plan["knowledge_search"]["query"],
                "local_search_completed": True,
                "tavily_query": plan["research"]["query"],
                "provider_routes": plan["provider_routes"],
                "evidence_set": plan["evidence_set"],
                "generated_artifact_paths": [],
                "plan": plan,
                "events": [
                    _event(
                        "workflow_planned",
                        node="plan",
                        topic=request.topic,
                        requested_outputs=request.requested_outputs,
                        local_evidence_count=plan["evidence_set"]["local_evidence_count"],
                    )
                ],
            },
        )
        return _workflow_response(
            workflow_run=_workflow_run_payload(run),
            plan=plan,
            events=run.metadata.get("events", []),
        )

    async def run_end_to_end_value_workflow(
        self,
        request: EndToEndValueWorkflowRequest,
    ) -> dict[str, Any]:
        planned = await self.plan_end_to_end_value_workflow(request)
        run_id = planned["workflow_run"]["workflow_run_id"]
        final_run = await self._run_planned_slice(run_id)
        return _workflow_response(
            workflow_run=_workflow_run_payload(final_run),
            plan=final_run.metadata.get("plan", planned["plan"]),
            events=final_run.metadata.get("events", []),
        )

    async def get_workflow(self, workflow_run_id: str) -> dict[str, Any] | None:
        run = await self.workflow_run_store.get_run(workflow_run_id)
        if run is None:
            return None
        return _workflow_response(
            workflow_run=_workflow_run_payload(run),
            plan=run.metadata.get("plan"),
            events=run.metadata.get("events", []),
        )

    async def list_workflows(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        safe_status = status if status in {"queued", "running", "interrupted", "completed", "failed", "cancelled"} else None
        safe_limit = max(1, min(500, limit))
        safe_offset = max(0, offset)
        runs = await self.workflow_run_store.list_runs(
            limit=500,
            offset=0,
            status=safe_status,  # type: ignore[arg-type]
        )
        filtered = [run for run in runs if run.workflow_kind == WORKFLOW_KIND]
        page = filtered[safe_offset : safe_offset + safe_limit]
        return {
            "total": len(filtered),
            "workflows": [_workflow_run_payload(run) for run in page],
        }

    async def list_events(self, workflow_run_id: str) -> dict[str, Any] | None:
        run = await self.workflow_run_store.get_run(workflow_run_id)
        if run is None:
            return None
        history = await self.workflow_run_store.list_run_history(workflow_run_id)
        return {
            "workflow_run_id": workflow_run_id,
            "events": run.metadata.get("events", []),
            "history": history,
        }

    async def _build_plan(self, request: EndToEndValueWorkflowRequest) -> dict[str, Any]:
        query = _build_knowledge_search_query(request)
        include_dirs = list(request.scope.folders)
        knowledge_results, vault_results, selected_notes = await asyncio.gather(
            asyncio.to_thread(
                self.vault_search.search,
                query,
                limit=request.max_vault_results,
                include_dirs=["03-Knowledge"],
            ),
            asyncio.to_thread(
                self.vault_search.search,
                query,
                limit=request.max_vault_results,
                include_dirs=include_dirs,
            ),
            asyncio.to_thread(self._read_selected_notes, request.scope.note_paths),
        )
        frontmatter_by_path = await asyncio.to_thread(
            self._frontmatter_for_results,
            knowledge_results,
            vault_results,
        )
        evidence_items = _build_evidence_items(
            knowledge_results=knowledge_results,
            vault_results=vault_results,
            selected_notes=selected_notes,
            explicit_source_ids=request.scope.source_ids,
            frontmatter_by_path=frontmatter_by_path,
        )
        source_ids = sorted(
            {
                source_id
                for item in evidence_items
                for source_id in item.get("source_ids", [])
                if str(source_id).strip()
            }
            | set(request.scope.source_ids)
        )
        tavily_query = _build_tavily_query(request) if _wants_research(request) else ""
        planned_steps = _build_planned_steps(request.requested_outputs, wants_research=bool(tavily_query))
        return {
            "tool": request.tool,
            "topic": request.topic,
            "comment": request.comment,
            "scope": request.scope.model_dump(),
            "requested_outputs": request.requested_outputs,
            "knowledge_search": {
                "query": query,
                "policy": "local_vault_and_knowledge_first",
                "knowledge_returned": int(knowledge_results.get("returned", 0) or 0),
                "vault_returned": int(vault_results.get("returned", 0) or 0),
                "selected_note_count": len(selected_notes),
            },
            "evidence_set": {
                "local_evidence_count": len(evidence_items),
                "vault_evidence": evidence_items,
                "tavily_evidence": [],
                "source_ids": source_ids,
                "evidence_gaps": _evidence_gaps(request, evidence_items),
            },
            "research": {
                "provider": "tavily",
                "query": tavily_query,
                "requested": _wants_research(request),
                "api_key_configured": self.tavily_api_key_configured,
                "search_depth": self.tavily_search_depth,
                "execution_policy": "placeholder_only_first_backend_slice",
                "status": "not_called",
            },
            "provider_routes": {
                "local_search": "vault_lexical_search",
                "tavily": "placeholder_only",
                "iwi": "reserved_not_invoked",
                "evaluation": "reserved_not_invoked",
                "business_plan": "reserved_not_invoked",
                "dossier": "reserved_not_invoked",
            },
            "planned_steps": planned_steps,
            "generated_artifact_paths": [],
            "safety": {
                "external_calls_made": False,
                "silent_user_note_edits": False,
                "local_search_first_completed": True,
                "unsupported_claim_policy": "label_gaps_do_not_invent",
            },
        }

    def _read_selected_notes(self, note_paths: list[str]) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        for path in note_paths:
            try:
                notes.append(self.vault_search.read_note(path, chunk_lines=80))
            except (FileNotFoundError, ValueError) as exc:
                notes.append(
                    {
                        "relative_path": path,
                        "title": path,
                        "error": str(exc),
                        "content": "",
                        "frontmatter": {},
                    }
                )
        return notes

    def _frontmatter_for_results(self, *search_results: dict[str, Any]) -> dict[str, dict[str, Any]]:
        frontmatter_by_path: dict[str, dict[str, Any]] = {}
        for search_result in search_results:
            for item in search_result.get("results", []) or []:
                relative_path = str(item.get("relative_path") or "").strip()
                if not relative_path or relative_path in frontmatter_by_path:
                    continue
                try:
                    note = self.vault_search.read_note(relative_path, chunk_lines=25)
                except (FileNotFoundError, ValueError):
                    continue
                frontmatter = note.get("frontmatter", {})
                if isinstance(frontmatter, dict):
                    frontmatter_by_path[relative_path] = frontmatter
        return frontmatter_by_path

    async def _run_planned_slice(self, workflow_run_id: str) -> WorkflowRunSnapshot:
        run = await self.workflow_run_store.get_run(workflow_run_id)
        if run is None:
            raise KeyError(f"Workflow run not found: {workflow_run_id}")
        metadata = dict(run.metadata)
        plan = dict(metadata.get("plan", {}))
        requested_outputs = list(metadata.get("requested_outputs", []))

        await self._append_status_event(
            workflow_run_id,
            status="running",
            current_node="search_knowledge_base",
            event_type="local_search_completed",
            local_evidence_count=plan.get("evidence_set", {}).get("local_evidence_count", 0),
            knowledge_search_query=metadata.get("knowledge_search_query", ""),
        )
        await self._append_status_event(
            workflow_run_id,
            status="running",
            current_node="construct_evidence_set",
            event_type="evidence_set_constructed",
            source_ids=plan.get("evidence_set", {}).get("source_ids", []),
            evidence_gaps=plan.get("evidence_set", {}).get("evidence_gaps", []),
        )
        if plan.get("research", {}).get("requested"):
            await self._append_status_event(
                workflow_run_id,
                status="running",
                current_node="record_tavily_research_placeholder",
                event_type="tavily_placeholder_recorded",
                tavily_query=plan.get("research", {}).get("query", ""),
                api_key_configured=plan.get("research", {}).get("api_key_configured", False),
                external_calls_made=False,
            )

        for output in requested_outputs:
            await self._append_status_event(
                workflow_run_id,
                status="running",
                current_node=f"reserve_{output}",
                event_type="output_step_reserved",
                output=output,
                execution_policy="reserved_not_invoked_in_first_backend_slice",
            )

        return await self._append_status_event(
            workflow_run_id,
            status="interrupted",
            current_node="awaiting_post_knowledge_implementation",
            event_type="workflow_interrupted_after_safe_planning_slice",
            interruption_reason="downstream_iwi_research_evaluation_business_plan_dossier_execution_not_implemented_in_this_slice",
            generated_artifact_paths=[],
        )

    async def _append_status_event(
        self,
        workflow_run_id: str,
        *,
        status: str,
        current_node: str,
        event_type: str,
        **payload: Any,
    ) -> WorkflowRunSnapshot:
        existing = await self.workflow_run_store.get_run(workflow_run_id)
        if existing is None:
            raise KeyError(f"Workflow run not found: {workflow_run_id}")
        events = list(existing.metadata.get("events", []))
        events.append(_event(event_type, node=current_node, **payload))
        return await self.workflow_run_store.update_status(
            workflow_run_id,
            status=status,  # type: ignore[arg-type]
            current_node=current_node,
            metadata={"events": events, **payload},
        )


def _event(event_type: str, *, node: str, **payload: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "node": node,
        "created_at": _utc_now(),
        **payload,
    }


def _workflow_run_payload(run: WorkflowRunSnapshot) -> dict[str, Any]:
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


def _workflow_response(*, workflow_run: dict[str, Any], plan: dict[str, Any] | None, events: list[dict[str, Any]]) -> dict[str, Any]:
    safe_plan = plan or {}
    evidence_set = safe_plan.get("evidence_set", {}) if isinstance(safe_plan.get("evidence_set", {}), dict) else {}
    research = safe_plan.get("research", {}) if isinstance(safe_plan.get("research", {}), dict) else {}
    provider_routes = safe_plan.get("provider_routes", {}) if isinstance(safe_plan.get("provider_routes", {}), dict) else {}
    current_step = workflow_run.get("current_node") or ""
    artifact_paths = list(safe_plan.get("generated_artifact_paths", []) or [])
    metadata_paths = workflow_run.get("metadata", {}).get("generated_artifact_paths", [])
    if metadata_paths:
        artifact_paths = list(metadata_paths)
    return {
        "workflow_run_id": workflow_run.get("workflow_run_id", ""),
        "status": workflow_run.get("status", ""),
        "topic": safe_plan.get("topic") or workflow_run.get("metadata", {}).get("topic", ""),
        "comment": safe_plan.get("comment") or workflow_run.get("metadata", {}).get("comment", ""),
        "planned_steps": list(safe_plan.get("planned_steps", []) or []),
        "current_step": current_step,
        "evidence_summary": {
            "local_evidence_count": int(evidence_set.get("local_evidence_count", 0) or 0),
            "vault_evidence_count": len(evidence_set.get("vault_evidence", []) or []),
            "tavily_evidence_count": len(evidence_set.get("tavily_evidence", []) or []),
            "source_ids": list(evidence_set.get("source_ids", []) or []),
            "evidence_gaps": list(evidence_set.get("evidence_gaps", []) or []),
        },
        "provider_usage": {
            "external_calls_made": False,
            "tavily": {
                "requested": bool(research.get("requested", False)),
                "status": research.get("status", "not_called"),
                "query": research.get("query", ""),
                "api_key_configured": bool(research.get("api_key_configured", False)),
                "search_depth": research.get("search_depth", ""),
            },
            "routes": provider_routes,
        },
        "artifact_paths": artifact_paths,
        "workflow_run": workflow_run,
        "plan": safe_plan,
        "events": events,
    }


def _build_knowledge_search_query(request: EndToEndValueWorkflowRequest) -> str:
    parts = [request.topic, request.comment, *request.scope.tags, *request.scope.source_ids]
    tokens = _keyword_tokens(" ".join(parts))
    return " ".join(tokens[:24]) or request.topic


def _build_tavily_query(request: EndToEndValueWorkflowRequest) -> str:
    tokens = _keyword_tokens(f"{request.topic} {request.comment}")
    return " ".join([*tokens[:14], "market", "technical", "commercial", "risk"]).strip()


def _keyword_tokens(text: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "again",
        "also",
        "analyze",
        "and",
        "are",
        "for",
        "from",
        "into",
        "our",
        "search",
        "that",
        "the",
        "then",
        "this",
        "use",
        "with",
        "what",
        "whether",
    }
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_\-]{3,}", text.lower()):
        if token in stopwords:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _wants_research(request: EndToEndValueWorkflowRequest) -> bool:
    return request.use_tavily or "research" in request.requested_outputs


def _build_planned_steps(requested_outputs: list[str], *, wants_research: bool) -> list[dict[str, Any]]:
    steps = [
        {
            "step": "search_knowledge_base",
            "status": "planned",
            "execution_policy": "mandatory_local_first",
        },
        {
            "step": "construct_evidence_set",
            "status": "planned",
            "execution_policy": "local_vault_evidence_only_first",
        },
    ]
    if wants_research:
        steps.append(
            {
                "step": "run_deep_research",
                "status": "reserved",
                "execution_policy": "tavily_placeholder_only_first_backend_slice",
            }
        )
    for output in requested_outputs:
        if output == "research":
            continue
        steps.append(
            {
                "step": f"generate_{output}" if output in {"business_plan", "dossier"} else f"run_{output}",
                "output": output,
                "status": "reserved",
                "execution_policy": "not_invoked_in_first_backend_slice",
            }
        )
    return steps


def _build_evidence_items(
    *,
    knowledge_results: dict[str, Any],
    vault_results: dict[str, Any],
    selected_notes: list[dict[str, Any]],
    explicit_source_ids: list[str],
    frontmatter_by_path: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for note in selected_notes:
        rel = str(note.get("relative_path") or "").strip()
        if not rel or rel in seen_paths:
            continue
        seen_paths.add(rel)
        frontmatter = note.get("frontmatter", {}) if isinstance(note.get("frontmatter"), dict) else {}
        items.append(
            {
                "evidence_id": f"L{len(items) + 1:03d}",
                "origin": "vault",
                "evidence_type": _evidence_type_for_path(rel),
                "relative_path": rel,
                "title": str(note.get("title") or rel),
                "score": 1.0,
                "excerpt": str(note.get("content") or "")[:800],
                "sha256": str(note.get("sha256") or ""),
                "source_ids": _source_ids_from_frontmatter(frontmatter, explicit_source_ids),
                "selected_by_scope": True,
                "error": str(note.get("error") or ""),
            }
        )
    for result in list(knowledge_results.get("results", []) or []) + list(vault_results.get("results", []) or []):
        rel = str(result.get("relative_path") or "").strip()
        if not rel or rel in seen_paths:
            continue
        seen_paths.add(rel)
        items.append(
            {
                "evidence_id": f"L{len(items) + 1:03d}",
                "origin": "vault",
                "evidence_type": _evidence_type_for_path(rel),
                "relative_path": rel,
                "title": str(result.get("title") or rel),
                "citation_id": str(result.get("citation_id") or ""),
                "score": float(result.get("score") or 0.0),
                "excerpt": str(result.get("excerpt") or ""),
                "sha256": str(result.get("sha256") or ""),
                "source_ids": _source_ids_from_frontmatter(
                    frontmatter_by_path.get(rel, {}),
                    explicit_source_ids,
                ),
                "selected_by_scope": False,
            }
        )
    return items


def _evidence_type_for_path(relative_path: str) -> str:
    if relative_path.startswith("03-Knowledge/"):
        return "knowledge_note"
    if relative_path.startswith("00-Chaos/"):
        return "source_package"
    if relative_path.startswith("01-Data/"):
        return "data_artifact"
    if relative_path.startswith("02-Information/"):
        return "information_artifact"
    return "vault_note"


def _source_ids_from_frontmatter(frontmatter: dict[str, Any], fallback: list[str]) -> list[str]:
    candidates: list[Any] = [
        frontmatter.get("source_id"),
        frontmatter.get("source_ids"),
        frontmatter.get("aily_source_id"),
    ]
    source_ids: list[str] = []
    for candidate in candidates:
        values = candidate if isinstance(candidate, list) else [candidate]
        for value in values:
            text = str(value or "").strip()
            if text and text not in source_ids:
                source_ids.append(text)
    for source_id in fallback:
        if source_id not in source_ids:
            source_ids.append(source_id)
    return source_ids


def _evidence_gaps(request: EndToEndValueWorkflowRequest, evidence_items: list[dict[str, Any]]) -> list[str]:
    gaps: list[str] = []
    if not evidence_items:
        gaps.append("No matching local vault or Knowledge evidence found for the workflow topic/comment.")
    if "research" in request.requested_outputs or request.use_tavily:
        gaps.append("External Tavily evidence has not been fetched in this first backend slice.")
    for output in request.requested_outputs:
        if output != "research":
            gaps.append(f"{output} artifact generation is reserved but not executed in this first backend slice.")
    return gaps
