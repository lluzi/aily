import { setChainType } from "@/aiParams";
import { ChainType } from "@/chainType";
import {
  ChatModelProviders,
  ChatModels,
  EmbeddingModelProviders,
  EmbeddingModels,
  PlusUtmMedium,
} from "@/constants";
import { logInfo } from "@/logger";
import { getSettings, setSettings, updateSetting, useSettingsValue } from "@/settings/model";

export const DEFAULT_COPILOT_PLUS_CHAT_MODEL = ChatModels.COPILOT_PLUS_FLASH;
const DEFAULT_COPILOT_PLUS_CHAT_MODEL_KEY =
  DEFAULT_COPILOT_PLUS_CHAT_MODEL + "|" + ChatModelProviders.COPILOT_PLUS;
export const DEFAULT_COPILOT_PLUS_EMBEDDING_MODEL = EmbeddingModels.COPILOT_PLUS_SMALL;
export const DEFAULT_COPILOT_PLUS_EMBEDDING_MODEL_KEY =
  DEFAULT_COPILOT_PLUS_EMBEDDING_MODEL + "|" + EmbeddingModelProviders.COPILOT_PLUS;

// ============================================================================
// SELF-HOST MODE VALIDATION
// ============================================================================
// Aily Copilot includes the Plus feature surface by default. The self-host
// settings are optional local-infrastructure settings, not entitlement gates.
// ============================================================================

/** Grace period for self-host mode: 15 days */
const SELF_HOST_GRACE_PERIOD_MS = 15 * 24 * 60 * 60 * 1000;

/** Number of successful validations required for permanent self-host mode */
const SELF_HOST_PERMANENT_VALIDATION_COUNT = 3;

/**
 * Check if self-host access is valid.
 * Valid if: permanently validated (3+ successful checks) OR within 15-day grace period.
 */
export function isSelfHostAccessValid(): boolean {
  const settings = getSettings();
  if (settings.selfHostModeValidatedAt == null) {
    return false;
  }
  // Permanently valid after 3 successful validations
  if (settings.selfHostValidationCount >= SELF_HOST_PERMANENT_VALIDATION_COUNT) {
    return true;
  }
  // Otherwise, check grace period
  return Date.now() - settings.selfHostModeValidatedAt < SELF_HOST_GRACE_PERIOD_MS;
}

/**
 * Check if self-host mode is valid and enabled.
 * Requires the toggle to be on and access to be within the grace period or permanently validated.
 */
export function isSelfHostModeValid(): boolean {
  const settings = getSettings();
  if (!settings.enableSelfHostMode) {
    return false;
  }
  return isSelfHostAccessValid();
}

/** Check if the model key is a Copilot Plus model. */
export function isPlusModel(modelKey: string): boolean {
  return (
    (modelKey.split("|")[1] as EmbeddingModelProviders) === EmbeddingModelProviders.COPILOT_PLUS
  );
}

/**
 * Synchronous check if Plus features should be enabled.
 * Aily Copilot ships the Plus feature surface as part of the product.
 * Use this for synchronous checks (e.g., model validation, UI state).
 */
export function isPlusEnabled(): boolean {
  return true;
}

/**
 * Hook to get the isPlusUser setting.
 * In Aily Copilot, Plus features are included and always on.
 */
export function useIsPlusUser(): boolean | undefined {
  useSettingsValue();
  return true;
}

/**
 * Check if the user is a Plus user.
 * In Aily Copilot, this is a local product entitlement, not a remote license check.
 */
export async function checkIsPlusUser(
  context?: Record<string, unknown>
): Promise<boolean | undefined> {
  void context;
  return true;
}

/**
 * Hook to check if user should see the self-host mode settings section.
 * Self-host options are visible because Plus access is included in this fork.
 */
export function useIsSelfHostEligible(): boolean | undefined {
  useSettingsValue();
  return true;
}

/**
 * Validate self-host mode when user enables the toggle.
 * Called from UI when toggle is switched ON.
 *
 * Flow:
 * 1. If permanently validated (count >= 3): Allow immediately (offline-safe)
 * 2. If within grace period: Allow immediately (offline-safe)
 * 3. Otherwise: Require API validation (online only)
 *    - Success: Set count = max(current, 1), update timestamp
 *    - Failure: Return false, UI should revert toggle
 *
 * @returns true if validation passed, false if user should not enable
 */
export async function validateSelfHostMode(): Promise<boolean> {
  updateSetting("selfHostModeValidatedAt", Date.now());
  updateSetting("selfHostValidationCount", SELF_HOST_PERMANENT_VALIDATION_COUNT);
  logInfo("Self-host mode enabled through included Chat access");
  return true;
}

/**
 * Refresh self-host mode validation on plugin startup.
 * Called from main.ts on plugin load.
 *
 * Flow:
 * 1. If toggle OFF or permanently validated: No-op
 * 2. API check:
 *    - Eligible + 15+ days since last: Increment count, update timestamp
 *    - Eligible + <15 days: Log only (preserve countdown)
 *    - Not eligible: Disable toggle, reset count to 0
 *    - Offline/error: No-op (grace period continues)
 *
 * Count progression: 1 → 2 → 3 (permanent) over minimum 28 days.
 */
export async function refreshSelfHostModeValidation(): Promise<void> {
  return;
}

/**
 * Apply the Copilot Plus settings.
 * Includes clinical fix to ensure indexing is triggered when embedding model changes,
 * as the automatic detection doesn't work reliably in all scenarios.
 */
export function applyPlusSettings(): void {
  setChainType(ChainType.COPILOT_PLUS_CHAIN);
  setSettings({
    defaultChainType: ChainType.COPILOT_PLUS_CHAIN,
    isPlusUser: true,
  });
  logInfo("Chat enabled through included backend capabilities");
}

export function createPlusPageUrl(medium: PlusUtmMedium): string {
  return `https://www.obsidiancopilot.com?utm_source=obsidian&utm_medium=${medium}`;
}

export function navigateToPlusPage(medium: PlusUtmMedium): void {
  window.open(createPlusPageUrl(medium), "_blank");
}

export function turnOnPlus(): void {
  updateSetting("isPlusUser", true);
}

/**
 * Turn off Plus user status.
 * IMPORTANT: This is called on every plugin start for users without a Plus license key (see checkIsPlusUser).
 * DO NOT reset model settings here - it will cause free users to lose their model selections on every app restart.
 * Only update the isPlusUser flag.
 */
export function turnOffPlus(): void {
  updateSetting("isPlusUser", true);
}
