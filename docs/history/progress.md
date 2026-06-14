<!--
Origin: Created by Codex lead agent on 2026-05-17.
Role: Planning/progress log only; not acceptance evidence for any gate.
-->

# Aily V1 Progress Log

## 2026-05-23 Aily V2.0 Development Start

Started V2.0 development from `docs/AILY_V2_0_REQUIREMENTS.md`.

Team assignments:

- Hegel: backend workflow orchestration worker. Owns
  `/api/copilot/workflows/*` and related `aily/copilot/` service code.
- Leibniz: Obsidian plugin workflow tool-surface worker. Owns plugin client
  methods and command affordances for comment-based workflow invocation.
- Codex lead: local planning, integration review, QA/audit checks, and final
  coordination. The lead will not manually create acceptance evidence.

Initial implementation target:

- `run_end_to_end_value_workflow`
- required user `topic` and `comment`
- mandatory local knowledge-base / Obsidian vault search as first step
- Tavily/deep-research metadata visible as supplemental evidence
- status/events endpoints for monitorability
- plugin command surface for user invocation

Planning note:

- The `planning-with-files` catchup helper failed under bare `python` because
  the project machine does not expose `python` on PATH. Continue to use
  `uv run python` for project commands, as already required by the tracker.

Implemented first V2.0 development slice:

- Added backend workflow service for `run_end_to_end_value_workflow`.
- Added `/api/copilot/workflows/tools`, `/workflows`, `/workflows/plan`,
  `/workflows/run`, `/workflows/run_end_to_end_value_workflow`,
  `/workflows/{workflow_run_id}`, and `/workflows/{workflow_run_id}/events`.
- Added `copilot_value_workflow` as a workflow kind.
- Added plugin backend-client methods for planning, running, reading status,
  and reading workflow events.
- Added Obsidian command `Run End-To-End Value Workflow`.
- Added a modal that requires topic and comment, requests a backend plan, and
  asks for explicit confirmation before execution.

Current limitation:

- This is a safe first slice. It performs local vault/Knowledge search and
  records evidence, provider visibility, events, and reserved downstream steps.
  Tavily, Insight, Wisdom, Impact, evaluation, business-plan generation, and
  dossier generation are not executed yet by this workflow service.

Verification:

```bash
uv run python -m compileall -q aily/copilot aily/orchestration/state.py aily/main.py
git diff --check -- aily/copilot/workflows.py aily/copilot/router.py aily/copilot/__init__.py aily/orchestration/state.py obsidian-plugin/aily-copilot/src/aily/AilyBackendClient.ts obsidian-plugin/aily-copilot/src/aily/workflowTools.tsx obsidian-plugin/aily-copilot/src/commands/index.ts obsidian-plugin/aily-copilot/src/constants.ts task_plan.md findings.md progress.md
npm run build
uv run python -m compileall -q aily scripts
npx prettier --check src/aily/AilyBackendClient.ts src/aily/workflowTools.tsx src/commands/index.ts src/constants.ts
npx jest src/settings/model.test.ts --runInBand
uv run python - <<'PY'
# temporary-vault ASGI smoke for /api/copilot/workflows/plan, /run, and /events
PY
```

Results:

- Python compile passed.
- Diff whitespace check passed.
- Obsidian plugin production build passed.
- Full `aily scripts` compile passed.
- Plugin formatting check passed.
- `src/settings/model.test.ts` passed: 33 tests.
- Temporary-vault ASGI smoke passed and confirmed top-level response fields,
  local evidence count, Tavily `not_called` status, and workflow events for
  local search and safe interruption.

## 2026-05-23 20-PDF Product Maturity Evaluation

Command started:

```bash
uv run python scripts/run_10pdf_end_to_end_panel_evidence.py \
  --pdf-count 20 \
  --seed 20260523 \
  --vault-path "/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily" \
  --research-model mini \
  --max-results 3
```

Run directory:

```text
/Users/luzi/.aily/runs/2026-05-23T15-18-57Z_20pdf_panel_end_to_end
```

Result:

- Product maturity evaluation failed.
- The runner selected 20 PDFs and began processing them against the visible
  iCloud Documents vault.
- The run was stopped after roughly 28 minutes because it had processed only 4
  sources into the runtime store, completed only 3 source-foundation workflows,
  and had not reached research, evaluation, business-plan, or dossier output.
- No final `manifest.json` exists because the run did not complete.

Observed runtime state at stop:

- selected PDFs: 20
- source records: 4
- canonical markdown packages: 4
- completed source-foundation workflows: 3
- running source-foundation workflows: 1
- graph nodes: 59
- graph edges: 104
- research jobs: 0
- team evaluations: 0
- business plans: 0
- LLM trace records: 27
- providers observed: Kimi and DeepSeek
- observed timeout issue: `LLM timeout (attempt 1)`, `attempt 2`,
  `attempt 3`, then `Batch information clustering failed`

Visible vault changes:

- `00-Chaos`: 4 new source-equivalent Markdown files from the attempted PDFs.
- `01-Data/2026-05-23`: 45 notes.
- `02-Information/2026-05-23`: 39 notes.
- `03-Knowledge/2026-05-23`: 42 notes.

Quality checks:

```bash
uv run python scripts/score_kiosk_markdown_quality.py \
  --vault-path "/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily"

uv run python scripts/score_obsidian_vault_quality.py \
  --vault-path "/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily"
```

Results:

- 00-Chaos source-equivalent Markdown quality passed for the 4 attempted PDFs.
  Each produced slide sections, slide screenshots, and present screenshot
  assets.
- Overall Obsidian vault quality failed:
  - overall score: 94.03
  - note pass rate: 0.889, below 0.95 threshold
  - unresolved links: 29, threshold is 0
  - additional failure: raw SHA/debug ID in plugin README scored by the vault
    quality scanner

Maturity judgment:

- Not product-grade for a 20-PDF workflow.
- The system can process real PDFs and produce good 00-Chaos source-equivalent
  Markdown for attempted files.
