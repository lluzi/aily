import { AilyBackendClient } from "@/aily/AilyBackendClient";
import { AI_SENDER } from "@/constants";
import { getCurrentProject } from "@/aiParams";
import { getSettings } from "@/settings/model";
import { ChatMessage } from "@/types/message";
import { formatDateTime } from "@/utils";
import { BaseChainRunner } from "./BaseChainRunner";

function getLayerText(message: ChatMessage, layerId: string): string {
  return message.contextEnvelope?.layers.find((layer) => layer.id === layerId)?.text?.trim() ?? "";
}

function getUserVisibleMessage(message: ChatMessage): string {
  return (
    getLayerText(message, "L5_USER") ||
    String(message.originalMessage || message.message || "").trim()
  );
}

function getSearchQuery(message: ChatMessage): string {
  const l5 = getLayerText(message, "L5_USER");
  const l3 = getLayerText(message, "L3_TURN_CONTEXT");
  return [l5, l3].filter(Boolean).join("\n\n").slice(0, 12000);
}

export class AilyChainRunner extends BaseChainRunner {
  private readonly client = new AilyBackendClient();

  async run(
    userMessage: ChatMessage,
    abortController: AbortController,
    updateCurrentAiMessage: (message: string) => void,
    addMessage: (message: ChatMessage) => void,
    options: {
      debug?: boolean;
      ignoreSystemMessage?: boolean;
      updateLoading?: (loading: boolean) => void;
    }
  ): Promise<string> {
    options.updateLoading?.(true);
    updateCurrentAiMessage("Aily is reading the vault and reconciling evidence...");

    try {
      const currentProject = getCurrentProject();
      const settings = getSettings();
      const response = await this.client.chat({
        message: getUserVisibleMessage(userMessage),
        search_query: getSearchQuery(userMessage),
        project_id: currentProject?.id ?? "",
        limit: 10,
        chat_history: this.extractHistory(),
        use_llm: settings.ailyUseLlm,
      });

      if (abortController.signal.aborted) {
        updateCurrentAiMessage("");
        return "";
      }

      const answer = formatAnswer(response);
      const sources = normalizeSources(response);
      return await this.handleResponse(
        answer,
        userMessage,
        abortController,
        addMessage,
        updateCurrentAiMessage,
        sources
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      updateCurrentAiMessage("");
      addMessage({
        message: `Aily backend request failed: ${message}`,
        sender: AI_SENDER,
        isVisible: true,
        isErrorMessage: true,
        timestamp: formatDateTime(new Date()),
      });
      return "";
    } finally {
      options.updateLoading?.(false);
    }
  }

  private extractHistory(): Array<{ role: string; content: string }> {
    const messages = (
      this.chainManager.memoryManager.getMemory().chatHistory as {
        messages?: Array<{ _getType?: () => string; content?: unknown }>;
      }
    ).messages;

    if (!Array.isArray(messages)) return [];
    return messages.slice(-12).map((item) => ({
      role: item._getType?.() === "human" ? "user" : "assistant",
      content: String(item.content ?? ""),
    }));
  }
}

function formatAnswer(response: { answer?: string; grounding_status?: string; used_llm?: boolean }): string {
  const answer = response.answer?.trim() || "Aily returned an empty response.";
  const meta = [
    response.grounding_status ? `grounding: ${response.grounding_status}` : "",
    typeof response.used_llm === "boolean" ? `external LLM: ${response.used_llm ? "yes" : "no"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return meta ? `${answer}\n\n---\n${meta}` : answer;
}

function normalizeSources(response: {
  citations?: Array<{ title?: string; path?: string; relative_path?: string; score?: number }>;
  search?: {
    results?: Array<{ title?: string; path?: string; relative_path?: string; score?: number }>;
  };
}): { title: string; path: string; score: number }[] {
  const citations = response.citations && response.citations.length > 0
    ? response.citations
    : response.search?.results ?? [];

  return citations
    .map((item) => {
      const path = item.relative_path || item.path || "";
      return {
        title: item.title || path || "Vault evidence",
        path,
        score: Number(item.score ?? 0),
      };
    })
    .filter((item) => item.path);
}
