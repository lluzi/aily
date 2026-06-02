from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from typing_extensions import TypedDict

WorkflowKind = Literal["source_foundation", "triggered_iwi", "business_planning", "copilot_value_workflow", "smoke"]
WorkflowStatus = Literal[
    "queued",
    "running",
    "interrupted",
    "completed",
    "failed",
    "cancelled",
]


class WorkflowState(TypedDict, total=False):
    workflow_run_id: str
    langgraph_thread_id: str
    workflow_kind: WorkflowKind
    status: WorkflowStatus
    current_node: str
    steps: list[str]
    motive: str
    source_id: str
    job_id: str
    job_type: str
    upload_id: str
    batch_id: str
    filename: str
    content_type: str
    url: str
    canonical_document_id: str
    canonical_markdown_path: str
    canonical_markdown_sha256: str
    markdown: str
    source_type: str
    processing_method: str
    rain_type: str
    stream_type: str
    pipeline_id: str
    final_stage: str
    stage_count: int
    stage_results: list[dict[str, Any]]
    topic_ids: list[str]
    topics: list[dict[str, Any]]
    source_ids: list[str]
    chat_thread_id: str
    message_id: str
    topic_extraction_id: str
    workflow_plan_id: str
    workflow_plan: dict[str, Any]
    knowledge_context: list[dict[str, Any]]
    research_required: bool
    execute_iwi: bool
    execute_research: bool
    execute_business_plan: bool
    node_ids: list[str]
    iwi_result: dict[str, Any]
    research_job: dict[str, Any]
    second_opinion_ids: list[str]
    research_ids: list[str]
    evaluation_ids: list[str]
    team_evaluations: list[dict[str, Any]]
    business_plan_id: str
    business_plan: dict[str, Any]
    obsidian_document_ids: list[str]
    approvals: dict[str, Any]
    metadata: dict[str, Any]
    error: str


@dataclass(frozen=True)
class WorkflowRunSnapshot:
    workflow_run_id: str
    langgraph_thread_id: str
    workflow_kind: str
    status: str
    current_node: str
    input_summary: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    completed_at: str | None = None
    last_error: str | None = None