- It is not yet reliable or fast enough for 20-PDF unattended processing.
- It does not yet complete the full value path from foundation through research,
  evaluation, business plan, and dossier under 20-PDF load.

## 2026-05-23 Aily-Copilot Upstream Fork Replacement

- Replaced the stripped `aily-copilot` pane implementation with a full
  upstream-derived Obsidian Copilot source tree from
  `logancyang/obsidian-copilot` commit `bd8829f`.
- Preserved upstream Copilot's React chat surface, mode selector, context
  controls, chat history controls, settings system, project UI, and AGPL-3.0
  license.
- Added `ChainType.AILY_CHAIN`, `AilyChainRunner`, and `AilyBackendClient` so
  `Aily` is a first-class Copilot chat mode backed by `/api/copilot/chat`.
- Added Aily backend settings for base URL, optional bearer token, backend LLM
  routing, and connection testing.
- Installed the rebuilt plugin into
  `/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily/.obsidian/plugins/aily-copilot`.
- Runtime evidence: after restarting Obsidian with CDP, the upstream Copilot
  chat opened with the `aily` mode selected and successfully returned a
  grounded Aily backend response with vault citations. Screenshot:
  `/tmp/aily-copilot-upstream-fork-working.png`.

## 2026-05-17

Created the unattended development planning surface:

- `.omx/plans/aily-v1-unattended-development-plan.md`
- `task_plan.md`
- `findings.md`
- `progress.md`

Scope:

- Documentation and planning scaffolding only.
- No runtime behavior changed.
- No acceptance evidence claimed.

Verification:

- `python3 -m compileall -q aily scripts` passed.
- This planning edit has not introduced Python code.

Next recommended unattended goal:

M0: add or confirm basic test configuration, then record Tier 0 commands and
any future pytest/ruff/mypy baseline decisions.

## 2026-05-17 Strict Gate Update

Updated the plan and tracker to prohibit marking milestones complete from
self-selected smoke tests alone.

Observed authentic test corpus:

- `/Users/luzi/aily_chaos/pdf`
- 286 PDF files
- 538 MB total

Environment readiness:

- Use `uv run python`, not bare `python3`, for project tests.
- Kimi, DeepSeek, Tavily, and Obsidian REST API keys are configured.
- Obsidian REST API key is stored without the literal `Bearer ` prefix because
  the current writer adds that prefix in the HTTP header.
- MinerU commands are present.
- Docling, EasyOCR, Poppler command-line tools, and Tesseract are missing.
- `orchestrator_enabled=true`, `orchestrator_shadow_mode=false`,
  `inbox_watcher_enabled=true`, and `email_delivery_enabled=true` are configured
  for local test/evidence runs.
- `~/Aily/Inbox` and `~/.aily/runs` now exist.
- Obsidian REST service is not listening on `127.0.0.1:27123`, so REST-specific
  gate claims remain blocked until Obsidian and the Local REST API plugin are
  running.

Verification:

- `uv run python -m compileall -q aily scripts` passed.
- Added visible gate runbook: `docs/AILY_TEST_QUALITY_GATES.md`.

## 2026-05-17 Multi-Source Gate Update

Updated the gate rule so every gate must be judged from multiple evidence
sources:

- source file truth
- runtime command/log truth
- durable database/workflow/checkpoint truth
- Obsidian vault truth
- event/audit truth
- direct reviewer observation

The gate runbook now requires:

- `evidence-matrix.json`
- `obsidian-vault-review.json`
- `cross-source-reconciliation.json`

A gate can pass only when the vault review aligns with test results and the
other observed evidence sources.

## 2026-05-17 Evidence Origin And Anti-Forgery Rule

Updated the gate rule:

- The lead agent cannot manually create, edit, or repair evidence artifacts used
  to pass a gate.
- Every generated evidence file must include an origin header.
- Files created by Codex as planning artifacts must identify Codex as creator
  and must not be used as gate evidence.
- Evidence must come from the application, evidence runner, test harness,
  external service response, database query/export, or independent reviewer
  process.

## 2026-05-17 Full Goal Started

Created Goal:

```text
019e3551-1c1c-79e2-9f4d-34b4e12e8d18
```

Objective:

Complete full Aily V1 development across M0-M9 and Gate 0-Gate 6 with subagent
separation of duties, multi-source evidence, origin headers, and no lead-agent
manual modification of gate evidence.

Initial subagent team launched:

- Kepler: Intake specialist
- Nietzsche: Orchestration specialist
- Arendt: DIKIWI specialist
- Dirac: Vault/export specialist
- Laplace: Evidence harness specialist
- Harvey: Security/config auditor

Current milestone:

- M0 `ACTIVE`

Current scope:

- Discovery and M0/Gate 0 scoping.
- No acceptance evidence created.
- No runtime code edited yet.

## 2026-05-17 M0 Harness Implementation

Implemented M0 evidence-harness support:

- Added origin metadata to `EvidenceRun.write_json`, `write_text`, and JSONL
  evidence output.
- Added generated `obsidian-vault-review.json`, `evidence-matrix.json`,
  `cross-source-reconciliation.json`, and `artifact-index.json`.
- Added artifact hashing into the manifest.
- Added component-scoped acceptance claims so Gate 0 can be real readiness
  evidence without pretending LLM or GraphDB paths were exercised.
- Updated run registry reads to unwrap `_origin`-wrapped JSON payloads.
- Added `scripts/run_gate0_readiness_evidence.py` as the dedicated Gate 0
  evidence runner.
- Updated the mocked source-foundation evidence script so its generated
  development input has an origin header.

Commands run:

```bash
uv run python -m compileall -q aily scripts
uv run python scripts/run_gate0_readiness_evidence.py --help
uv run python scripts/run_source_foundation_graph_evidence.py --help
```

Results:

- Compile/import check passed.
- Gate 0 runner help command succeeded.
- SourceFoundationGraph mocked runner help command succeeded and did not
  generate evidence.

Status:

- M0 moved to `VERIFY`, not `DONE`.
- No acceptance evidence has been generated by the lead agent.
- Gate 0 remains awaiting independent runner/auditor execution and review.

