import { ChainType } from "@/chainType";
import {
  AilyBackendClient,
  type AilyConfigResponse,
  type AilyConfigUpdateRequest,
} from "@/aily/AilyBackendClient";
import { Button } from "@/components/ui/button";
import { HelpTooltip } from "@/components/ui/help-tooltip";
import { Input } from "@/components/ui/input";
import { getModelDisplayWithIcons } from "@/components/ui/model-display";
import { SettingItem } from "@/components/ui/setting-item";
import { DEFAULT_OPEN_AREA, SEND_SHORTCUT } from "@/constants";
import { useApp } from "@/context";
import { useTab } from "@/contexts/TabContext";
import { cn } from "@/lib/utils";
import { getModelKeyFromModel, updateSetting, useSettingsValue } from "@/settings/model";
import { PlusSettings } from "@/settings/v2/components/PlusSettings";
import { checkModelApiKey, formatDateTime } from "@/utils";
import { isSortStrategy } from "@/utils/recentUsageManager";
import { Key, Loader2 } from "lucide-react";
import { Notice } from "obsidian";
import React, { useEffect, useState } from "react";
import { ApiKeyDialog } from "./ApiKeyDialog";

const defaultModeOptions = [
  { label: "Chat", value: ChainType.COPILOT_PLUS_CHAIN },
  { label: "DIKIWI", value: ChainType.VAULT_QA_CHAIN },
  { label: "Projects", value: ChainType.PROJECT_CHAIN },
];

type AilyProvider = "kimi" | "deepseek";
type TavilySearchDepth = "basic" | "advanced";

interface AilyConfigDraft {
  llmProvider: AilyProvider;
  copilotChatProvider: AilyProvider;
  copilotDossierProvider: AilyProvider;
  kimiModel: string;
  kimiVisionModel: string;
  deepseekModel: string;
  tavilySearchDepth: TavilySearchDepth;
  timeoutSeconds: string;
  maxRetries: string;
  maxConcurrency: string;
  minIntervalSeconds: string;
}

const defaultAilyConfigDraft: AilyConfigDraft = {
  llmProvider: "kimi",
  copilotChatProvider: "deepseek",
  copilotDossierProvider: "deepseek",
  kimiModel: "kimi-k2.6",
  kimiVisionModel: "kimi-k2.6",
  deepseekModel: "deepseek-v4-pro",
  tavilySearchDepth: "basic",
  timeoutSeconds: "120",
  maxRetries: "2",
  maxConcurrency: "1",
  minIntervalSeconds: "6",
};

