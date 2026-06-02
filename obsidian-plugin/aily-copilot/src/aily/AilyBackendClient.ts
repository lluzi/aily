import { getSettings } from "@/settings/model";
import { requestUrl } from "obsidian";

export interface AilyChatRequest {
  message: string;
  search_query: string;
  project_id?: string;
  limit?: number;
  chat_history?: Array<{ role: string; content: string }>;
  use_llm?: boolean;
}

export interface AilyCitation {
  id?: string;
  title?: string;
  path?: string;
  relative_path?: string;
  score?: number;
}

export interface AilyChatResponse {
  answer: string;
  grounding_status?: string;
  used_llm?: boolean;
  citations?: AilyCitation[];
  search?: {
    results?: Array<{
      title?: string;
      path?: string;
      relative_path?: string;
      score?: number;
    }>;
  };
  suggested_actions?: string[];
}

export interface AilyStatusResponse {
  status: string;
  vault_path?: string;
  features?: Record<string, boolean>;
}

export interface AilyUploadResponse {
  uploads?: Array<{
    upload_id?: string;
    source_id?: string;
    filename?: string;
    duplicate?: boolean;
    status?: string;
  }>;
  message?: string;
}

export type AilyWorkflowOutput =
  | "insight"
  | "wisdom"
  | "impact"
  | "research"
  | "evaluation"
  | "business_plan"
  | "dossier";

export interface AilyWorkflowScope {
  project_id?: string;
  source_ids?: string[];
  note_paths?: string[];
  folders?: string[];
  tags?: string[];
}

export interface AilyWorkflowRequest {
  tool: "run_end_to_end_value_workflow";
  topic: string;
  comment: string;
  scope?: AilyWorkflowScope;
  requested_outputs: AilyWorkflowOutput[];
}

export interface AilyWorkflowResponse {
  workflow_run_id?: string;
  status?: string;
  topic?: string;
  comment?: string;
  planned_steps?: unknown[];
  current_step?: string;
  evidence_summary?: Record<string, unknown>;
  provider_usage?: Record<string, unknown>;
  artifact_paths?: string[];
}

export interface AilyWorkflowEventsResponse {
  workflow_run_id?: string;
  events?: Array<Record<string, unknown>>;
}

export interface AilySecretStatus {
  configured: boolean;
  preview: string;
}

export interface AilyResolvedRoute {
  workload: string;
  provider: "kimi" | "deepseek";
  model: string;
  base_url: string;
  api_key_configured: boolean;
}

export interface AilyConfigResponse {
  llm_provider: "kimi" | "deepseek";
  llm_base_url: string;
  llm_model: string;
  kimi: {
    model: string;
    vision_model: string;
    api_key: AilySecretStatus;
  };
  deepseek: {
    model: string;
    api_key: AilySecretStatus;
  };
  tavily: {
    search_depth: "basic" | "advanced";
    api_key: AilySecretStatus;
  };
  runtime: {
    timeout_seconds: number;
    max_retries: number;
    max_concurrency: number;
    min_interval_seconds: number;
  };
  routes: {
    copilot_chat: AilyResolvedRoute;
    copilot_dossier: AilyResolvedRoute;
  };
  workload_routes_json: string;
  persistence: "runtime_only" | string;
}

export interface AilyConfigUpdateRequest {
  llm_provider?: "kimi" | "deepseek";
  copilot_chat_provider?: "kimi" | "deepseek";
  copilot_dossier_provider?: "kimi" | "deepseek";
  kimi_api_key?: string;
  kimi_model?: string;
  kimi_vision_model?: string;
  deepseek_api_key?: string;
  deepseek_model?: string;
  tavily_api_key?: string;
  tavily_search_depth?: "basic" | "advanced";
  llm_timeout_seconds?: number;
  llm_max_retries?: number;
  llm_max_concurrency?: number;
  llm_min_interval_seconds?: number;
}

export class AilyBackendClient {
  async status(): Promise<AilyStatusResponse> {
    return this.request<AilyStatusResponse>("/api/copilot/status", "GET");
  }

  async config(): Promise<AilyConfigResponse> {
    return this.request<AilyConfigResponse>("/api/copilot/config", "GET");
  }

  async updateConfig(payload: AilyConfigUpdateRequest): Promise<AilyConfigResponse> {
    return this.request<AilyConfigResponse>("/api/copilot/config", "POST", payload);
  }

  async chat(payload: AilyChatRequest): Promise<AilyChatResponse> {
    return this.request<AilyChatResponse>("/api/copilot/chat", "POST", payload);
  }

  async uploadSourceFile(params: {
    filename: string;
    data: ArrayBuffer;
    mimeType?: string;
  }): Promise<AilyUploadResponse> {
    const settings = getSettings();
    const boundary = `----aily-copilot-${Date.now().toString(16)}`;
    const body = buildMultipartFileBody({
      boundary,
      fieldName: "files",
      filename: params.filename,
      mimeType: params.mimeType || "application/octet-stream",
      data: params.data,
    });
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": `multipart/form-data; boundary=${boundary}`,
    };
    if (settings.ailyApiToken) {
      headers.Authorization = `Bearer ${settings.ailyApiToken}`;
    }