Additional M0 safety change:

- `ObsidianWriter` now rejects API keys that include a literal `Bearer ` prefix,
  preventing silently malformed `Authorization: Bearer Bearer ...` headers.

Additional verification:

```bash
uv run python - <<'PY'
from aily.writer.obsidian import ObsidianWriter
try:
    ObsidianWriter(api_key='Bearer abc', vault_path='.')
except ValueError as exc:
    print('bearer_prefix_rejected', 'Bearer' in str(exc))
else:
    raise SystemExit('expected ValueError')
PY
```

Result:

- `bearer_prefix_rejected True`

## 2026-05-17 V1 Vault Layout Setup Path

Implemented a shared V1 vault layout helper and setup script:

- `aily/writer/vault_layout.py`
- `scripts/setup_v1_vault_layout.py`

The helper defines and inspects the V1 folders from
`docs/AILY_V1_UPGRADE_PLAN.md`, while preserving legacy compatibility folders
used by the current DIKIWI writer.

Updated:

- `DikiwiObsidianWriter` now ensures the V1 layout plus legacy compatibility
  folders when initialized.
- Gate 0 readiness runner now checks the V1 layout through the shared helper.
- Evidence vault review now inspects V1 folders and legacy compatibility
  folders.

Verification used a temporary vault only, not the real Obsidian vault:

```bash
uv run python -m compileall -q aily scripts
uv run python scripts/setup_v1_vault_layout.py --vault-path "$TMPDIR/vault" --dry-run
uv run python scripts/setup_v1_vault_layout.py --vault-path "$TMPDIR/vault"
```

Result:

- Temporary vault contained all required V1 directories.
- Real vault was not modified by the lead agent.

## 2026-05-17 Gate 0 Passed

Independent Gate 0 runner/auditor completed setup, evidence generation, and
audit.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T10-27-19Z_gate0_readiness/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Gate 0 status: `PASS`
- `acceptance.mocked=false`
- `failures_count=0`
- Obsidian REST returned HTTP `200`
- Required V1 vault folders present
- Generated evidence had origin metadata and `modified_by_lead_agent=false`
- Artifact hashes matched
- Secret scan found no obvious leaked credentials

Lead-side read-only verification:

```bash
uv run python scripts/validate_evidence_run.py /Users/luzi/.aily/runs/2026-05-17T10-27-19Z_gate0_readiness
```

Result:

- validator returned `valid=true`
- checked artifacts: `26`
- required folder count: `16`
- missing required directories: `[]`

Tracker update:

- M0 moved to `DONE`.
- M1 moved to `ACTIVE`.

## 2026-05-17 M1 Source Lineage And Vault Projection

Started M1 implementation.

Implemented:

- SourceFoundationGraph now carries source lineage through graph state:
  `origin_path`, `storage_path`, canonical Markdown package path/hash, and
  `source_paths` for DIKIWI agent metadata.
- SourceFoundationGraph now writes an idempotent application-generated canonical
  Markdown artifact into `00-Chaos/canonical-markdown`.
- Canonical vault artifacts include frontmatter with `origin_creator:
  application` and `origin_modified_by_lead_agent: false`.
- SourceFoundationGraph emits `canonical_markdown_vault_artifact_written`.
- Evidence artifact inventory now distinguishes evidence files from runtime
  artifacts, so SQLite DBs and raw runtime package files are hashed but not
  required to carry text/JSON origin headers.
- Added `aily/verify/evidence_validator.py` and
  `scripts/validate_evidence_run.py` for origin/hash/secret hygiene validation.

Development verification:

```bash
uv run python -m compileall -q aily scripts
uv run python scripts/run_source_foundation_graph_evidence.py --runs-root "$TMPDIR/runs" --run-id dev-source-graph-smoke
uv run python scripts/validate_evidence_run.py "$RUN_PATH"
```

Result:

- Compile/import check passed.
- Mocked SourceFoundationGraph development evidence completed with exit code
  `0`.
- Validator returned `valid=true` for the development evidence run.

Important limitation:

- The SourceFoundationGraph run above is mocked development evidence only and
  does not close Gate 1.

## 2026-05-17 Gate 1 Runner Added

Added the real Gate 1 runner:

```text
scripts/run_gate1_pdf_intake_evidence.py
```

The runner is designed for independent execution. It uses:

- a real source PDF from `/Users/luzi/aily_chaos/pdf`
- real SourceStore and source job records
- real PDF extraction and canonical Markdown conversion
- real SourceFoundationGraph execution
- real LLM routes through `DikiwiMind.process_input_foundation`
- real GraphDB
- configured Obsidian vault projection
- generated evidence with origin headers

Also added:

- `aily.writer.vault_layout.write_canonical_markdown_vault_artifact`
- SourceFoundationGraph source lineage propagation into DIKIWI metadata
- canonical Markdown projection to `00-Chaos/canonical-markdown`

Verification:

```bash
uv run python -m compileall -q aily scripts
uv run python scripts/run_gate1_pdf_intake_evidence.py --help
```

Result:

- Compile/import check passed.
- Gate 1 runner help command passed.
- The lead agent did not run Gate 1 acceptance evidence.

Independent Gate 1 runner/auditor launched.

## 2026-05-17 Gate 1 Failure And Fixes

Independent Gate 1 run failed:

```text
/Users/luzi/.aily/runs/2026-05-17T10-35-36Z_gate1_pdf_intake/manifest.json
```

Observed blockers:

- Validator expected volatile SQLite `-wal` and `-shm` files that disappeared
  after DB close.
- `source-manifest.json` lacked role and selection reason.
- `evidence-matrix.json` referenced `workflow-run.json`, but the runner writes
  `workflow-runs.json`.
- Manifest claimed `real_llm=true`, but `llm-calls.jsonl` was empty.
- D/I/K vault notes did not consistently carry the actual `sha256:` source ID.

Fixes implemented:

- Artifact inventory now excludes volatile SQLite `-wal` and `-shm` files.
- Source manifests support per-source context such as role and selection reason.
- Evidence matrix now references `workflow-runs.json`.
- Gate 1 runner writes a run-local LLM trace file and passes it to
  `EvidenceRun.finalize()`.
- SourceFoundationGraph now includes `source_id:<id>` in DIKIWI `source_paths`.
- `DikiwiObsidianWriter` now extracts that source ID into note frontmatter and
  source trace sections.

Verification:

```bash
uv run python -m compileall -q aily scripts
uv run python scripts/run_gate1_pdf_intake_evidence.py --help
uv run python scripts/validate_evidence_run.py --help
```

Result:

- Compile/import check passed.
- Runner and validator CLIs load.

## 2026-05-17 Gate 1 Passed

Independent Gate 1 rerun/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T10-43-47Z_gate1_pdf_intake/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `44`
- Gate 1 status: `PASS`
- Source manifest includes role and selection reason.
- LLM trace includes real DATA, INFORMATION, and KNOWLEDGE calls.
- D/I/K vault sample notes include matching `source_id` frontmatter and Source
  Trace.
- Graph delta: `12` information nodes, `10` tag nodes, `39` edges.
- Vault delta showed new Data, Information, and Knowledge notes.
- `04-Insight`, `05-Wisdom`, and `06-Impact` remained unchanged at `0`.

Tracker update:

- M1 moved to `DONE`.
- M2 moved to `ACTIVE`.

## 2026-05-17 M2 Resume/Idempotency Implementation

Implemented M2 support:

- `WorkflowRunStore` now records `workflow_run_history` transitions.
- Resuming a failed/interrupted workflow back to a non-terminal status clears
  `completed_at`.
- Added `WorkflowRunStore.list_run_history()`.
- Added Gate 2 runner:
  `scripts/run_gate2_resume_idempotency_evidence.py`.

Gate 2 runner design:

- Uses a real PDF source.
- First pass creates source/canonical Markdown and then performs a controlled
  DIKIWI-boundary failure.
- Second pass re-invokes the same workflow run/thread with real DIKIWI.
- Evidence records markdown package reuse, workflow history, checkpoints,
  SourceStore/job state, graph/vault deltas, LLM trace, and negative downstream
  side-effect checks.

Verification:

```bash
uv run python -m compileall -q aily scripts
uv run python scripts/run_gate2_resume_idempotency_evidence.py --help
```

Result:

- Compile/import check passed.
- Gate 2 runner CLI loads.

The lead agent has not run Gate 2 acceptance evidence.

## 2026-05-17 Gate 2 Passed

Independent Gate 2 runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T10-51-47Z_gate2_resume_idempotency/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `47`
- Gate 2 status: `PASS`
- Workflow history shows controlled failure, resume, and completion on the same
  workflow run/thread.
- Source/job/canonical package stayed stable across failure and resume.
- Canonical Markdown was reused.
- SQLite counts showed no duplicate source, job, upload, or markdown package.
- Vault review showed one canonical vault artifact for the source.
- Checkpoints remained on the same thread.
- LLM trace shows real resumed DATA, INFORMATION, and KNOWLEDGE calls.

Follow-up cleanup:

- `WorkflowRunStore.update_status()` now clears `last_error` when a run reaches
  `completed` without a new error.
- `WorkflowRunStore.update_status()` now also clears stale `last_error` values
  when a failed run is resumed to `queued`, `running`, or `interrupted`.

Verification:

```bash
uv run python -m compileall -q aily scripts
uv run python - <<'PY'
# focused WorkflowRunStore state-hygiene smoke
PY
```

Result:

- Compile/import check passed.
- Focused store smoke passed: failed -> running cleared `last_error`, and
  failed -> completed also cleared `last_error`.

Tracker update:

- M2 moved to `DONE`.
- M3 moved to `ACTIVE`.

## 2026-05-17 M3 Chat And Workflow Plan Implementation

Implemented the first durable chat/workflow-plan slice:

- Added `aily/orchestration/chat_store.py` with SQLite-backed chat threads,
  chat messages, topic extractions, workflow plans, workflow-plan decisions, and
  dispatch linkage.
- Added `SETTINGS.chat_store_db_path`.
- Added GraphDB information-node search over labels and rich node properties.
- Added `/api/ui/chat/threads`, `/api/ui/chat/threads/{id}/messages`,
  `/api/ui/workflow-plans/{id}`, and
  `/api/ui/workflow-plans/{id}/confirm`.
- Changed `/api/ui/workflows/iwi` so a raw motive creates an awaiting
  confirmation workflow plan instead of immediately starting I/W/I.
- Added app-side context selection from `03-Knowledge` vault notes plus graph
  information nodes, carrying source IDs and source paths into the plan.
- Added confirmation dispatch that starts real triggered I/W/I only after an
  approved workflow plan exists and graph-backed context is present.
- Extended the evidence acceptance contract with explicit `chat` and `workflow`
  real-component claims.
- Added Gate 3 evidence runner source:
  `scripts/run_gate3_triggered_iwi_evidence.py`.

Development verification:

```bash
uv run python -m compileall -q aily scripts
uv run python - <<'PY'
# focused ChatStore lifecycle smoke
PY
uv run python - <<'PY'
import aily.main
print('imported_main', hasattr(aily.main, '_ui_chat_message_handler'))
PY
uv run python scripts/run_gate3_triggered_iwi_evidence.py --help
uv run python - <<'PY'
# focused EvidenceRun chat/workflow contract smoke
PY
```

Result:

- Compile/import check passed.
- Focused ChatStore lifecycle smoke passed.
- `aily.main` imports and exposes the chat planning handler.
- Gate 3 runner CLI loads.
- EvidenceRun rejects non-real `chat` claims and accepts real
  `chat`/`workflow` claims.

Status:

- M3 remains `ACTIVE` until independent evidence proves the plan is linked to
  real PDF-derived Knowledge.
- Gate 3 evidence runner source has been delegated to the evidence-harness
  worker; the lead agent has not run Gate 3 acceptance evidence.

## 2026-05-17 Gate 3 Passed