export const BasicSettings: React.FC = () => {
  const app = useApp();
  const settings = useSettingsValue();
  const { setSelectedTab } = useTab();
  const [isChecking, setIsChecking] = useState(false);
  const [isCheckingAily, setIsCheckingAily] = useState(false);
  const [isLoadingAilyConfig, setIsLoadingAilyConfig] = useState(false);
  const [isSavingAilyConfig, setIsSavingAilyConfig] = useState(false);
  const [ailyConfig, setAilyConfig] = useState<AilyConfigResponse | null>(null);
  const [ailyConfigDraft, setAilyConfigDraft] =
    useState<AilyConfigDraft>(defaultAilyConfigDraft);
  const [kimiApiKeyDraft, setKimiApiKeyDraft] = useState("");
  const [deepseekApiKeyDraft, setDeepseekApiKeyDraft] = useState("");
  const [tavilyApiKeyDraft, setTavilyApiKeyDraft] = useState("");
  const [conversationNoteName, setConversationNoteName] = useState(
    settings.defaultConversationNoteName || "{$date}_{$time}__{$topic}"
  );

  useEffect(() => {
    void loadAilyConfig({ silent: true });
  }, [settings.ailyApiBaseUrl, settings.ailyApiToken]);

  const applyCustomNoteFormat = () => {
    setIsChecking(true);

    try {
      // Check required variables
      const format = conversationNoteName || "{$date}_{$time}__{$topic}";
      const requiredVars = ["{$date}", "{$time}", "{$topic}"];
      const missingVars = requiredVars.filter((v) => !format.includes(v));

      if (missingVars.length > 0) {
        new Notice(`Error: Missing required variables: ${missingVars.join(", ")}`, 4000);
        return;
      }

      // Check illegal characters (excluding variable placeholders)
      const illegalChars = /[\\/:*?"<>|]/;
      const formatWithoutVars = format
        .replace(/\{\$date}/g, "")
        .replace(/\{\$time}/g, "")
        .replace(/\{\$topic}/g, "");

      if (illegalChars.test(formatWithoutVars)) {
        new Notice(`Error: Format contains illegal characters (\\/:*?"<>|)`, 4000);
        return;
      }

      // Generate example filename
      const { fileName: timestampFileName } = formatDateTime(new Date());
      const firstTenWords = "test topic name";

      // Create example filename
      const customFileName = format
        .replace("{$topic}", firstTenWords.slice(0, 100).replace(/\s+/g, "_"))
        .replace("{$date}", timestampFileName.split("_")[0])
        .replace("{$time}", timestampFileName.split("_")[1]);

      // Save settings
      updateSetting("defaultConversationNoteName", format);
      setConversationNoteName(format);
      new Notice(`Format applied successfully! Example: ${customFileName}`, 4000);
    } catch (error) {
      new Notice(
        `Error applying format: ${error instanceof Error ? error.message : String(error)}`,
        4000
      );
    } finally {
      setIsChecking(false);
    }
  };

  const checkAilyBackend = async () => {
    setIsCheckingAily(true);
    try {
      const status = await new AilyBackendClient().status();
      new Notice(`Aily backend connected: ${status.vault_path || status.status}`, 5000);
    } catch (error) {
      new Notice(
        `Aily backend unavailable: ${error instanceof Error ? error.message : String(error)}`,
        8000
      );
    } finally {
      setIsCheckingAily(false);
    }
  };

  const loadAilyConfig = async ({ silent = false }: { silent?: boolean } = {}) => {
    setIsLoadingAilyConfig(true);
    try {
      const config = await new AilyBackendClient().config();
      setAilyConfig(config);
      setAilyConfigDraft({
        llmProvider: config.llm_provider,
        copilotChatProvider: config.routes.copilot_chat.provider,
        copilotDossierProvider: config.routes.copilot_dossier.provider,
        kimiModel: config.kimi.model,
        kimiVisionModel: config.kimi.vision_model,
        deepseekModel: config.deepseek.model,
        tavilySearchDepth: config.tavily.search_depth,
        timeoutSeconds: String(config.runtime.timeout_seconds),
        maxRetries: String(config.runtime.max_retries),
        maxConcurrency: String(config.runtime.max_concurrency),
        minIntervalSeconds: String(config.runtime.min_interval_seconds),
      });
      if (!silent) new Notice("Aily configuration loaded", 3000);
    } catch (error) {
      if (!silent) {
        new Notice(
          `Could not load Aily configuration: ${
            error instanceof Error ? error.message : String(error)
          }`,
          8000
        );
      }
    } finally {
      setIsLoadingAilyConfig(false);
    }
  };

  const updateAilyConfigDraft = <K extends keyof AilyConfigDraft>(
    key: K,
    value: AilyConfigDraft[K]
  ) => {
    setAilyConfigDraft((draft) => ({ ...draft, [key]: value }));
  };

  const saveAilyConfig = async () => {
    const timeoutSeconds = Number(ailyConfigDraft.timeoutSeconds);
    const maxRetries = Number(ailyConfigDraft.maxRetries);
    const maxConcurrency = Number(ailyConfigDraft.maxConcurrency);
    const minIntervalSeconds = Number(ailyConfigDraft.minIntervalSeconds);

    if (
      !Number.isFinite(timeoutSeconds) ||
      !Number.isFinite(maxRetries) ||
      !Number.isFinite(maxConcurrency) ||
      !Number.isFinite(minIntervalSeconds)
    ) {
      new Notice("Aily runtime limits must be valid numbers", 5000);
      return;
    }

    const payload: AilyConfigUpdateRequest = {
      llm_provider: ailyConfigDraft.llmProvider,
      copilot_chat_provider: ailyConfigDraft.copilotChatProvider,
      copilot_dossier_provider: ailyConfigDraft.copilotDossierProvider,
      kimi_model: ailyConfigDraft.kimiModel.trim(),
      kimi_vision_model: ailyConfigDraft.kimiVisionModel.trim(),
      deepseek_model: ailyConfigDraft.deepseekModel.trim(),
      tavily_search_depth: ailyConfigDraft.tavilySearchDepth,
      llm_timeout_seconds: timeoutSeconds,
      llm_max_retries: maxRetries,
      llm_max_concurrency: maxConcurrency,
      llm_min_interval_seconds: minIntervalSeconds,
    };
    if (kimiApiKeyDraft.trim()) payload.kimi_api_key = kimiApiKeyDraft.trim();
    if (deepseekApiKeyDraft.trim()) payload.deepseek_api_key = deepseekApiKeyDraft.trim();
    if (tavilyApiKeyDraft.trim()) payload.tavily_api_key = tavilyApiKeyDraft.trim();

    setIsSavingAilyConfig(true);
    try {
      const config = await new AilyBackendClient().updateConfig(payload);
      setAilyConfig(config);
      setKimiApiKeyDraft("");
      setDeepseekApiKeyDraft("");
      setTavilyApiKeyDraft("");
      new Notice("Aily runtime configuration saved", 4000);
    } catch (error) {
      new Notice(
        `Could not save Aily configuration: ${
          error instanceof Error ? error.message : String(error)
        }`,
        8000
      );
    } finally {
      setIsSavingAilyConfig(false);
    }
  };

  const defaultModelActivated = !!settings.activeModels.find(
    (m) => m.enabled && getModelKeyFromModel(m) === settings.defaultModelKey
  );
  const enableActivatedModels = settings.activeModels
    .filter((m) => m.enabled)
    .map((model) => ({
      label: getModelDisplayWithIcons(model),
      value: getModelKeyFromModel(model),
    }));

  return (
    <div className="tw-space-y-4">
      <PlusSettings />

      <section>
        <div className="tw-mb-3 tw-text-xl tw-font-bold">Aily Backend</div>
        <div className="tw-space-y-4">
          <SettingItem
            type="text"
            title="Aily API Base URL"
            description="Local or hosted Aily backend used by Chat, DIKIWI, and Projects."
            value={settings.ailyApiBaseUrl}
            onChange={(value) => updateSetting("ailyApiBaseUrl", value)}
            placeholder="http://127.0.0.1:8000"
          />
          <SettingItem
            type="text"
            title="Aily API Token"
            description="Optional bearer token if the Aily backend requires authentication."
            value={settings.ailyApiToken}
            onChange={(value) => updateSetting("ailyApiToken", value)}
            placeholder="Leave empty for local development"
          />
          <SettingItem
            type="switch"
            title="Use Aily LLM Providers"
            description="When enabled, the backend routes answers through Aily's configured external LLM providers while keeping vault grounding."
            checked={settings.ailyUseLlm}
            onCheckedChange={(checked) => updateSetting("ailyUseLlm", checked)}
          />
          <SettingItem
            type="select"
            title="Default Aily Provider"
            description="Backend default provider for Aily workloads that do not have a more specific route."
            value={ailyConfigDraft.llmProvider}
            onChange={(value) => updateAilyConfigDraft("llmProvider", value as AilyProvider)}
            options={[
              { label: "Kimi", value: "kimi" },
              { label: "DeepSeek", value: "deepseek" },
            ]}
          />
          <SettingItem
            type="select"
            title="Copilot Chat Provider"
            description="Provider used by Chat for grounded vault conversations."
            value={ailyConfigDraft.copilotChatProvider}
            onChange={(value) =>
              updateAilyConfigDraft("copilotChatProvider", value as AilyProvider)
            }
            options={[
              { label: "DeepSeek", value: "deepseek" },
              { label: "Kimi", value: "kimi" },
            ]}
          />
          <SettingItem
            type="select"
            title="Dossier Provider"
            description="Provider used when Aily generates dossier drafts from the vault."
            value={ailyConfigDraft.copilotDossierProvider}
            onChange={(value) =>
              updateAilyConfigDraft("copilotDossierProvider", value as AilyProvider)
            }
            options={[
              { label: "DeepSeek", value: "deepseek" },
              { label: "Kimi", value: "kimi" },
            ]}
          />
          <SettingItem
            type="custom"
            title="Kimi"
            description={`Model settings. API key is ${
              ailyConfig?.kimi.api_key.configured
                ? `configured (${ailyConfig.kimi.api_key.preview})`
                : "not configured"
            }.`}
          >
            <div className="tw-grid tw-w-full tw-gap-2 sm:tw-w-[360px]">
              <Input
                type="text"
                placeholder="Kimi chat model"
                value={ailyConfigDraft.kimiModel}
                onChange={(event) => updateAilyConfigDraft("kimiModel", event.target.value)}
              />
              <Input
                type="text"
                placeholder="Kimi vision model"
                value={ailyConfigDraft.kimiVisionModel}
                onChange={(event) => updateAilyConfigDraft("kimiVisionModel", event.target.value)}
              />
              <Input
                type="password"
                placeholder="New Kimi API key; leave blank to keep existing"
                value={kimiApiKeyDraft}
                onChange={(event) => setKimiApiKeyDraft(event.target.value)}
              />
            </div>
          </SettingItem>
          <SettingItem
            type="custom"
            title="DeepSeek"
            description={`Model settings. API key is ${
              ailyConfig?.deepseek.api_key.configured
                ? `configured (${ailyConfig.deepseek.api_key.preview})`
                : "not configured"
            }.`}
          >
            <div className="tw-grid tw-w-full tw-gap-2 sm:tw-w-[360px]">
              <Input
                type="text"
                placeholder="DeepSeek chat model"
                value={ailyConfigDraft.deepseekModel}
                onChange={(event) => updateAilyConfigDraft("deepseekModel", event.target.value)}
              />
              <Input
                type="password"
                placeholder="New DeepSeek API key; leave blank to keep existing"
                value={deepseekApiKeyDraft}
                onChange={(event) => setDeepseekApiKeyDraft(event.target.value)}
              />
            </div>
          </SettingItem>
          <SettingItem
            type="custom"
            title="Tavily Search"
            description={`External web research for grounded dossiers and research packets. API key is ${
              ailyConfig?.tavily.api_key.configured
                ? `configured (${ailyConfig.tavily.api_key.preview})`
                : "not configured"
            }.`}
          >
            <div className="tw-grid tw-w-full tw-gap-2 sm:tw-w-[360px]">
              <select
                value={ailyConfigDraft.tavilySearchDepth}
                onChange={(event) =>
                  updateAilyConfigDraft("tavilySearchDepth", event.target.value as TavilySearchDepth)
                }
                className="tw-flex tw-h-9 tw-w-full tw-rounded-md tw-border tw-border-solid tw-border-border tw-bg-dropdown tw-px-3 tw-py-1 tw-text-sm"
              >
                <option value="basic">Basic search depth</option>
                <option value="advanced">Advanced search depth</option>
              </select>
              <Input
                type="password"
                placeholder="New Tavily API key; leave blank to keep existing"
                value={tavilyApiKeyDraft}
                onChange={(event) => setTavilyApiKeyDraft(event.target.value)}
              />
            </div>
          </SettingItem>
          <SettingItem
            type="custom"
            title="Runtime Limits"
            description="Provider timeout, retries, concurrency, and minimum request interval used by Aily LLM calls."
          >
            <div className="tw-grid tw-w-full tw-grid-cols-2 tw-gap-2 sm:tw-w-[360px]">
              <Input
                type="number"
                placeholder="Timeout seconds"
                value={ailyConfigDraft.timeoutSeconds}
                onChange={(event) => updateAilyConfigDraft("timeoutSeconds", event.target.value)}
              />
              <Input
                type="number"
                placeholder="Max retries"
                value={ailyConfigDraft.maxRetries}
                onChange={(event) => updateAilyConfigDraft("maxRetries", event.target.value)}
              />
              <Input
                type="number"
                placeholder="Max concurrency"
                value={ailyConfigDraft.maxConcurrency}
                onChange={(event) => updateAilyConfigDraft("maxConcurrency", event.target.value)}
              />
              <Input
                type="number"
                placeholder="Min interval seconds"
                value={ailyConfigDraft.minIntervalSeconds}
                onChange={(event) =>
                  updateAilyConfigDraft("minIntervalSeconds", event.target.value)
                }
              />
            </div>
          </SettingItem>
          <SettingItem
            type="custom"
            title="Aily Runtime Config"
            description="Load or save redacted backend runtime settings. Secret values are never read back into Obsidian."
          >
            <div className="tw-flex tw-flex-wrap tw-gap-2">
              <Button
                onClick={() => void loadAilyConfig()}
                disabled={isLoadingAilyConfig || isSavingAilyConfig}
                variant="secondary"
              >
                {isLoadingAilyConfig ? (
                  <>
                    <Loader2 className="tw-mr-2 tw-size-4 tw-animate-spin" />
                    Loading
                  </>
                ) : (
                  "Load Config"
                )}
              </Button>
              <Button
                onClick={() => void saveAilyConfig()}
                disabled={isSavingAilyConfig || isLoadingAilyConfig}
                variant="default"
              >
                {isSavingAilyConfig ? (
                  <>
                    <Loader2 className="tw-mr-2 tw-size-4 tw-animate-spin" />
                    Saving
                  </>
                ) : (
                  "Save Config"
                )}
              </Button>
            </div>
          </SettingItem>
          <SettingItem
            type="custom"
            title="Connection"
            description="Verify that Obsidian can reach the configured Aily backend."
          >
            <Button
              onClick={() => void checkAilyBackend()}
              disabled={isCheckingAily}
              variant="secondary"
            >
              {isCheckingAily ? (
                <>
                  <Loader2 className="tw-mr-2 tw-size-4 tw-animate-spin" />
                  Checking
                </>
              ) : (
                "Check Backend"
              )}
            </Button>
          </SettingItem>
        </div>
      </section>

      {/* General Section */}
      <section>
        <div className="tw-mb-3 tw-text-xl tw-font-bold">General</div>
        <div className="tw-space-y-4">
          <div className="tw-space-y-4">
            {/* API Key Section */}
            <SettingItem
              type="custom"
              title="API Keys"
              description={
                <div className="tw-flex tw-items-center tw-gap-1.5">
                  <span className="tw-leading-none">
                    Configure API keys for different AI providers
                  </span>
                  <HelpTooltip
                    content={
                      <div className="tw-flex tw-max-w-96 tw-flex-col tw-gap-2 tw-py-4">
                        <div className="tw-text-sm tw-font-medium tw-text-accent">
                          API key required for chat and DIKIWI features
                        </div>
                        <div className="tw-text-xs tw-text-muted">
                          To enable chat and DIKIWI functionality, please provide an API key from
                          your selected provider.
                        </div>
                      </div>
                    }
                  />
                </div>
              }
            >
              <Button
                onClick={() => {
                  new ApiKeyDialog(app, () => setSelectedTab("model")).open();
                }}
                variant="secondary"
                className="tw-flex tw-w-full tw-items-center tw-justify-center tw-gap-2 sm:tw-w-auto sm:tw-justify-start"
              >
                Set Keys
                <Key className="tw-size-4" />
              </Button>
            </SettingItem>
          </div>
          <SettingItem
            type="select"
            title="Default Chat Model"
            description={
              <div className="tw-flex tw-items-center tw-gap-1.5">
                <span className="tw-leading-none">Select the Chat model to use</span>
                <HelpTooltip
                  content={
                    <div className="tw-flex tw-max-w-96 tw-flex-col tw-gap-2 tw-py-4">
                      <div className="tw-text-sm tw-font-medium tw-text-accent">
                        Default model is DeepSeek V4 Pro
                      </div>
                      <div className="tw-text-xs tw-text-muted">
                        Set your DeepSeek API key in &apos;API keys&apos; to use this model, or
                        select a Kimi model if that is the configured provider for the task.
                      </div>
                    </div>
                  }
                />
              </div>
            }
            value={defaultModelActivated ? settings.defaultModelKey : "Select Model"}
            onChange={(value) => {
              const selectedModel = settings.activeModels.find(
                (m) => m.enabled && getModelKeyFromModel(m) === value
              );
              if (!selectedModel) return;

              const { hasApiKey, errorNotice } = checkModelApiKey(selectedModel, settings);
              if (!hasApiKey && errorNotice) {
                // Keep selection allowed; error will surface in chat on send
              }
              updateSetting("defaultModelKey", value);
            }}
            options={
              defaultModelActivated
                ? enableActivatedModels
                : [{ label: "Select Model", value: "Select Model" }, ...enableActivatedModels]
            }
            placeholder="Model"
          />

          {/* Basic Configuration Group */}
          <SettingItem
            type="select"
            title="Default Mode"
            description={
              <div className="tw-flex tw-items-center tw-gap-1.5">
                <span className="tw-leading-none">Select the default chat mode</span>
                <HelpTooltip
                  content={
                    <div className="tw-flex tw-max-w-96 tw-flex-col tw-gap-2">
                      <ul className="tw-pl-4 tw-text-sm tw-text-muted">
                        <li>
                          <strong>Chat:</strong> Regular chat mode for general conversations and
                          everyday tasks. This is the default mode and uses the included Aily
                          Copilot Plus capabilities.
                        </li>
                        <li>
                          <strong>DIKIWI:</strong> Use the Data, Information, and Knowledge
                          foundation workflow for vault-grounded processing and retrieval.
                        </li>
                        <li>
                          <strong>Projects:</strong> Focused execution mode for product concepts,
                          business plans, technical proposals, and custom documents.
                        </li>
                      </ul>
                    </div>
                  }
                />
              </div>
            }
            value={settings.defaultChainType}
            onChange={(value) => updateSetting("defaultChainType", value as ChainType)}
            options={defaultModeOptions}
          />

          <SettingItem
            type="select"
            title="Open Plugin In"
            description="Choose where to open the plugin"
            value={settings.defaultOpenArea}
            onChange={(value) => updateSetting("defaultOpenArea", value as DEFAULT_OPEN_AREA)}
            options={[
              { label: "Sidebar View", value: DEFAULT_OPEN_AREA.VIEW },
              { label: "Editor", value: DEFAULT_OPEN_AREA.EDITOR },
            ]}
          />

          <SettingItem
            type="select"
            title="Send Shortcut"
            description={
              <div className="tw-flex tw-items-center tw-gap-1.5">
                <span className="tw-leading-none">Choose keyboard shortcut to send messages</span>
                <HelpTooltip
                  content={
                    <div className="tw-flex tw-max-w-96 tw-flex-col tw-gap-2 tw-py-4">
                      <div className="tw-text-sm tw-font-medium tw-text-accent">
                        Shortcut not working?
                      </div>
                      <div className="tw-text-xs tw-text-muted">
                        If your selected shortcut doesn&#39;t work, check
                        <strong> Obsidian&#39;s Settings → Hotkeys</strong> to see if another
                        command is using the same key combination. <br />
                        You may need to remove or change the conflicting hotkey first.
                      </div>
                    </div>
                  }
                />
              </div>
            }
            value={settings.defaultSendShortcut}
            onChange={(value) => updateSetting("defaultSendShortcut", value as SEND_SHORTCUT)}
            options={[
              { label: "Enter", value: SEND_SHORTCUT.ENTER },
              { label: "Shift + Enter", value: SEND_SHORTCUT.SHIFT_ENTER },
            ]}
          />

          <SettingItem
            type="switch"
            title="Auto-Add Active Content to Context"
            description="Automatically add the active note or Web Viewer tab (Desktop only) to chat context when sending messages."
            checked={settings.autoAddActiveContentToContext}
            onCheckedChange={(checked) => {
              updateSetting("autoAddActiveContentToContext", checked);
            }}
          />

          <SettingItem
            type="switch"
            title="Auto-Add Selection to Context"
            description="Automatically add selected text from notes or Web Viewer (Desktop only) to chat context. Disable to use manual command instead."
            checked={settings.autoAddSelectionToContext}
            onCheckedChange={(checked) => {
              updateSetting("autoAddSelectionToContext", checked);
            }}
          />

          <SettingItem
            type="switch"
            title="Images in Markdown"
            description="Pass embedded images in markdown to the AI along with the text. Only works with multimodal models."
            checked={settings.passMarkdownImages}
            onCheckedChange={(checked) => {
              updateSetting("passMarkdownImages", checked);
            }}
          />

          <SettingItem
            type="switch"
            title="Suggested Prompts"
            description="Show suggested prompts in the chat view"
            checked={settings.showSuggestedPrompts}
            onCheckedChange={(checked) => updateSetting("showSuggestedPrompts", checked)}
          />

          <SettingItem
            type="switch"
            title="Relevant Notes"
            description="Show relevant notes in the chat view"
            checked={settings.showRelevantNotes}
            onCheckedChange={(checked) => updateSetting("showRelevantNotes", checked)}
          />
        </div>
      </section>

      {/* Saving Conversations Section */}
      <section>
        <div className="tw-mb-3 tw-text-xl tw-font-bold">Saving Conversations</div>
        <div className="tw-space-y-4">
          <SettingItem
            type="switch"
            title="Autosave Chat"
            description="Automatically saves the chat after every user message and AI response."
            checked={settings.autosaveChat}
            onCheckedChange={(checked) => updateSetting("autosaveChat", checked)}
          />

          <SettingItem
            type="switch"
            title="Generate AI Chat Title on Save"
            description="When enabled, uses an AI model to generate a concise title for saved chat notes. When disabled, uses the first 10 words of the first user message."
            checked={settings.generateAIChatTitleOnSave}
            onCheckedChange={(checked) => updateSetting("generateAIChatTitleOnSave", checked)}
          />

          <SettingItem
            type="text"
            title="Default Conversation Folder Name"
            description="The default folder name where chat conversations will be saved. Default is 'copilot/copilot-conversations'"
            value={settings.defaultSaveFolder}
            onChange={(value) => updateSetting("defaultSaveFolder", value)}
            placeholder="copilot/copilot-conversations"
          />

          <SettingItem
            type="text"
            title="Default Conversation Tag"
            description="The default tag to be used when saving a conversation. Default is 'ai-conversations'"
            value={settings.defaultConversationTag}
            onChange={(value) => updateSetting("defaultConversationTag", value)}
            placeholder="ai-conversations"
          />

          <SettingItem
            type="custom"
            title="Conversation Filename Template"
            description={
              <div className="tw-flex tw-items-start tw-gap-1.5 ">
                <span className="tw-leading-none">
                  Customize the format of saved conversation note names.
                </span>
                <HelpTooltip
                  content={
                    <div className="tw-flex tw-max-w-96 tw-flex-col tw-gap-2 tw-py-4">
                      <div className="tw-text-sm tw-font-medium tw-text-accent">
                        Note: All the following variables must be included in the template.
                      </div>
                      <div>
                        <div className="tw-text-sm tw-font-medium tw-text-muted">
                          Available variables:
                        </div>
                        <ul className="tw-pl-4 tw-text-sm tw-text-muted">
                          <li>
                            <strong>{"{$date}"}</strong>: Date in YYYYMMDD format
                          </li>
                          <li>
                            <strong>{"{$time}"}</strong>: Time in HHMMSS format
                          </li>
                          <li>
                            <strong>{"{$topic}"}</strong>: Chat conversation topic
                          </li>
                        </ul>
                        <i className="tw-mt-2 tw-text-sm tw-text-muted">
                          Example: {"{$date}_{$time}__{$topic}"} →
                          20250114_153232__polish_this_article_[[Readme]]
                        </i>
                      </div>
                    </div>
                  }
                />
              </div>
            }
          >
            <div className="tw-flex tw-w-[320px] tw-items-center tw-gap-1.5">
              <Input
                type="text"
                className={cn(
                  "tw-min-w-[80px] tw-grow tw-transition-all tw-duration-200",
                  isChecking ? "tw-w-[80px]" : "tw-w-[120px]"
                )}
                placeholder="{$date}_{$time}__{$topic}"
                value={conversationNoteName}
                onChange={(e) => setConversationNoteName(e.target.value)}
                disabled={isChecking}
              />

              <Button
                onClick={() => applyCustomNoteFormat()}
                disabled={isChecking}
                variant="secondary"
              >
                {isChecking ? (
                  <>
                    <Loader2 className="tw-mr-2 tw-size-4 tw-animate-spin" />
                    Apply
                  </>
                ) : (
                  "Apply"
                )}
              </Button>
            </div>
          </SettingItem>
        </div>
      </section>

      {/* Sorting Section */}
      <section>
        <div className="tw-mb-3 tw-text-xl tw-font-bold">Sorting</div>
        <div className="tw-space-y-4">
          <SettingItem
            type="select"
            title="Chat History Sort Strategy"
            description="Sort order for the chat history list"
            value={settings.chatHistorySortStrategy}
            onChange={(value) => {
              if (isSortStrategy(value)) {
                updateSetting("chatHistorySortStrategy", value);
              }
            }}
            options={[
              { label: "Recency", value: "recent" },
              { label: "Created", value: "created" },
              { label: "Alphabetical", value: "name" },
            ]}
          />

          <SettingItem
            type="select"
            title="Project List Sort Strategy"
            description="Sort order for the project list"
            value={settings.projectListSortStrategy}
            onChange={(value) => {
              if (isSortStrategy(value)) {
                updateSetting("projectListSortStrategy", value);
              }
            }}
            options={[
              { label: "Recency", value: "recent" },
              { label: "Created", value: "created" },
              { label: "Alphabetical", value: "name" },
            ]}
          />
        </div>
      </section>
    </div>
  );
};
