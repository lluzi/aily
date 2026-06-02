import { AilyBackendClient } from "@/aily/AilyBackendClient";
import { logError, logInfo } from "@/logger";
import { getSettings, subscribeToSettingsChange } from "@/settings/model";
import { App, EventRef, TAbstractFile, TFile } from "obsidian";

const PROCESS_DELAY_MS = 1500;

export class DikiwiAutoTriggerService {
  private createRef?: EventRef;
  private settingsUnsubscribe?: () => void;
  private enabled = false;
  private timers = new Map<string, number>();
  private inFlight = new Set<string>();

  constructor(
    private readonly app: App,
    private readonly client = new AilyBackendClient()
  ) {}

  initialize(): void {
    this.syncRegistration();
    this.settingsUnsubscribe = subscribeToSettingsChange((prev, next) => {
      if (
        prev.dikiwiAutoTrigger !== next.dikiwiAutoTrigger ||
        prev.dikiwiAutoTriggerFileExtensions !== next.dikiwiAutoTriggerFileExtensions ||
        prev.dikiwiAutoTriggerInclusions !== next.dikiwiAutoTriggerInclusions ||
        prev.dikiwiAutoTriggerExclusions !== next.dikiwiAutoTriggerExclusions
      ) {
        this.syncRegistration();
      }
    });
  }

  unload(): void {
    this.unregister();
    this.settingsUnsubscribe?.();
    this.settingsUnsubscribe = undefined;
    for (const timer of this.timers.values()) {
      window.clearTimeout(timer);
    }
    this.timers.clear();
    this.inFlight.clear();
  }

  private syncRegistration(): void {
    const shouldEnable = getSettings().dikiwiAutoTrigger;
    if (shouldEnable === this.enabled) return;

    if (shouldEnable) {
      this.createRef = this.app.vault.on("create", this.handleCreate);
      this.enabled = true;
      logInfo("[DIKIWI] Auto-trigger watcher enabled");
      return;
    }

    this.unregister();
  }

  private unregister(): void {
    if (this.createRef) {
      this.app.vault.offref(this.createRef);
      this.createRef = undefined;
    }
    if (this.enabled) {
      logInfo("[DIKIWI] Auto-trigger watcher disabled");
    }
    this.enabled = false;
  }

  private handleCreate = (file: TAbstractFile): void => {
    if (!(file instanceof TFile)) return;
    if (!this.shouldProcess(file)) return;

    const existingTimer = this.timers.get(file.path);
    if (existingTimer) {
      window.clearTimeout(existingTimer);
    }

    const timer = window.setTimeout(() => {
      this.timers.delete(file.path);
      void this.processFile(file);
    }, PROCESS_DELAY_MS);
    this.timers.set(file.path, timer);
  };

  private async processFile(file: TFile): Promise<void> {
    if (this.inFlight.has(file.path)) return;
    if (!this.shouldProcess(file)) return;

    this.inFlight.add(file.path);
    try {
      const data = await this.app.vault.readBinary(file);
      const response = await this.client.uploadSourceFile({
        filename: file.name,
        data,
        mimeType: getMimeType(file.extension),
      });
      logInfo("[DIKIWI] Auto-trigger submitted file", {
        path: file.path,
        uploads: response.uploads,
      });
    } catch (error) {
      logError("[DIKIWI] Auto-trigger failed to submit file", { path: file.path, error });
    } finally {
      this.inFlight.delete(file.path);
    }
  }

  private shouldProcess(file: TFile): boolean {
    const settings = getSettings();
    if (!settings.dikiwiAutoTrigger) return false;

    const extension = file.extension.toLowerCase();
    const allowedExtensions = parseList(settings.dikiwiAutoTriggerFileExtensions).map((item) =>
      item.replace(/^\./, "").toLowerCase()
    );
    if (allowedExtensions.length > 0 && !allowedExtensions.includes(extension)) {
      return false;
    }

    const path = file.path.replace(/^\/+/, "");
    const inclusions = parseList(settings.dikiwiAutoTriggerInclusions);
    if (inclusions.length > 0 && !matchesAnyPath(path, inclusions)) {
      return false;
    }

    const exclusions = parseList(settings.dikiwiAutoTriggerExclusions);
    return !matchesAnyPath(path, exclusions);
  }
}

function parseList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim().replace(/^\/+|\/+$/g, ""))
    .filter(Boolean);
}

function matchesAnyPath(path: string, patterns: string[]): boolean {
  return patterns.some((pattern) => path === pattern || path.startsWith(`${pattern}/`));
}

function getMimeType(extension: string): string {
  const extensionLower = extension.toLowerCase();
  const mimeTypes: Record<string, string> = {
    md: "text/markdown",
    txt: "text/plain",
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  };
  return mimeTypes[extensionLower] || "application/octet-stream";
}