Independent Gate 3 runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T11-09-24Z_gate3_triggered_iwi/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `64`
- Gate 3 status: `PASS`
- Chat records persist the user motive and show workflow plan proposal,
  confirmation, and dispatch.
- Workflow plan before confirmation had `status=awaiting_confirmation` and
  `requires_confirmation=true`.
- Pre-confirmation I/W/I vault delta was `0/0/0`.
- Confirmed workflow run `wf_fba1d8a30b474b6fb74c540b08bdc7a6` completed with
  `current_node=IMPACT` and `final_stage=IMPACT`.
- Post-trigger vault counts showed `04-Insight=5`, `05-Wisdom=3`,
  `06-Impact=1`.
- Evidence included source lineage through source ID
  `sha256:24fc16d89449a6c3d722975c85031436ac18feb43da1da98ea13bb9cd8e9b0ea`,
  canonical Markdown path, graph node IDs, source paths, and evidence snippets.
- LLM trace showed 10 successful real Kimi calls.
- Manifest acceptance reported mocked `false`, real files/graph/vault/LLM/chat
  and workflow `true`.
- Secret scan found no obvious API keys or bearer secrets.

Non-blocking note:

- Runtime logs included unresolved wikilink warnings in some Wisdom writes.

Tracker update:

- M3 moved to `DONE`.
- M4 moved to `ACTIVE`.

## 2026-05-17 M4 BusinessPlanningGraph Confirmation Implementation

Implemented the graph-owned confirmation lane:

- Replaced the BusinessPlanningGraph stub with graph nodes for
  `receive_motive`, `extract_topics`, `search_knowledge_context`,
  `draft_workflow_plan`, `await_user_confirmation`, and
  `workflow_plan_confirmed`.
- Added `BusinessPlanningDependencies` so graph nodes use durable
  `ChatStore`, `WorkflowRunStore`, context selection, and event emission.
- The graph now writes chat messages, topic extraction, workflow plan, workflow
  run status/history, and confirmation decisions through durable stores.
- The graph interrupts at `await_user_confirmation` and resumes with
  `langgraph.types.Command(resume={...})`.
- A rejected workflow plan cancels the run; an approved plan completes the graph
  confirmation lane while preserving IDs.
- The UI chat-message handler now starts `BusinessPlanningGraph` and returns the
  graph interrupt/workflow plan instead of manually creating the plan.
- The workflow-plan confirmation handler now resumes the same graph thread
  before any optional downstream I/W/I dispatch.

Development verification:

```bash
uv run python -m compileall -q aily scripts
uv run python - <<'PY'
# isolated BusinessPlanningGraph checkpoint interrupt/resume smoke
PY
uv run python - <<'PY'
# handler-level chat message -> graph interrupt -> confirm/resume smoke
PY
```

Result:

- Compile/import check passed.
- The isolated graph smoke created a business-planning workflow run, persisted
  chat/topic/plan records, interrupted for confirmation, resumed with approval,
  completed the workflow run, and preserved the same workflow plan ID.
- The handler-level smoke verified the UI chat path creates a business-planning
  workflow, returns an awaiting-confirmation plan, and resumes the same graph
  through the confirmation handler with `dispatch=false`.

Status:

- M4 remains `ACTIVE` pending independent real-path evidence using PDF-derived
  Knowledge and a generated manifest.

## 2026-05-17 M4 Passed

Independent M4 runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T11-25-06Z_m4_business_planning_graph/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `67`
- M4 status: `PASS`
- Real PDF used:
  `/Users/luzi/aily_chaos/pdf/105-intel-kommanaboyina-paper-user.pdf`
- Foundation graph completed through `DATA`, `INFORMATION`, and `KNOWLEDGE`.
- Business workflow kind was `business_planning`.
- BusinessPlanningGraph interrupted at `await_user_confirmation`.
- Workflow plan moved from `awaiting_confirmation` to `approved`.
- Resume preserved workflow run/thread/plan IDs and completed the same run.
- Business checkpoint DB existed with `checkpoints=8` and `writes=48`.
- Vault I/W/I deltas were all zero: `04-Insight=0`, `05-Wisdom=0`,
  `06-Impact=0`.
- No triggered I/W/I, export, or email dispatch events were recorded.
- LLM trace recorded 11 real provider calls.

Tracker update:

- M4 moved to `DONE`.
- M5 moved to `ACTIVE`.

## 2026-05-17 M5 Graph-Owned Triggered I/W/I Implementation

Implemented graph-owned post-confirmation I/W/I:

- `BusinessPlanningGraph` now has a `run_iwi` node.
- Confirmation resume accepts `dispatch_iwi=true` to route from
  `await_user_confirmation` into `run_iwi`.
- `BusinessPlanningDependencies` now accepts a triggered I/W/I runner.
- The `run_iwi` node requires selected graph-backed context node IDs.
- Triggered I/W/I now runs under the same `business_planning` workflow run ID,
  instead of creating a second `triggered_iwi` workflow run for graph-created
  plans.
- The workflow run records final stage, pipeline ID, selected node IDs, and I/W/I
  result summary in durable metadata.
- The UI confirmation handler resumes the BusinessPlanningGraph and returns the
  business workflow run result for graph-created plans.

Development verification:

```bash
uv run python -m compileall -q aily scripts
uv run python - <<'PY'
# isolated BusinessPlanningGraph confirmation -> run_iwi smoke with fake runner
PY
uv run python - <<'PY'
# UI handler confirmation -> graph-owned I/W/I smoke with fake runner
PY
```

Result:

- Compile/import check passed.
- Isolated graph smoke resumed the same business workflow thread, called the
  fake I/W/I runner with selected graph node IDs, and ended at `IMPACT`.
- Handler smoke confirmed a graph-created plan with dispatch enabled, kept a
  single `business_planning` workflow run, and ended that run at `IMPACT`.

Status:

- M5 remains `ACTIVE` pending independent real-path evidence with live I/W/I and
  source-lineage reconciliation.

## 2026-05-17 M5 Passed

