import { AilyBackendClient, AilySourceResponse } from "@/aily/AilyBackendClient";
import { logError, logInfo } from "@/logger";
import { App, Modal, Notice, Plugin, Setting, TFile } from "obsidian";

const MIME_TYPES: Record<string, string> = {
  md: "text/markdown",
  txt: "text/plain",
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

/** Best-effort MIME type for a vault file extension. */
function mimeForExtension(extension: string): string {
  return MIME_TYPES[extension.toLowerCase()] || "application/octet-stream";
}

/** Human-readable outcome for a submitted source. */
function describeSource(response: AilySourceResponse): string {
  return response.duplicate ? "already known (deduplicated)" : "queued for processing";
}

/** Modal prompting the user for a single URL to ingest. */
class UrlPromptModal extends Modal {
  private value = "";

  constructor(
    app: App,
    private readonly onSubmit: (url: string) => void
  ) {
    super(app);
  }

  onOpen(): void {
    this.titleEl.setText("Ingest URL into Aily");
    const setting = new Setting(this.contentEl)
      .setName("URL")
      .addText((text) =>
        text.setPlaceholder("https://…").onChange((value) => {
          this.value = value.trim();
        })
      );
    setting.controlEl.querySelector("input")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        this.submit();
      }
    });
    new Setting(this.contentEl).addButton((button) =>
      button
        .setButtonText("Ingest")
        .setCta()
        .onClick(() => this.submit())
    );
  }

  private submit(): void {
    const url = this.value;
    this.close();
    if (url) {
      this.onSubmit(url);
    }
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

/** Upload a vault file to the Aily backend for DIKIWI ingestion. */
async function ingestFile(app: App, client: AilyBackendClient, file: TFile): Promise<void> {
  try {
    const data = await app.vault.readBinary(file);
    const response = await client.uploadSourceFile({
      filename: file.name,
      data,
      mimeType: mimeForExtension(file.extension),
    });
    logInfo("[Aily] Ingested file", { path: file.path, uploads: response.uploads });
    new Notice(`Aily: ingesting “${file.name}”`);
  } catch (error) {
    logError("[Aily] Failed to ingest file", { path: file.path, error });
    new Notice(`Aily: failed to ingest “${file.name}”`);
  }
}

/**
 * Register Aily capture surfaces: command-palette entries for ingesting the
 * active file, a URL, or the current editor selection, plus a file-explorer
 * right-click action. Every route converges on the same backend source store,
 * so the intake stays channel-agnostic.
 */
export function registerAilyCaptureCommands(
  plugin: Plugin,
  client: AilyBackendClient = new AilyBackendClient()
): void {
  const app = plugin.app;

  plugin.addCommand({
    id: "aily-ingest-active-file",
    name: "Aily: Ingest current file",
    checkCallback: (checking) => {
      const file = app.workspace.getActiveFile();
      if (!file) return false;
      if (!checking) {
        void ingestFile(app, client, file);
      }
      return true;
    },
  });

  plugin.addCommand({
    id: "aily-ingest-url",
    name: "Aily: Ingest URL…",
    callback: () => {
      new UrlPromptModal(app, (url) => {
        void (async () => {
          try {
            const response = await client.submitUrl(url);
            new Notice(`Aily: URL ${describeSource(response)}`);
          } catch (error) {
            logError("[Aily] Failed to ingest URL", { url, error });
            new Notice("Aily: failed to ingest URL");
          }
        })();
      }).open();
    },
  });

  plugin.addCommand({
    id: "aily-ingest-selection",
    name: "Aily: Ingest selection",
    editorCheckCallback: (checking, editor, ctx) => {
      const selection = editor.getSelection().trim();
      if (!selection) return false;
      if (!checking) {
        const title = ctx.file ? `Selection from ${ctx.file.basename}` : "Aily selection";
        void (async () => {
          try {
            const response = await client.submitText(title, selection);
            new Notice(`Aily: selection ${describeSource(response)}`);
          } catch (error) {
            logError("[Aily] Failed to ingest selection", { error });
            new Notice("Aily: failed to ingest selection");
          }
        })();
      }
      return true;
    },
  });

  plugin.registerEvent(
    app.workspace.on("file-menu", (menu, file) => {
      if (!(file instanceof TFile)) return;
      menu.addItem((item) =>
        item
          .setTitle("Process with Aily")
          .setIcon("sparkles")
          .onClick(() => void ingestFile(app, client, file))
      );
    })
  );

  logInfo("[Aily] Capture commands registered");
}
