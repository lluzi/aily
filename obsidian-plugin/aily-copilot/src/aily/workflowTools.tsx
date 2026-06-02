import { getCurrentProject } from "@/aiParams";
import {
  AilyBackendClient,
  AilyWorkflowOutput,
  AilyWorkflowRequest,
  AilyWorkflowResponse,
} from "@/aily/AilyBackendClient";
import { ConfirmModal } from "@/components/modals/ConfirmModal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { logError } from "@/logger";
import { createPluginRoot } from "@/utils/react/createPluginRoot";
import { App, Modal, Notice } from "obsidian";
import React, { useMemo, useState } from "react";
import { Root } from "react-dom/client";

export const END_TO_END_VALUE_WORKFLOW_OUTPUTS: AilyWorkflowOutput[] = [
  "insight",
  "wisdom",
  "impact",
  "research",
  "evaluation",
  "business_plan",
  "dossier",
];

interface EndToEndValueWorkflowModalContentProps {
  app: App;
  onCancel: () => void;
}

/** Opens the first Aily workflow tool surface for a user-confirmed value workflow. */
export function openEndToEndValueWorkflowModal(app: App): void {
  new EndToEndValueWorkflowModal(app).open();
}

/** Modal that gathers the required workflow topic/comment before planning execution. */
class EndToEndValueWorkflowModal extends Modal {
  private root: Root;

  constructor(app: App) {
    super(app);
    // https://docs.obsidian.md/Reference/TypeScript+API/Modal/setTitle
    // @ts-ignore
    this.setTitle("Run End-To-End Value Workflow");
  }

  onOpen(): void {
    const { contentEl } = this;
    this.root = createPluginRoot(contentEl, this.app);
    this.root.render(
      <EndToEndValueWorkflowModalContent app={this.app} onCancel={() => this.close()} />
    );
  }

  onClose(): void {
    this.root.unmount();
  }
}

/** Collects workflow inputs and submits the plan request before execution confirmation. */
function EndToEndValueWorkflowModalContent({
  app,
  onCancel,
}: EndToEndValueWorkflowModalContentProps) {
  const activeFile = app.workspace.getActiveFile();
  const currentProject = getCurrentProject();
  const [topic, setTopic] = useState(activeFile?.basename ?? "");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPlanning, setIsPlanning] = useState(false);

  const scopeText = useMemo(() => {
    const parts = [];
    if (currentProject?.id) parts.push(`Project: ${currentProject.name || currentProject.id}`);
    if (activeFile?.path) parts.push(`Note: ${activeFile.path}`);
    return parts.length > 0 ? parts.join("\n") : "Vault-wide";
  }, [activeFile?.path, currentProject?.id, currentProject?.name]);

  const handleSubmit = async () => {
    const trimmedTopic = topic.trim();
    const trimmedComment = comment.trim();
    if (!trimmedTopic || !trimmedComment) {
      setError("Topic and comment are required.");
      return;
    }

    setError(null);
    setIsPlanning(true);
    const request = buildEndToEndValueWorkflowRequest({
      topic: trimmedTopic,
      comment: trimmedComment,
      projectId: currentProject?.id,
      notePath: activeFile?.path,
    });

    try {
      const client = new AilyBackendClient();
      const plan = await client.planWorkflow(request);
      setIsPlanning(false);
      onCancel();
      openWorkflowRunConfirmation(app, request, plan);
    } catch (err) {
      logError("[Aily Workflow] Failed to plan end-to-end value workflow", err);
      setError(errorMessage(err));
      new Notice("Failed to plan Aily workflow. Check backend status and settings.");
      setIsPlanning(false);
    }
  };

  return (
    <div className="tw-flex tw-flex-col tw-gap-4">
      <div className="tw-flex tw-flex-col tw-gap-2">
        <label className="tw-text-sm tw-font-medium">Topic</label>
        <Input
          value={topic}
          placeholder="Topic to evaluate"
          onChange={(event) => setTopic(event.target.value)}
        />
      </div>
      <div className="tw-flex tw-flex-col tw-gap-2">
        <label className="tw-text-sm tw-font-medium">Comment</label>
        <Textarea
          value={comment}
          placeholder="Tell Aily what value workflow to run and what outcome you need."
          onChange={(event) => setComment(event.target.value)}
        />
      </div>
      <div className="tw-rounded-md tw-border tw-border-solid tw-border-border tw-p-3 tw-text-sm">
        <div className="tw-font-medium">Scope</div>
        <div className="tw-mt-1 tw-whitespace-pre-wrap tw-text-muted">{scopeText}</div>
      </div>
      {error && <div className="tw-text-sm tw-text-error">{error}</div>}
      <div className="tw-flex tw-justify-end tw-gap-2">
        <Button variant="secondary" onClick={onCancel} disabled={isPlanning}>
          Cancel
        </Button>
        <Button variant="default" onClick={() => void handleSubmit()} disabled={isPlanning}>
          {isPlanning ? "Planning..." : "Plan Workflow"}
        </Button>
      </div>
    </div>
  );
}

/** Builds the stable Copilot workflow request without inventing hidden scope. */
function buildEndToEndValueWorkflowRequest(params: {
  topic: string;
  comment: string;
  projectId?: string;
  notePath?: string;
}): AilyWorkflowRequest {
  return {
    tool: "run_end_to_end_value_workflow",
    topic: params.topic,
    comment: params.comment,
    scope: {
      project_id: params.projectId,
      source_ids: [],
      note_paths: params.notePath ? [params.notePath] : [],
      folders: [],
      tags: [],
    },
    requested_outputs: END_TO_END_VALUE_WORKFLOW_OUTPUTS,
  };
}

/** Shows the backend plan and requires explicit user approval before running. */
function openWorkflowRunConfirmation(
  app: App,
  request: AilyWorkflowRequest,
  plan: AilyWorkflowResponse
): void {
  const body = [
    `Topic: ${request.topic}`,
    `Workflow run ID: ${plan.workflow_run_id || "pending"}`,
    "",
    "Planned steps:",
    formatPlannedSteps(plan.planned_steps),
    "",
    `Provider usage: ${formatJsonSummary(plan.provider_usage)}`,
    "",
    "Run this workflow now?",
  ].join("\n");

  new ConfirmModal(
    app,
    async () => {
      try {
        const response = await new AilyBackendClient().runWorkflow(request);
        const workflowRunId = response.workflow_run_id || plan.workflow_run_id || "unknown";
        new Notice(`Aily workflow ${workflowRunId}: ${response.status || "submitted"}`);
      } catch (err) {
        logError("[Aily Workflow] Failed to run end-to-end value workflow", err);
        new Notice(`Failed to run Aily workflow: ${errorMessage(err)}`);
      }
    },
    body,
    "Confirm Workflow Run",
    "Run Workflow",
    "Cancel"
  ).open();
}

/** Formats planned steps from backend responses without assuming a final schema. */
function formatPlannedSteps(steps: unknown[] | undefined): string {
  if (!steps || steps.length === 0) {
    return "- No planned steps returned.";
  }
  return steps.map((step, index) => `- ${index + 1}. ${formatJsonSummary(step)}`).join("\n");
}

/** Provides compact object summaries for modal text. */
function formatJsonSummary(value: unknown): string {
  if (value === undefined || value === null) return "unavailable";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Extracts the most useful user-facing message from an unknown thrown value. */
function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