Independent M5 runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T11-38-53Z_m5_graph_owned_iwi/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `68`
- M5 status: `PASS`
- Real PDF used:
  `/Users/luzi/aily_chaos/pdf/wb7-02-ayyagari-pres-user.pdf`
- Foundation ingestion completed through `KNOWLEDGE`; source status remained
  `completed`.
- Same `business_planning` workflow run/thread moved from interrupted
  confirmation to `IMPACT`:
  `wf_eae3263dd8e14f1a852e72b2f90fc496`.
- No separate `triggered_iwi` workflow run was created.
- Eight selected graph node IDs were passed into real
  `DikiwiMind.process_triggered_iwi`.
- Vault delta after graph-owned I/W/I was `04-Insight +3`,
  `05-Wisdom +3`, `06-Impact +1`.
- Evidence included source records/jobs/canonical package, foundation result,
  graph first/resumed results, chat/workflow/checkpoint/graph/vault/LLM/event
  files with origin headers.

Non-blocking note:

- Runtime emitted unresolved wikilink warnings during some Wisdom note writes.

Tracker update:

- M5 moved to `DONE`.
- M6 moved to `ACTIVE`.

## 2026-05-17 M6 Research And Second-Opinion Packet Implementation

Implemented durable external-evidence packet support:

- Added `aily/research/store.py` with SQLite-backed `ResearchJob`,
  `ResearchPacket`, `SecondOpinionReference`, and `SecondOpinionPacket`
  records.
- Added `aily/research/tavily_packets.py` with Tavily research packet creation,
  mini/pro-to-depth mapping, daily quota guard, source/citation capture, and
  explicit truth-policy metadata.
- Added deterministic second-opinion packet extraction for user-provided
  external references, labeled as non-authoritative.
- Added `SETTINGS.research_store_db_path`.
- Added UI research/second-opinion listing endpoints under `/api/ui`.
- Extended `BusinessPlanningGraph` with `run_deep_research`, routed only after
  confirmation/resume.
- Added graph metadata for `research_ids`, research status, and packet linkage.

Development verification:

```bash
uv run python -m compileall -q aily scripts
uv run python - <<'PY'
# ResearchStore + fake Tavily packet + second-opinion packet smoke
PY
uv run python - <<'PY'
# BusinessPlanningGraph I/W/I + research branch smoke with fake runners
PY
```

Result:

- Compile/import check passed.
- Fake Tavily packet stored without API-key material in the packet contract.
- Second-opinion packet stored with `trusted_by_default=false`.
- Graph smoke resumed after confirmation, ran fake I/W/I and fake research, and
  completed with `research_ids` stored in workflow metadata.

Status:

- M6 remains `ACTIVE` pending independent evidence with a real Tavily call and
  second-opinion packet reconciliation.

## 2026-05-17 M6 Passed

Independent M6 runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T12-00-19Z_m6_research_second_opinion/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `77`
- M6 status: `PASS`
- Real PDFs used: primary `wb7-02-ayyagari-pres-user.pdf`, second opinion
  `lp-01-tu-paper.pdf`.
- Foundation completed real `DATA -> INFORMATION -> KNOWLEDGE`, with outputs
  `11 / 11 / 12`.
- Graph evidence included 11 information nodes and 35 edges.
- Business workflow completed at `research_completed`, with confirmation/resume
  and `dispatch_research=true`.
- Tavily packet completed with `provider=tavily`, `search_depth=basic`,
  `quota_allowed=true`, 3 sources, and 3 claims.
- Packet distinguished `aily_knowledge_context` from `tavily` external search
  and linked to workflow/topic extraction.
- Second-opinion reference was external/non-authoritative and packet truth
  policy had `trusted_by_default=false`.
- Vault reconciliation found the primary source ID in Obsidian notes.

Quality note:

- Topic extraction produced a generic topic label (`run`), making the Tavily
  query broad.

Tracker update:

- M6 moved to `DONE`.
- M7 moved to `ACTIVE`.

## 2026-05-17 M7 Specialist Evaluation And Business Plan Implementation

Implemented business-plan synthesis support:

- Added `aily/business/store.py` with durable `TeamEvaluation` and
  `BusinessPlan` SQLite records.
- Added `aily/business/synthesis.py` with three structured specialist outputs:
  Technical Innovation, Engineering Assessment, and Commercial Feasibility.
- Specialist outputs distinguish internal Aily evidence, external Tavily
  research, and non-authoritative second-opinion claims.
- Business plans include source lineage, unresolved risks, kill criteria,
  evaluation IDs, research IDs, second-opinion IDs, and recommendation.
- Evaluations write Markdown under `08-Evaluations`; business plans write
  Markdown under `09-Business-Plans`.
- Added `SETTINGS.business_plan_store_db_path`.
- Extended `BusinessPlanningGraph` with a `run_business_plan` node after
  confirmation, I/W/I, and/or research.
- Added Studio-facing business-plan list/detail routes under `/api/ui`.

Development verification:

```bash
uv run python -m compileall -q aily scripts
uv run python - <<'PY'
# BusinessPlanStore + BusinessPlanSynthesizer smoke
PY
uv run python - <<'PY'
# BusinessPlanningGraph business-plan branch smoke
PY
```

Result:

- Compile/import check passed.
- Store/synthesizer smoke wrote three evaluation notes and one business plan
  note into a temporary vault.
- Graph smoke resumed after confirmation and completed with `business_plan_id`
  in workflow metadata.

Status:

- M7 remains `ACTIVE` pending independent evidence proving three team outputs,
  merged business plan, Obsidian plan note, source lineage, and unresolved-risk
  reconciliation.

## 2026-05-17 M7 Passed

Independent M7 runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T12-12-54Z_m7_business_plan/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `89`
- M7 status: `PASS`
- Manifest reported `mocked=false`; real files/graph/vault/LLM/chat/workflow
  were all true.
- BusinessPlanningGraph resumed with `dispatch_iwi=true`,
  `dispatch_research=true`, and `dispatch_business_plan=true`.
