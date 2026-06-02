from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from aily.orchestration.chat_store import ChatStore, build_iwi_workflow_steps, extract_candidate_topics
from aily.orchestration.runs import WorkflowRunStore
from aily.orchestration.state import WorkflowState


ContextSelector = Callable[[str, list[dict[str, Any]], list[str]], Awaitable[list[dict[str, Any]]]]
TriggeredIwiRunner = Callable[[str, str, list[str]], Awaitable[Any]]
ResearchRunner = Callable[[WorkflowState], Awaitable[dict[str, Any]]]
BusinessPlanRunner = Callable[[WorkflowState], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class BusinessPlanningDependencies:
    chat_store: ChatStore
    workflow_run_store: WorkflowRunStore
    select_context: ContextSelector | None = None
    run_iwi: TriggeredIwiRunner | None = None
    run_research: ResearchRunner | None = None
    run_business_plan: BusinessPlanRunner | None = None
    emit_event: Callable[..., Awaitable[None]] | None = None


async def _emit(deps: BusinessPlanningDependencies, event_type: str, **payload: Any) -> None:
    if deps.emit_event is not None:
        await deps.emit_event(event_type, **payload)


async def _set_status(
    deps: BusinessPlanningDependencies,
    state: WorkflowState,
    *,
    status: str,
    current_node: str,
    metadata: dict[str, Any] | None = None,
    last_error: str | None = None,
) -> None:
    workflow_run_id = state.get("workflow_run_id", "")
    if workflow_run_id:
        await deps.workflow_run_store.update_status(
            workflow_run_id,
            status=status,  # type: ignore[arg-type]
            current_node=current_node,
            metadata=metadata or {},
            last_error=last_error,
        )


def _append_step(state: WorkflowState, step: str, **updates: Any) -> WorkflowState:
    steps = [*state.get("steps", []), step]
    return {
        **updates,
        "steps": steps,
        "current_node": step,
        "status": updates.get("status", "running"),
    }


def _selected_graph_node_ids(state: WorkflowState) -> list[str]:
    node_ids: list[str] = []
    for item in state.get("knowledge_context", []):
        if not isinstance(item, dict):
            continue
        if item.get("context_type") != "graph_information_node":
            continue
        node_id = str(item.get("node_id") or "").strip()
        if node_id and node_id not in node_ids:
            node_ids.append(node_id)
    return node_ids


def _stage_name(stage: Any) -> str:
    return str(getattr(stage, "name", stage) or "")


def _failed_stage(result: Any) -> Any | None:
    for stage_result in getattr(result, "stage_results", []) or []:
        if getattr(stage_result, "success", False) is False:
            return stage_result
    return None


def _iwi_result_summary(result: Any) -> dict[str, Any]:
    stage_results = list(getattr(result, "stage_results", []) or [])
    final_stage = ""
    successful = [item for item in stage_results if getattr(item, "success", False)]
    if successful:
        final_stage = _stage_name(getattr(successful[-1], "stage", ""))
    return {
        "pipeline_id": str(getattr(result, "pipeline_id", "") or ""),
        "input_id": str(getattr(result, "input_id", "") or ""),
        "final_stage": final_stage,
        "stage_count": len(stage_results),
        "stages": [
            {
                "stage": _stage_name(getattr(item, "stage", "")),
                "success": bool(getattr(item, "success", False)),
                "items_processed": int(getattr(item, "items_processed", 0) or 0),
                "items_output": int(getattr(item, "items_output", 0) or 0),
                "error_message": str(getattr(item, "error_message", "") or ""),
            }
            for item in stage_results
        ],
    }


async def _default_select_context(
    _motive: str,
    _topics: list[dict[str, Any]],
    _source_ids: list[str],
) -> list[dict[str, Any]]:
    return []


async def receive_motive(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    motive = state.get("motive", "").strip()
    if not motive:
        raise ValueError("BusinessPlanningGraph requires a motive")
    await _set_status(deps, state, status="running", current_node="receive_motive")

    chat_thread_id = state.get("chat_thread_id", "")
    if chat_thread_id:
        thread = await deps.chat_store.get_thread(chat_thread_id)
        if thread is None:
            raise KeyError(f"Chat thread not found: {chat_thread_id}")
    else:
        thread = await deps.chat_store.create_thread(
            title=motive[:80],
            metadata={
                "created_from": "business_planning_graph",
                "workflow_run_id": state.get("workflow_run_id", ""),
            },
        )
        chat_thread_id = thread["chat_thread_id"]

    message_id = state.get("message_id", "")
    if message_id:
        message = await deps.chat_store.get_message(message_id)
        if message is None:
            raise KeyError(f"Chat message not found: {message_id}")
    else:
        message = await deps.chat_store.add_message(
            chat_thread_id,
            role="user",
            content=motive,
            metadata={
                "source_ids": state.get("source_ids", []),
                "created_from": "business_planning_graph",
                "workflow_run_id": state.get("workflow_run_id", ""),
            },
        )
        message_id = message["message_id"]

    await _emit(
        deps,
        "business_planning_motive_received",
        workflow_run_id=state.get("workflow_run_id", ""),
        chat_thread_id=chat_thread_id,
        message_id=message_id,
    )
    return _append_step(
        state,
        "receive_motive",
        chat_thread_id=chat_thread_id,
        message_id=message_id,
        motive=motive,
    )


async def extract_topics(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    await _set_status(deps, state, status="running", current_node="extract_topics")
    topics = state.get("topics") or extract_candidate_topics(state.get("motive", ""))
    topic_ids = [str(topic.get("topic_id")) for topic in topics if topic.get("topic_id")]
    await _emit(
        deps,
        "business_planning_topics_extracted",
        workflow_run_id=state.get("workflow_run_id", ""),
        chat_thread_id=state.get("chat_thread_id", ""),
        topic_count=len(topics),
    )
    return _append_step(state, "extract_topics", topics=topics, topic_ids=topic_ids)


async def search_knowledge_context(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    await _set_status(deps, state, status="running", current_node="search_knowledge_context")
    select_context = deps.select_context or _default_select_context
    source_ids = [str(item) for item in state.get("source_ids", []) if str(item).strip()]
    knowledge_context = state.get("knowledge_context") or await select_context(
        state.get("motive", ""),
        list(state.get("topics", [])),
        source_ids,
    )
    await _emit(
        deps,
        "business_planning_context_selected",
        workflow_run_id=state.get("workflow_run_id", ""),
        chat_thread_id=state.get("chat_thread_id", ""),
        context_count=len(knowledge_context),
    )
    return _append_step(
        state,
        "search_knowledge_context",
        knowledge_context=knowledge_context,
    )


async def draft_workflow_plan(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    await _set_status(deps, state, status="running", current_node="draft_workflow_plan")
    workflow_plan_id = state.get("workflow_plan_id", "")
    topic_extraction_id = state.get("topic_extraction_id", "")
    if workflow_plan_id:
        plan = await deps.chat_store.get_workflow_plan(workflow_plan_id)
        if plan is None:
            raise KeyError(f"Workflow plan not found: {workflow_plan_id}")
    else:
        if topic_extraction_id:
            topic_extraction = {"topic_extraction_id": topic_extraction_id}
        else:
            topic_extraction = await deps.chat_store.create_topic_extraction(
                chat_thread_id=state["chat_thread_id"],
                message_id=state["message_id"],
                motive=state.get("motive", ""),
                topics=list(state.get("topics", [])),
                knowledge_context=list(state.get("knowledge_context", [])),
                metadata={
                    "created_from": "business_planning_graph",
                    "workflow_run_id": state.get("workflow_run_id", ""),
                },
            )
            topic_extraction_id = topic_extraction["topic_extraction_id"]
        plan = await deps.chat_store.create_workflow_plan(
            chat_thread_id=state["chat_thread_id"],
            message_id=state["message_id"],
            topic_extraction_id=topic_extraction["topic_extraction_id"],
            plan_type="business_planning",
            motive=state.get("motive", ""),
            topics=list(state.get("topics", [])),
            knowledge_context=list(state.get("knowledge_context", [])),
            proposed_steps=build_iwi_workflow_steps(research_required=bool(state.get("research_required", False))),
            metadata={
                "created_from": "business_planning_graph",
                "workflow_run_id": state.get("workflow_run_id", ""),
                "requires_confirmation": True,
                "source_ids": state.get("source_ids", []),
                "context_count": len(state.get("knowledge_context", [])),
            },
        )
        workflow_plan_id = plan["workflow_plan_id"]
        await deps.chat_store.add_message(
            state["chat_thread_id"],
            role="assistant",
            content="Business planning workflow proposed. Awaiting confirmation.",
            metadata={
                "workflow_plan_id": workflow_plan_id,
                "topic_extraction_id": topic_extraction_id,
                "notification_type": "workflow_plan_awaiting_confirmation",
                "created_from": "business_planning_graph",
            },
        )

    await _emit(
        deps,
        "business_planning_workflow_plan_proposed",
        workflow_run_id=state.get("workflow_run_id", ""),
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=workflow_plan_id,
        status=plan.get("status", ""),
    )
    return _append_step(
        state,
        "draft_workflow_plan",
        topic_extraction_id=topic_extraction_id,
        workflow_plan_id=workflow_plan_id,
        workflow_plan=plan,
        approvals={
            **state.get("approvals", {}),
            "workflow_plan": "pending",
        },
    )


async def await_user_confirmation(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    await _set_status(
        deps,
        state,
        status="interrupted",
        current_node="await_user_confirmation",
        metadata={
            "chat_thread_id": state.get("chat_thread_id", ""),
            "workflow_plan_id": state.get("workflow_plan_id", ""),
        },
    )
    await _emit(
        deps,
        "business_planning_confirmation_required",
        workflow_run_id=state.get("workflow_run_id", ""),
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
    )
    response = interrupt(
        {
            "action": "confirm_workflow_plan",
            "workflow_run_id": state["workflow_run_id"],
            "langgraph_thread_id": state.get("langgraph_thread_id", state["workflow_run_id"]),
            "chat_thread_id": state.get("chat_thread_id", ""),
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "message": "Approve this Aily workflow plan?",
        }
    )
    approved = isinstance(response, dict) and response.get("approved") is True
    plan = await deps.chat_store.set_workflow_plan_decision(
        state["workflow_plan_id"],
        approved=approved,
        decided_by=str(response.get("decided_by") or "user") if isinstance(response, dict) else "user",
        metadata={"decision_source": "business_planning_graph_resume"},
    )
    status = "running" if approved else "cancelled"
    await _set_status(
        deps,
        state,
        status=status,
        current_node="await_user_confirmation",
        metadata={
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "workflow_plan_status": plan["status"],
        },
    )
    await _emit(
        deps,
        "business_planning_workflow_plan_confirmed" if approved else "business_planning_workflow_plan_rejected",
        workflow_run_id=state.get("workflow_run_id", ""),
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
        approved=approved,
    )
    return _append_step(
        state,
        "await_user_confirmation",
        status=status,
        workflow_plan=plan,
        execute_iwi=approved
        and bool(isinstance(response, dict) and response.get("dispatch_iwi") is True),
        execute_research=approved
        and bool(
            state.get("research_required")
            or (isinstance(response, dict) and response.get("dispatch_research") is True)
        ),
        execute_business_plan=approved
        and bool(isinstance(response, dict) and response.get("dispatch_business_plan") is True),
        approvals={
            **state.get("approvals", {}),
            "workflow_plan": "approved" if approved else "rejected",
        },
    )


def route_after_confirmation(state: WorkflowState) -> str:
    if state.get("approvals", {}).get("workflow_plan") != "approved":
        return "complete_confirmed_plan"
    if state.get("execute_iwi"):
        return "run_iwi"
    if state.get("execute_research"):
        return "run_deep_research"
    if state.get("execute_business_plan"):
        return "run_business_plan"
    return "complete_confirmed_plan"


def route_after_iwi(state: WorkflowState) -> str:
    if state.get("status") == "failed":
        return "complete_confirmed_plan"
    if state.get("execute_research"):
        return "run_deep_research"
    if state.get("execute_business_plan"):
        return "run_business_plan"
    return "complete_confirmed_plan"


def route_after_research(state: WorkflowState) -> str:
    if state.get("status") == "failed":
        return "complete_confirmed_plan"
    if state.get("execute_business_plan"):
        return "run_business_plan"
    return "complete_confirmed_plan"


async def run_iwi(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    if deps.run_iwi is None:
        raise RuntimeError("BusinessPlanningGraph I/W/I runner is unavailable")
    node_ids = _selected_graph_node_ids(state)
    if not node_ids:
        error = "BusinessPlanningGraph requires graph-backed context before running I/W/I"
        await _set_status(deps, state, status="failed", current_node="run_iwi", last_error=error)
        raise RuntimeError(error)
    workflow_run_id = state["workflow_run_id"]
    await _set_status(
        deps,
        state,
        status="running",
        current_node="run_iwi",
        metadata={
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "node_ids": node_ids,
            "trigger": "business_planning_graph",
        },
    )
    await _emit(
        deps,
        "business_planning_iwi_started",
        workflow_run_id=workflow_run_id,
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
        node_ids=node_ids,
    )
    result = await deps.run_iwi(state.get("motive", ""), workflow_run_id, node_ids)
    failed = _failed_stage(result)
    summary = _iwi_result_summary(result)
    if failed is not None:
        error = str(getattr(failed, "error_message", "") or f"{_stage_name(getattr(failed, 'stage', ''))} failed")
        await _set_status(
            deps,
            state,
            status="failed",
            current_node=_stage_name(getattr(failed, "stage", "")) or "run_iwi",
            metadata={
                "workflow_plan_id": state.get("workflow_plan_id", ""),
                "node_ids": node_ids,
                "iwi_result": summary,
            },
            last_error=error,
        )
        await _emit(
            deps,
            "business_planning_iwi_failed",
            workflow_run_id=workflow_run_id,
            chat_thread_id=state.get("chat_thread_id", ""),
            workflow_plan_id=state.get("workflow_plan_id", ""),
            error=error,
            final_stage=summary.get("final_stage", ""),
        )
        return _append_step(
            state,
            "run_iwi",
            status="failed",
            node_ids=node_ids,
            iwi_result=summary,
            error=error,
        )

    await _set_status(
        deps,
        state,
        status="completed",
        current_node=summary.get("final_stage") or "run_iwi",
        metadata={
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "node_ids": node_ids,
            "iwi_result": summary,
            "final_stage": summary.get("final_stage", ""),
            "pipeline_id": summary.get("pipeline_id", ""),
            "stage_count": summary.get("stage_count", 0),
        },
    )
    await _emit(
        deps,
        "business_planning_iwi_completed",
        workflow_run_id=workflow_run_id,
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
        final_stage=summary.get("final_stage", ""),
        stage_count=summary.get("stage_count", 0),
        node_ids=node_ids,
    )
    return _append_step(
        state,
        "run_iwi",
        status="completed",
        node_ids=node_ids,
        iwi_result=summary,
        final_stage=summary.get("final_stage", ""),
        pipeline_id=summary.get("pipeline_id", ""),
    )


async def run_deep_research(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    if deps.run_research is None:
        raise RuntimeError("BusinessPlanningGraph research runner is unavailable")
    workflow_run_id = state["workflow_run_id"]
    await _set_status(
        deps,
        state,
        status="running",
        current_node="run_deep_research",
        metadata={
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "trigger": "business_planning_graph",
        },
    )
    await _emit(
        deps,
        "business_planning_research_started",
        workflow_run_id=workflow_run_id,
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
    )
    research_job = await deps.run_research(state)
    status = str(research_job.get("status") or "")
    if status != "completed":
        error = str(research_job.get("error") or f"Research ended with status {status}")
        await _set_status(
            deps,
            state,
            status="failed",
            current_node="run_deep_research",
            metadata={
                "workflow_plan_id": state.get("workflow_plan_id", ""),
                "research_id": research_job.get("research_id", ""),
                "research_status": status,
            },
            last_error=error,
        )
        await _emit(
            deps,
            "business_planning_research_failed",
            workflow_run_id=workflow_run_id,
            chat_thread_id=state.get("chat_thread_id", ""),
            workflow_plan_id=state.get("workflow_plan_id", ""),
            research_id=research_job.get("research_id", ""),
            error=error,
        )
        return _append_step(
            state,
            "run_deep_research",
            status="failed",
            research_ids=[str(research_job.get("research_id") or "")],
            research_job=research_job,
            error=error,
        )
    await _set_status(
        deps,
        state,
        status="running",
        current_node="run_deep_research",
        metadata={
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "research_id": research_job.get("research_id", ""),
            "research_status": status,
        },
    )
    await _emit(
        deps,
        "business_planning_research_completed",
        workflow_run_id=workflow_run_id,
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
        research_id=research_job.get("research_id", ""),
    )
    return _append_step(
        state,
        "run_deep_research",
        status="running",
        research_ids=[str(research_job.get("research_id") or "")],
        research_job=research_job,
    )


async def run_business_plan(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    if deps.run_business_plan is None:
        raise RuntimeError("BusinessPlanningGraph business-plan runner is unavailable")
    workflow_run_id = state["workflow_run_id"]
    await _set_status(
        deps,
        state,
        status="running",
        current_node="run_specialist_evaluations",
        metadata={"workflow_plan_id": state.get("workflow_plan_id", "")},
    )
    await _emit(
        deps,
        "business_planning_evaluations_started",
        workflow_run_id=workflow_run_id,
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
    )
    result = await deps.run_business_plan(state)
    evaluation_ids = [str(item.get("evaluation_id") or "") for item in result.get("evaluations", [])]
    business_plan_id = str((result.get("business_plan") or {}).get("business_plan_id") or "")
    await _set_status(
        deps,
        state,
        status="completed",
        current_node="business_plan_completed",
        metadata={
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "evaluation_ids": evaluation_ids,
            "business_plan_id": business_plan_id,
            "business_plan_obsidian_path": (result.get("business_plan") or {}).get("obsidian_path", ""),
        },
    )
    await _emit(
        deps,
        "business_planning_business_plan_completed",
        workflow_run_id=workflow_run_id,
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
        evaluation_ids=evaluation_ids,
        business_plan_id=business_plan_id,
    )
    return _append_step(
        state,
        "business_plan_completed",
        status="completed",
        evaluation_ids=evaluation_ids,
        business_plan_id=business_plan_id,
        business_plan=result.get("business_plan", {}),
        team_evaluations=result.get("evaluations", []),
    )


async def complete_confirmed_plan(state: WorkflowState, deps: BusinessPlanningDependencies) -> WorkflowState:
    if state.get("status") == "failed":
        return _append_step(state, "workflow_plan_failed", status="failed")
    if state.get("execute_business_plan") and state.get("business_plan"):
        return _append_step(state, "workflow_plan_business_plan_completed", status="completed")
    approved = state.get("approvals", {}).get("workflow_plan") == "approved"
    if not approved:
        await _set_status(deps, state, status="cancelled", current_node="workflow_plan_rejected")
        return _append_step(state, "workflow_plan_rejected", status="cancelled")
    current_node = "workflow_plan_confirmed"
    if state.get("execute_research") and state.get("research_job"):
        current_node = "research_completed"
    if state.get("execute_iwi") and state.get("iwi_result"):
        current_node = str(state.get("final_stage") or "iwi_completed")
    await _set_status(
        deps,
        state,
        status="completed",
        current_node=current_node,
        metadata={
            "chat_thread_id": state.get("chat_thread_id", ""),
            "workflow_plan_id": state.get("workflow_plan_id", ""),
            "research_ids": state.get("research_ids", []),
            "evaluation_ids": state.get("evaluation_ids", []),
            "business_plan_id": state.get("business_plan_id", ""),
            "final_stage": state.get("final_stage", ""),
            "pipeline_id": state.get("pipeline_id", ""),
        },
    )
    await _emit(
        deps,
        "business_planning_confirmation_completed",
        workflow_run_id=state.get("workflow_run_id", ""),
        chat_thread_id=state.get("chat_thread_id", ""),
        workflow_plan_id=state.get("workflow_plan_id", ""),
    )
    return _append_step(state, current_node, status="completed")


def build_business_planning_graph(
    checkpointer: Any | None = None,
    *,
    dependencies: BusinessPlanningDependencies,
) -> Any:
    async def receive_motive_node(state: WorkflowState) -> WorkflowState:
        return await receive_motive(state, dependencies)

    async def extract_topics_node(state: WorkflowState) -> WorkflowState:
        return await extract_topics(state, dependencies)

    async def search_knowledge_context_node(state: WorkflowState) -> WorkflowState:
        return await search_knowledge_context(state, dependencies)

    async def draft_workflow_plan_node(state: WorkflowState) -> WorkflowState:
        return await draft_workflow_plan(state, dependencies)

    async def await_user_confirmation_node(state: WorkflowState) -> WorkflowState:
        return await await_user_confirmation(state, dependencies)

    async def complete_confirmed_plan_node(state: WorkflowState) -> WorkflowState:
        return await complete_confirmed_plan(state, dependencies)

    async def run_iwi_node(state: WorkflowState) -> WorkflowState:
        return await run_iwi(state, dependencies)

    async def run_deep_research_node(state: WorkflowState) -> WorkflowState:
        return await run_deep_research(state, dependencies)

    async def run_business_plan_node(state: WorkflowState) -> WorkflowState:
        return await run_business_plan(state, dependencies)

    graph = StateGraph(WorkflowState)
    graph.add_node("receive_motive", receive_motive_node)
    graph.add_node("extract_topics", extract_topics_node)
    graph.add_node("search_knowledge_context", search_knowledge_context_node)
    graph.add_node("draft_workflow_plan", draft_workflow_plan_node)
    graph.add_node("await_user_confirmation", await_user_confirmation_node)
    graph.add_node("run_iwi", run_iwi_node)
    graph.add_node("run_deep_research", run_deep_research_node)
    graph.add_node("run_business_plan", run_business_plan_node)
    graph.add_node("complete_confirmed_plan", complete_confirmed_plan_node)
    graph.add_edge(START, "receive_motive")
    graph.add_edge("receive_motive", "extract_topics")
    graph.add_edge("extract_topics", "search_knowledge_context")
    graph.add_edge("search_knowledge_context", "draft_workflow_plan")
    graph.add_edge("draft_workflow_plan", "await_user_confirmation")
    graph.add_conditional_edges(
        "await_user_confirmation",
        route_after_confirmation,
        {
            "run_iwi": "run_iwi",
            "run_deep_research": "run_deep_research",
            "run_business_plan": "run_business_plan",
            "complete_confirmed_plan": "complete_confirmed_plan",
        },
    )
    graph.add_conditional_edges(
        "run_iwi",
        route_after_iwi,
        {
            "run_deep_research": "run_deep_research",
            "run_business_plan": "run_business_plan",
            "complete_confirmed_plan": "complete_confirmed_plan",
        },
    )
    graph.add_conditional_edges(
        "run_deep_research",
        route_after_research,
        {
            "run_business_plan": "run_business_plan",
            "complete_confirmed_plan": "complete_confirmed_plan",
        },
    )
    graph.add_edge("run_business_plan", "complete_confirmed_plan")
    graph.add_edge("complete_confirmed_plan", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