    const url = `${settings.ailyApiBaseUrl.replace(/\/$/, "")}/api/ui/uploads`;
    const response = await requestUrl({
      url,
      method: "POST",
      headers,
      body,
      throw: false,
    });
    const parsed = parseJson(response.text);
    if (response.status < 200 || response.status >= 300) {
      const detail =
        typeof parsed.detail === "string"
          ? parsed.detail
          : response.text || `HTTP ${response.status}`;
      throw new Error(`Aily API ${response.status}: ${detail}`);
    }
    return parsed as AilyUploadResponse;
  }

  /** Creates a backend workflow plan for user review before execution. */
  async planWorkflow(payload: AilyWorkflowRequest): Promise<AilyWorkflowResponse> {
    const response = await this.request<Record<string, unknown>>(
      "/api/copilot/workflows/plan",
      "POST",
      payload
    );
    return normalizeWorkflowResponse(response);
  }

  /** Starts a previously user-confirmed workflow request. */
  async runWorkflow(payload: AilyWorkflowRequest): Promise<AilyWorkflowResponse> {
    const response = await this.request<Record<string, unknown>>(
      "/api/copilot/workflows/run",
      "POST",
      payload
    );
    return normalizeWorkflowResponse(response);
  }

  /** Fetches the latest state for a workflow run. */
  async getWorkflow(workflowRunId: string): Promise<AilyWorkflowResponse> {
    const response = await this.request<Record<string, unknown>>(
      `/api/copilot/workflows/${encodeURIComponent(workflowRunId)}`,
      "GET"
    );
    return normalizeWorkflowResponse(response);
  }

  /** Fetches backend events for a workflow run. */
  async getWorkflowEvents(workflowRunId: string): Promise<AilyWorkflowEventsResponse> {
    const response = await this.request<Record<string, unknown>>(
      `/api/copilot/workflows/${encodeURIComponent(workflowRunId)}/events`,
      "GET"
    );
    const workflowRun = asRecord(response.workflow_run);
    return {
      ...(response as AilyWorkflowEventsResponse),
      workflow_run_id:
        stringValue(response.workflow_run_id) ??
        stringValue(workflowRun?.workflow_run_id) ??
        stringValue(workflowRun?.id),
      events: recordArrayValue(response.events),
    };
  }

  private async request<T>(path: string, method: "GET" | "POST", body?: unknown): Promise<T> {
    const settings = getSettings();
    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    if (method === "POST") {
      headers["Content-Type"] = "application/json";
    }
    if (settings.ailyApiToken) {
      headers.Authorization = `Bearer ${settings.ailyApiToken}`;
    }

    const url = `${settings.ailyApiBaseUrl.replace(/\/$/, "")}${path}`;
    const response = await requestUrl({
      url,
      method,
      headers,
      body: method === "POST" ? JSON.stringify(body ?? {}) : undefined,
      throw: false,
    });

    const parsed = parseJson(response.text);
    if (response.status < 200 || response.status >= 300) {
      const detail =
        typeof parsed.detail === "string"
          ? parsed.detail
          : response.text || `HTTP ${response.status}`;
      throw new Error(`Aily API ${response.status}: ${detail}`);
    }
    return parsed as T;
  }
}

function parseJson(text: string): Record<string, unknown> {
  if (!text.trim()) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { text };
  }
}

function normalizeWorkflowResponse(payload: Record<string, unknown>): AilyWorkflowResponse {
  const workflowRun = asRecord(payload.workflow_run);
  const plan = asRecord(payload.plan);
  return {
    ...(payload as AilyWorkflowResponse),
    workflow_run_id:
      stringValue(payload.workflow_run_id) ??
      stringValue(workflowRun?.workflow_run_id) ??
      stringValue(workflowRun?.id) ??
      stringValue(plan?.workflow_run_id),
    status:
      stringValue(payload.status) ?? stringValue(workflowRun?.status) ?? stringValue(plan?.status),
    topic:
      stringValue(payload.topic) ?? stringValue(workflowRun?.topic) ?? stringValue(plan?.topic),
    comment:
      stringValue(payload.comment) ??
      stringValue(workflowRun?.comment) ??
      stringValue(plan?.comment),
    planned_steps:
      arrayValue(payload.planned_steps) ??
      arrayValue(plan?.planned_steps) ??
      arrayValue(workflowRun?.planned_steps),
    current_step:
      stringValue(payload.current_step) ??
      stringValue(workflowRun?.current_step) ??
      stringValue(plan?.current_step),
    evidence_summary:
      asRecord(payload.evidence_summary) ??
      asRecord(plan?.evidence_summary) ??
      asRecord(workflowRun?.evidence_summary),
    provider_usage:
      asRecord(payload.provider_usage) ??
      asRecord(plan?.provider_usage) ??
      asRecord(workflowRun?.provider_usage),
    artifact_paths:
      stringArrayValue(payload.artifact_paths) ??
      stringArrayValue(workflowRun?.artifact_paths) ??
      stringArrayValue(plan?.artifact_paths),
  };
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function arrayValue(value: unknown): unknown[] | undefined {
  return Array.isArray(value) ? value : undefined;
}

function stringArrayValue(value: unknown): string[] | undefined {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : undefined;
}

function recordArrayValue(value: unknown): Array<Record<string, unknown>> | undefined {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => asRecord(item) !== undefined)
    : undefined;
}

function buildMultipartFileBody(params: {
  boundary: string;
  fieldName: string;
  filename: string;
  mimeType: string;
  data: ArrayBuffer;
}): ArrayBuffer {
  const encoder = new TextEncoder();
  const safeFilename = params.filename.replace(/"/g, "%22");
  const prefix = encoder.encode(
    `--${params.boundary}\r\n` +
      `Content-Disposition: form-data; name="${params.fieldName}"; filename="${safeFilename}"\r\n` +
      `Content-Type: ${params.mimeType}\r\n\r\n`
  );
  const suffix = encoder.encode(`\r\n--${params.boundary}--\r\n`);
  const fileBytes = new Uint8Array(params.data);
  const body = new Uint8Array(prefix.length + fileBytes.length + suffix.length);
  body.set(prefix, 0);
  body.set(fileBytes, prefix.length);
  body.set(suffix, prefix.length + fileBytes.length);
  return body.buffer;
}