- Triggered I/W/I reached `IMPACT`.
- Tavily research completed with 3 sources and reconciliation policy.
- Second opinion was marked non-authoritative and not trusted by default.
- Three team evaluations were created.
- Business plan was written under `09-Business-Plans` with frontmatter, source
  lineage, unresolved risks, and kill criteria.

Tracker update:

- M7 moved to `DONE`.
- M8 moved to `ACTIVE`.

## 2026-05-17 M8 Export And Email Dry-Run Implementation

Implemented outbound artifact preparation:

- Added `aily/export/outbound.py` with `ExportEmailService`.
- Markdown business plans can export to PDF and DOCX.
- PDF export uses a minimal local PDF writer; DOCX export uses `python-docx`.
- Export records include source Markdown path/hash and exported artifact hashes.
- Email delivery is dry-run only and records recipients, subject, body,
  attachments, hashes, and safety policy.
- Dry-run delivery records write to `10-Outbox` with
  `real_send_performed=false` and no SMTP/provider call.

Development verification:

```bash
uv run python -m compileall -q aily scripts
uv run python - <<'PY'
# temp-vault export + email dry-run smoke
PY
```

Result:

- Compile/import check passed.
- Temporary business-plan Markdown exported to PDF and DOCX with hashes.
- Email dry-run JSON/Markdown records were written under `10-Outbox`.
- Smoke asserted `real_send_performed=false`.

Status:

- M8 remains `ACTIVE` pending independent Gate 6 evidence using a real
  Obsidian business-plan document and generated export/email artifacts.

## 2026-05-17 M8 / Gate 6 Passed

Independent M8 runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T12-23-22Z_m8_export_email_dry_run/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `56`
- Gate 6 status: `PASS`
- Used real M7 business-plan manifest:
  `/Users/luzi/.aily/runs/2026-05-17T12-12-54Z_m7_business_plan/manifest.json`.
- Selected the real Obsidian plan under `09-Business-Plans`.
- Exported PDF and DOCX under `10-Outbox/_exports`; hashes matched the export
  record.
- Created dry-run JSON and Markdown email records under `10-Outbox`.
- Confirmed `real_send_performed=false` and `smtp_or_provider_called=false`.
- Static inspection found no SMTP/provider send call in
  `ExportEmailService.create_email_dry_run`.
- EvidenceRun was `mocked=false` with real files, vault, and workflow claimed.

Tracker update:

- M8 moved to `DONE`.
- M9 moved to `ACTIVE`.

## 2026-05-17 M9 Release Readiness Passed

Independent M9 release runner/auditor completed.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-17T12-27-08Z_m9_release_readiness/manifest.json
```

Auditor result:

- Evidence runner exit code: `0`
- Validator: `valid=true`
- Checked artifacts: `54`
- M9 status: `PASS`
- Runner-internal `uv run python -m compileall -q aily scripts` passed.

## 2026-05-19 Outbox Removed From Active V1 Flow

User decision:

- Stop generating Outbox artifacts entirely.
- Promote dossiers from `11-Dossiers` to `10-Dossiers`.

Implementation notes:

- Removed the active `aily.export` outbox/export/email service package.
- Removed the M8 export/email dry-run evidence runner.
- Removed export/email generation from the N-PDF panel evidence runner.
- Updated the required vault layout so V1 creates `10-Dossiers` and no longer
  creates `10-Outbox`.
- Updated dossier output defaults to write under `10-Dossiers`.

Verification:

- `uv run python -m compileall -q aily scripts`
- `inspect_v1_vault_layout` confirmed no missing required directories in
  `/Users/luzi/Documents/Aily Test Vaults/20260519T_20pdf_panel_e2e`.
- Current visible test vault cleanup removed the old delivery folder and moved
  dossiers into `10-Dossiers`.

## 2026-05-23 Shared iCloud Vault Becomes Default Test Vault

User decision:

- Future evidence and development runs should use
  `/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily`
  as the default Obsidian vault so generated notes are directly usable.

Implementation notes:

- Updated the local `.env` vault path values to the iCloud Documents vault.
- Updated the default `dikiwi_vault_path` fallback in `aily/config.py`.
- Updated evidence-run vault resolution to use the configured shared vault
  instead of creating `~/Documents/Aily Test Vaults/<run-id>`.
- Updated the visible-vault guard to allow both normal `~/Documents` paths and
  the iCloud Documents path.
- Created the standard V1 vault layout in the iCloud vault with
  `scripts/setup_v1_vault_layout.py`.

Verification:

- `resolve_test_vault_path("example-run")` resolved to the iCloud vault.
- `scripts/setup_v1_vault_layout.py` reported no missing required V1
  directories after setup.

## 2026-05-23 Aily-Copilot AC0/AC1 Started

Research:

- Reviewed `https://github.com/logancyang/obsidian-copilot` source and docs.
- Captured product/code-review findings in `docs/OBSIDIAN_COPILOT_REVIEW.md`.
- Captured Aily-Copilot plan in `docs/AILY_COPILOT_DEVELOPMENT_PLAN.md`.

Implementation:

- Added `aily/copilot` package.
- Added deterministic `VaultSearchService` for lexical vault search, note
  reading, backlink/link extraction, and graph-neighborhood payloads.
- Added `CopilotContextEnvelopeBuilder` with L1-L5-style layers, stable hashes,
  and a citation catalog.
- Added `/api/copilot` router with status, vault search, read note,
  neighborhood, and context-envelope endpoints.
- Mounted the router in `aily/main.py`.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-23T06-50-08Z_aily_copilot_backend/manifest.json
```

Verification:

- `uv run python scripts/run_aily_copilot_backend_evidence.py` exited `0`.
- Evidence runner validated service behavior and FastAPI router smoke calls.
- `uv run python -m compileall -q aily scripts` passed.

## 2026-05-23 Aily-Copilot Grounded Chat And Plugin MVP

Implementation:

- Added `CopilotVaultChatService` for grounded vault answers with citation
  catalogs.
- Added `/api/copilot/chat` with deterministic extractive mode and optional
  live LLM mode.
- Routed `copilot.chat` and `copilot.dossier` workloads to DeepSeek in the
  provider route table.
- Added `/api/copilot/dossiers/generate` to write dossier outputs under
  `10-Dossiers`.
- Added a thin Obsidian companion plugin under
  `obsidian-plugin/aily-copilot`.
- Added `scripts/install_aily_copilot_plugin.py` and installed the plugin into:

```text
/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily/.obsidian/plugins/aily-copilot
```

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-23T07-01-27Z_aily_copilot_backend/manifest.json
```

