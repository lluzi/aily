# Aily — Gap Analysis: Current Build vs. Real Product

_Audit date: 2026-06-04. Branch: `refocus/knowledge-refinery`._

## ✅ Resolution (closed 2026-06-14)

All blockers, majors, and P2 polish below have been closed and verified
(55 passing tests + an integrated boot). Highlights:

| Gap | Status | How |
|-----|--------|-----|
| B1 no service | ✅ | `scripts/install_service.sh` + launchd template + `DEPLOY.md` |
| B2 cloud-drop not configurable | ✅ | `INBOX_PATH` override (verified honored at boot) |
| B3 no control surface | ✅ | engine-served mobile control page `/api/copilot/control` |
| B4 decks vanish silently | ✅ | image-only/empty PDFs → `completed_empty` with reason (vision still needs a Kimi key) |
| M1 silent empty success | ✅ | `completed_empty` status + reason in SourceStatus + status note |
| M2 no heartbeat/feedback | ✅ | `99-System/Aily Status.md` (alive / what's new / needs-you) |
| M3 no backup | ✅ | daily snapshot to `~/.aily/backups/` (7-day retention) |
| M4 phantom `07-Proposal` | ✅ | Residual quarantined (no longer runs) |
| M5 plugin dead-end | ✅ | removed the reserved_not_invoked workflow modal |
| M6 garbage notes | ✅ | no fallback placeholder notes; confidence/placeholder filter |
| M7 large-doc truncation | ✅ | size-aware per-source budget (bounded by hard cap) |
| M8 stale docs | ✅ | README/AGENTS/CURRENT_STATE rewritten; planning sediment → `docs/history/` |
| M9 cold-start recovery | ✅ | stale-lock requeue at startup |
| cost runaway | ✅ | soft daily call cap (`llm_daily_max_calls`); detection is LLM-free |
| auth errors / WAL / logs / boilerplate / config prune | ✅ | 401/403 fail-fast; candidates WAL; rotating logs; de-cluttered Data notes; orphan fields removed |

The analysis below is retained as the original audit.

## Target being measured against

A product you **live in**, not a dev build:

> An always-on **Mac Mini home server** runs the Aily engine 24/7. You drop files
> and links into a **cloud-synced folder** from any device. The Mini digests them
> into your Obsidian vault. You **read** the refined vault via **Obsidian Publish**,
> and approve deeper synthesis. No terminal, ever.

## TL;DR

**The engine is real; the product is not yet inhabitable.** Every gap below is
*delivery, operability, trust, or feedback* — not core capability. The one-line
gap: **Aily can think, but it can't yet run unattended, can't be fed from your
phone, and can't tell you it's alive or that it worked.**

Rough estimate: backend capability ≈ 80% of v1; the *product* ≈ 50%, because the
missing half is the half a human touches. ~1 focused week to inhabitable.

## What is genuinely DONE (so this is balanced)

- Single-folder intake → extract → **D→I→K** notes, validated end-to-end with a real LLM.
- **detect → recommend → approve → generate** value loop, validated (real Insight/Wisdom/Impact).
- Source dedup (SHA256), durable SQLite stores (WAL on all but one), stale-lock requeue, per-source LLM budget (30 calls), retry/backoff, circuit breaker.
- Provider routing with single-provider fallback (fixed this session), `/ready` preconditions, 40 tests, boots clean.
- **Text PDF extraction works** (298K chars from a 1.36MB book in 10s).

---

## BLOCKERS — can't use it as the intended product at all

| # | Gap | Evidence | Why it blocks |
|---|-----|----------|---------------|
| B1 | **No working unattended service.** The only daemon (`scripts/com.aily.chaos.plist`) hardcodes `/Users/luzi`, launches the **removed** `run_chaos_daemon.py` (not `uvicorn aily.main:app`), and has no real restart story. README has no deploy section. | `scripts/com.aily.chaos.plist:11`; `README.md` (dev-only) | The engine dies with the terminal. There is no way to "have a Mac Mini standing by." |
| B2 | **Cloud-drop capture can't be configured.** `resolved_inbox_path` always prefers `<vault>/00-Chaos/_inbox` and ignores `INBOX_PATH` whenever a vault is set. | `aily/config.py` `resolved_inbox_path` | The whole "drop into a Dropbox/iCloud folder the Mini watches" model is unreachable; capture is locked to a folder inside the vault. |
| B3 | **No human surface for the value loop.** Candidate approve/dismiss is API-only; no Obsidian view; unreachable from a phone or the read-only Publish site. | plugin `src/aily/` has no candidate UI; `aily/copilot/router.py` candidates endpoints | The differentiated feature (approve synthesis) is curl-only. Nobody curls their second brain. |
| B4 | **Image-heavy PDFs (decks) silently produce nothing.** pdfplumber extracts little from image slides → DataAgent quality gate rejects → source marked `completed` with 0 notes. No vision fallback (DeepSeek is text-only; vision needs a Kimi key). | PDF test (text ✓, deck needs vision); `aily/dikiwi/agents/data_agent.py:50`; routing vision exemption | Decks/scanned papers are a huge share of real input and they vanish without a trace. |

---

## MAJOR — usable but untrustworthy, fragile, or invisible

| # | Gap | Evidence | Impact |
|---|-----|----------|--------|
| M1 | **Silent empty success.** A source that produced zero notes (quality gate, empty extraction) still reports `completed`; the reason is buried in pipeline metadata. | `data_agent.py:50` returns `success=True, items_output=0`; `main.py` sets `completed` | Trust killer — user can't distinguish "nothing useful" from "broken." Need `completed_empty`/`needs_review` + reason in SourceStatus. |
| M2 | **No feedback / "what's new" / heartbeat.** Nothing is written into the vault to say the engine is alive or what it just produced. `/health` `/ready` are HTTP-only. | no vault status writer found | On Publish (read-only) the user has no way to know it's running or that a drop worked. "Is my brain alive?" is unanswerable without a terminal. |
| M3 | **No backup of `~/.aily` derived state** (graph, candidates, source store). `aily/security/backup.py` exists but is only callable via a manual API action — never scheduled. | `aily/security/backup.py`; only wired to `/api/control` | DB corruption / disk loss / fat-finger = multi-day knowledge work gone. (The cloud drop folder backs up *originals* only.) |
| M4 | **`07-Proposal` folder mismatch.** ResidualAgent writes post-IMPACT reports to `07-Proposal`, which is **not** in the V1 vault layout (only a legacy-compat alias); it `mkdir`s a folder outside the contract. | `aily/dikiwi/agents/residual_agent.py:282`; `aily/writer/vault_layout.py` | Triggered synthesis pollutes the vault with an off-layout folder; Publish/structure contract breaks. |
| M5 | **Plugin dead-end.** The old "Run End-to-End Value Workflow" modal is still wired into chat controls + command palette, but the backend returns `reserved_not_invoked`. | `obsidian-plugin/.../aily/workflowTools.tsx`; `ChatControls.tsx`; `aily/copilot/workflows.py:281-284` | A visible feature that does nothing — reads as "the product is broken" on first contact. |
| M6 | **No confidence floor on note creation.** Low/zero-confidence extractions become permanent notes (observed: `confidence: 0%` notes written about "extraction failed" placeholder text). | `aily/writer/dikiwi_obsidian.py` note writers (no gate) | Garbage accreted as permanent vault notes degrades the thing that *is* the product. |
| M7 | **Large docs exceed the per-source call budget.** A 298K-char book vastly exceeds 30 LLM calls/source, so it processes partially with no "incomplete" signal. | PDF test (298,778 chars); `dikiwi_max_llm_calls_per_source=30` | Big PDFs silently yield partial knowledge; no size-aware budgeting or "truncated" status. |
| M8 | **Docs misrepresent the product.** README still mentions Feishu; `docs/` leads with V1/V2 planning specs; `progress.md`/`task_plan.md`/`findings.md` describe the removed world. | `README.md`; `docs/ARCHITECTURE_AND_VISION.md`, `AILY_CHAOS_ARCHITECTURE.md`; root planning files | Misleads you and any future collaborator/agent; Step 1 of the plan (lock the definition) was never executed. |
| M9 | **No cold-start stale-lock requeue.** Jobs stuck `running` after a crash are recovered only once the worker loop hits the 30-min stale timeout, not at startup. | requeue called in worker loop, not lifespan | After a crash/reboot, in-flight sources stall for up to 30 min before retry. |

---

## MINOR / polish

- `synthesis_candidates.db` doesn't enable WAL (all other stores do). — `aily/synthesis/candidates.py:initialize`
- Every Data note carries identical boilerplate ("## Data Characteristics", "## Why This Matters") → vault clutter. — `aily/writer/dikiwi_obsidian.py`
- No log rotation; logs not visible from the vault. — unbounded growth on a 24/7 box.
- 401/403/429 auth-class LLM errors aren't distinguished or surfaced (source just fails). — `aily/main.py` retry markers.
- Orphaned Feishu/voice/digest config fields linger in `config.py`.

---

## Correction on a common misread: cost is NOT a runaway risk

The detection loop is **LLM-free** (pure graph queries; `subgraphs_to_candidates`
is a pure function) and generation runs **only on explicit approval**. So there is
**no unattended spend** — the engine doesn't burn tokens while you sleep. Real cost
exposure is bounded: ingestion is capped at 30 calls/source; the only un-capped
dimension is *aggregate* spend if you drop a very large batch at once (N sources ×
30). A simple aggregate/daily ceiling would close it, but this is MAJOR-mild, not a
blocker.

---

## Critical path to "inhabitable" (ordered)

**P0 — move-in (≈2 days): you can run it on the Mini and feed it from your phone.**
1. Real **launchd service** → `uvicorn aily.main:app`, parameterized path, KeepAlive, log file + rotation; delete the stale chaos plist. + `DEPLOY.md`. (B1)
2. **`INBOX_PATH` override** so the watcher can point at a cloud-synced drop folder even when a vault is set; test. (B2)
3. **Heartbeat + "recently refined" notes** written to `99-System` so "alive?" and "what's new?" are answerable on Publish. (M2)
4. **Honest statuses** — `completed_empty` / `needs_review` + reason surfaced via SourceStatus + a vault note. (M1)

**P1 — trust & control (≈3 days): you'd actually rely on it.**
5. **Candidate approval reachable from a phone** — an engine-served mini control page (Publish is read-only) or a plugin panel. (B3)
6. **Confidence floor** on note creation + fix the **`07-Proposal`** folder to a layout folder. (M6, M4)
7. **Scheduled `~/.aily` backup** + cold-start requeue + candidates WAL. (M3, M9, minor)
8. **Decks/scanned PDFs**: either wire a vision provider (needs a Kimi key) or make image-only PDFs **fail loudly**, not silently. (B4)
9. **Docs truth pass** (README/AGENTS/CURRENT_STATE; archive `progress/task_plan/findings` + V1/V2 specs) and **remove the dead workflow modal**. (M8, M5)

**P2 — durability/polish (≈2 days):** size-aware budgeting for large docs (M7), aggregate spend cap, log visibility, boilerplate trim, auth-error alerting, config prune.

**≈1 week of focused work to a product you use every day.**

---

## Appendix — the single sharpest sentence per layer

- **Operability:** the only daemon is hardcoded to another user and launches a deleted script.
- **Capture:** you cannot point it at the cloud folder you intend to drop into.
- **Trust:** a source can say "completed" while nothing happened, and nothing tells you why.
- **Control:** the approve step that gates all the valuable output is curl-only.
- **Inputs:** decks — a primary real-world input — disappear silently without vision.