Verification:

- `uv run python scripts/run_aily_copilot_backend_evidence.py` exited `0`.
- Evidence covers search, read, neighborhood, context envelope, chat API, and
  dossier generation API using a fixture vault.
- `uv run python -m compileall -q aily scripts` passed.
- `node --check obsidian-plugin/aily-copilot/main.js` passed.
- Installed plugin `main.js` under the iCloud vault also passed `node --check`.

Known gaps:

- Real LLM answer quality is not yet verified in this evidence run; the route is
  wired but the evidence uses deterministic mode to avoid unnecessary API spend.
- Obsidian runtime behavior still needs a manual smoke test after enabling the
  plugin in Obsidian.

## 2026-05-23 Aily-Copilot Product Readiness Slice

Implementation:

- Added persistent Aily-Copilot project scopes in `aily/copilot/projects.py`.
- Added preview-first proposal storage in `aily/copilot/proposals.py`; targets
  are not changed until an apply request succeeds.
- Extended `VaultSearchService` with content-based relevant-note
  recommendations and relationship explanations.
- Extended `/api/copilot` with relevant-note, project, and proposal endpoints.
- Added `copilot.chat`/`copilot.dossier` traffic expectations to the LLM
  monitor route table.
- Expanded the Obsidian plugin with project selection, relevant notes, draft
  preview, and apply/reject actions.
- Updated the plugin installer to enable `aily-copilot` in the iCloud vault.
- Added `scripts/run_aily_copilot_real_vault_smoke.py` for HTTP smoke tests
  against the actual iCloud vault and running backend.

Evidence:

```text
/Users/luzi/.aily/runs/2026-05-23T07-21-35Z_aily_copilot_backend/manifest.json
/Users/luzi/.aily/runs/2026-05-23T07-21-35Z_aily_copilot_real_vault/manifest.json
/Users/luzi/.aily/runs/2026-05-23T07-21-42Z_aily_copilot_real_vault/manifest.json
/Users/luzi/.aily/runs/2026-05-23T07-21-42Z_aily_copilot_real_vault/copilot-llm-traffic-monitor.json
```

Verification:

- `uv run python -m compileall -q aily scripts` passed.
- `node --check obsidian-plugin/aily-copilot/main.js` passed.
- Installed plugin `main.js` under the iCloud vault passed `node --check`.
- `uv run python scripts/run_aily_copilot_backend_evidence.py` exited `0`.
- `uv run python scripts/run_aily_copilot_real_vault_smoke.py` exited `0`.
- `uv run python scripts/run_aily_copilot_real_vault_smoke.py --use-live-llm`
  exited `0` and recorded a DeepSeek `copilot.chat` traffic receipt.
- `uv run python scripts/monitor_llm_traffic.py --no-require-kimi ...` exited
  `0` for the copilot live trace.

Runtime status:

- Backend is running at `http://127.0.0.1:8000`.
- The active test/user vault is
  `/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily`.
- The plugin is installed under `.obsidian/plugins/aily-copilot` and enabled in
  `.obsidian/community-plugins.json`.

Residual risk:

- Terminal evidence cannot prove the visual desktop Obsidian click-through.
  The plugin is installed/enabled and backend calls are verified; the remaining
  check is opening Obsidian and using the side panel as a human.
- The real iCloud vault currently contains only a small origin-labeled seed
  pair plus default notes, so large-scale dossier quality still depends on
  ingesting richer source material.

## 2026-05-23 Aily-Copilot Obsidian Runtime Fix

Issue:

- The first thin local plugin could be installed on disk but was not proven
  inside the Obsidian renderer. The chat pane did not perform useful work for
  the user.

Fix:

- Replaced the scratch plugin source with a local fork of
  `logancyang/obsidian-copilot` under `obsidian-plugin/aily-copilot`.
- Preserved the upstream AGPL-3.0 license in the forked plugin tree.
- Replaced the fork entrypoint with an Aily-backed Obsidian plugin that uses
  Obsidian's `requestUrl` API instead of browser `fetch`.
- Added FastAPI CORS middleware for Obsidian app origins.
- Built and installed the forked plugin into the iCloud vault.

Runtime verification:

- `npm ci --ignore-scripts` completed in the fork workspace.
- `npm run build` completed and generated `main.js`/`styles.css`.
- `node --check obsidian-plugin/aily-copilot/main.js` passed.
- `node --check` passed for the installed vault plugin bundle.
- `uv run python -m compileall -q aily scripts` passed.
- `uv run python scripts/run_aily_copilot_real_vault_smoke.py` exited `0`.
- CORS preflight from `Origin: app://obsidian.md` returned `200 OK`.
- Obsidian was relaunched with remote debugging and agent-browser verified the
  actual desktop pane:
  - `Aily Copilot` pane visible.
  - Input and `Ask Aily` button visible.
  - A real question was submitted.
  - The pane returned a grounded answer with citations and relevant notes.
  - Screenshot saved locally at `/tmp/aily-copilot-obisidian-working.png`.
- M0-M9 and Gate0-Gate6 were present and marked `PASS` in the release matrix.
- All 9 required prior manifests validated successfully.
- Gate 6 dry-run review found no real email send.
- M9 claimed only files, vault, and workflow, because the aggregator did not
  directly exercise graph, LLM, or chat paths.

Residual risks recorded as non-blocking:

- M6 topic label was broad: `run`.
- Unresolved wikilink sampled count was `0`.

Tracker update:

- M9 moved to `DONE`.
- Full Aily V1 goal is ready to mark complete.
