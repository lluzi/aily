<!--
Origin: Created by Codex lead agent on 2026-05-17.
Role: Planning/runbook document only; not acceptance evidence for any gate.
-->

# Aily Test Quality Gates

Date: 2026-05-17

This document defines the evidence gates for testing Aily against the V1 design.
A gate is not complete because a small helper test passes. A gate is complete
only when an evidence run proves the intended product state with real inputs and
observable artifacts.

No gate may pass from a single document, single log, or single database query.
Each gate must be judged from multiple independent evidence sources, including a
review of the Obsidian vault contents.

The lead agent must not manually create, edit, or repair evidence artifacts used
to pass a gate. Evidence must be produced by the application, evidence runner,
test harness, external service response, database export/query, or independent
review process. The lead agent may inspect and summarize evidence, but the
source artifacts must remain machine-generated or independently produced.

Every generated evidence file must begin with an origin header that identifies
who or what created it, when it was created, how it was generated, and whether it
is acceptance evidence, development evidence, or reviewer commentary.

## Runtime Rule

Use the project runtime:

```bash
uv run python ...
```

Do not use bare `python3` for Aily gates. The system Python on this machine does
not match the project dependency environment.

## Evidence Corpus

Use original PDFs from:

```text
/Users/luzi/aily_chaos/pdf
```

The corpus currently contains 286 PDFs. Gates should use a small selected set,
not the full corpus, unless the gate is explicitly a scale or batch gate.

Default PDF gate set:

| Role | File | Size |
|---|---|---:|
| Small PDF | `/Users/luzi/aily_chaos/pdf/wb7-02-ayyagari-pres-user.pdf` | 290451 bytes |
| Medium PDF | `/Users/luzi/aily_chaos/pdf/tb3-02-ju-pres-user.pdf` | 813701 bytes |
| Large PDF | `/Users/luzi/aily_chaos/pdf/dd-12-akash-pres-user.pdf` | 23838274 bytes |

The evidence manifest must record the full path, size, SHA-256 hash, selection
reason, and run role for every selected source.

## Gate Status

Each gate has one of these statuses:

| Status | Meaning |
|---|---|
| `BLOCKED` | Required config, dependency, service, or source is missing. |
| `READY` | The gate can be run, but no real evidence run has passed yet. |
| `RUNNING` | A real evidence run is in progress. |
| `PASS` | A manifest proves the required observable state. |
| `FAIL` | The run completed but evidence is missing, inconsistent, or regressed. |

## Global Pass Contract

Every gate that claims `PASS` must have:

- `~/.aily/runs/<run_id>/manifest.json`
- `acceptance.mocked=false`
- real source files from the selected PDF set
- command, git state, environment snapshot, and started/completed timestamps
- source manifest with hashes
- stdout/stderr logs
- explicit artifact paths and artifact hashes
- no API keys or bearer tokens in artifacts
- a reconciliation section explaining how observed state proves the gate
- `evidence-matrix.json`, mapping every gate requirement to at least two
  independent evidence sources
- `obsidian-vault-review.json`, recording vault files inspected, frontmatter,
  links, stage folder placement, source IDs, and reviewer observations
- `cross-source-reconciliation.json`, comparing runtime test results, database
  state, vault content, logs/events, and direct observations
- origin headers in every generated evidence file
- no evidence artifact manually authored or modified by the lead agent

Mocked runs may support development, but they cannot close a quality gate.

## Evidence Origin Headers

Every evidence file must start with an origin header appropriate to its format.

Markdown or text evidence:

```text
---
origin_creator: <application|evidence-runner|test-harness|external-service|reviewer>
origin_created_at: <ISO-8601 timestamp>
origin_generation_method: <command, API endpoint, DB query, or review procedure>
origin_evidence_class: <acceptance|development|review-commentary>
origin_modified_by_lead_agent: false
---
```

JSON evidence:

```json
{
  "_origin": {
    "creator": "<application|evidence-runner|test-harness|external-service|reviewer>",
    "created_at": "<ISO-8601 timestamp>",
    "generation_method": "<command, API endpoint, DB query, or review procedure>",
    "evidence_class": "<acceptance|development|review-commentary>",
    "modified_by_lead_agent": false
  }
}
```

If a file was created by Codex as a planning artifact rather than evidence, it
must say so in its header and must not be used to satisfy a gate.

## Multi-Source Evidence Rule

Every gate must be reviewed from these perspectives:

| Perspective | Required Evidence |
|---|---|
| Source truth | Original source file paths, hashes, sizes, and selection reason. |
| Runtime truth | Commands, stdout/stderr, LLM traces where relevant, and process exit status. |
| Durable state truth | SourceStore, WorkflowRun, LangGraph checkpoint, GraphDB, or other SQLite/domain records. |
| Obsidian truth | Vault files, folder placement, frontmatter, backlinks/source IDs, and content excerpts. |
| Event truth | UI/status events, audit records, queue records, or watcher observations. |
| Human observation | A short written observation of what was inspected and whether it matches the design intent. |

Gate judgment must use an all-of condition:

```text
PASS = every required perspective is present, internally coherent, and aligned
       with the expected design behavior.
FAIL = any required perspective is missing, contradicts another source, leaks
       secrets, or shows behavior outside the gate's allowed scope.
```

The Obsidian review is mandatory even for gates that are not primarily about
writing documents. In those cases, the review must confirm either:

- the expected vault artifacts exist and match the run, or
- no new vault artifact should exist for that gate, and the vault was checked
  for accidental side effects.

## Gate 0: Test Configuration Readiness

Purpose:

Prove the local machine is configured for real Aily V1 gate runs.

Required evidence:

- `uv run python -m compileall -q aily scripts` succeeds.
- `TAVILY_API_KEY` is configured.
- `OBSIDIAN_REST_API_KEY` is configured without a literal `Bearer ` prefix.
- `ORCHESTRATOR_ENABLED=true`.
- `ORCHESTRATOR_SHADOW_MODE=false`.
- `INBOX_WATCHER_ENABLED=true`.
- `DIKIWI_FOUNDATION_ONLY_INGESTION=true`.
- `EMAIL_DELIVERY_ENABLED=true`, with delivery gates still restricted to draft
  or dry-run unless a separate explicit send approval exists.
- `~/Aily/Inbox` exists.
- `~/.aily/runs` exists.
- Obsidian REST service responds on `127.0.0.1:27123` before any REST behavior
  is claimed.
- Obsidian vault path exists and the V1 folders are inspected:
  `00-Chaos`, `00-Chaos/_assets`, `00-Chaos/sources`,
  `00-Chaos/canonical-markdown`, `01-Data`, `02-Information`,
  `03-Knowledge`, `04-Insight`, `05-Wisdom`, `06-Impact`, `07-Research`,
  `07-Research/Second-Opinions`, `08-Evaluations`, `09-Business-Plans`,
  `10-Dossiers`, and `99-System`.
- Vault review confirms no unexpected test artifacts were created by readiness
  checks.

Current status:

`BLOCKED` for Obsidian REST claims because the local REST service is not
currently listening on `127.0.0.1:27123`. Other local feature flags are
configured.

## Gate 1: PDF Intake To Knowledge

Purpose:

Prove real PDF ingestion reaches the durable knowledge foundation and stops
after Knowledge by default.

Input:

- Default PDF gate set, or at least one selected corpus PDF for a narrow smoke
  run.

Required evidence:

- SourceStore row for each selected PDF.
- Canonical Markdown package for each selected PDF.
- Chaos/raw source artifact or source-object record for each selected PDF.
- Data, Information, and Knowledge stage artifacts.
- GraphDB node/edge deltas tied to the source IDs.
- WorkflowRun record and LangGraph checkpoint/thread IDs when graph path is
  enabled.
- UI/status events showing ingestion progress and completion.
- Obsidian filesystem notes in the expected numbered vault layout when the
  writer is used.
- Obsidian vault review checks `00-Chaos`, `01-Data`, `02-Information`, and
  `03-Knowledge` for matching source IDs, frontmatter, links, and content
  excerpts.
- No automatic `04-Insight`, `05-Wisdom`, `06-Impact`, business-plan, export,
  or email artifacts from plain ingestion.

Pass condition:

The evidence matrix reconciles source IDs, canonical package IDs, stage outputs,
graph deltas, vault notes, UI/status events, and workflow status for the
selected PDFs. The Obsidian review must agree with the database and runtime
results.

## Gate 2: Resume And Idempotency

Purpose:

Prove partial failure and retry do not duplicate durable side effects.

Required evidence:

- One run interrupted or failed after source registration or canonical Markdown
  creation.
- A retry/resume run for the same source.
- Existing source and Markdown package are reused.
- Duplicate source count is zero or explicitly reconciled as the same stable
  source identity.
- Duplicate Obsidian note count is zero for the same stage/source identity.
- WorkflowRun transitions show failed/interrupted then resumed/completed.
- Obsidian vault review compares before/after file lists and confirms no
  duplicated stage notes or orphaned restart artifacts.

Pass condition:

The retry reaches the intended terminal state without re-extracting or
duplicating source, markdown, graph, event, or vault artifacts. Vault state,
database state, and runtime logs must agree.

## Gate 3: Triggered I/W/I Synthesis

Purpose:

Prove Insight, Wisdom, and Impact are user-triggered synthesis work, not
automatic ingestion work.

Required evidence:

- A user motive or chat prompt is persisted.
- A workflow plan is generated from the motive.
- The workflow pauses for confirmation before I/W/I.
- No I/W/I artifacts exist before confirmation.
- After confirmation, Insight, Wisdom, and Impact artifacts are created.
- Every generated claim links back to Knowledge/source evidence.
- Obsidian vault review confirms pre-confirmation absence and post-confirmation
  presence of expected `04-Insight`, `05-Wisdom`, and `06-Impact` notes.

Pass condition:

The evidence matrix proves the pause/resume boundary from workflow state,
events, logs, and vault contents, and shows post-confirmation I/W/I outputs with
source lineage.

## Gate 4: Deep Research Packet

Purpose:

Prove external research supplements internal knowledge through a controlled,
traceable packet.

Required evidence:

- Tavily request made with configured search depth and explicit query.
- Research quota check recorded before the call.
- Result packet stores query, timestamps, source URLs, titles, summaries, and
  relevance metadata.
- Packet distinguishes internal Aily evidence from Tavily evidence.
- No API key appears in the packet, logs, or manifest.
- Obsidian vault review confirms whether the research packet was written to the
  vault, linked into a generated document, or intentionally kept outside the
  vault for that run.

Pass condition:

A real Tavily packet exists and is linked to a workflow run or topic extraction,
and the packet's claims reconcile with vault content and internal Aily evidence.

## Gate 5: Business Planning Graph

Purpose:

Prove Aily can transform the knowledge foundation plus user motive into a
business-planning output.

Required evidence:

- Topic extraction from user motive.
- Obsidian/Knowledge context selection.
- Optional second-opinion attachment marked non-authoritative when used.
- Technical Innovation, Engineering Assessment, and Commercial Feasibility
  outputs.
- Merged business plan with source lineage and unresolved-risk section.
- Obsidian document written for the generated plan.
- Obsidian vault review confirms the generated plan, team outputs if written,
  source links, frontmatter, and unresolved-risk section match the workflow
  records and test observations.

Pass condition:

The evidence matrix reconciles workflow state, team outputs, business-plan
document, Obsidian path, source lineage, and reviewer observations from multiple
perspectives.

## Gate 6: Export And Email Dry-Run

Purpose:

Prove formal outbound artifacts can be prepared without accidentally sending
real email.

Required evidence:

- Selected Obsidian Markdown document exported to PDF.
- Selected Obsidian Markdown document exported to DOCX.
- Exported file hashes recorded.
- Email draft or dry-run JSON records recipients, subject, body, attachment
  paths, and attachment hashes.
- No real send occurs unless a separate explicit approval exists for a safe
  mailbox.
- Obsidian vault review confirms the exported source Markdown and any delivery
  record match the exported files and email dry-run metadata.

Pass condition:

The evidence matrix proves export artifacts and email dry-run artifacts exist,
the vault source document matches the exports, and no unapproved send occurred.

## Release-Candidate Gate

A V1 release candidate is not accepted until these gates pass with real evidence:

1. Gate 0 configuration readiness.
2. Gate 1 real PDF intake to Knowledge.
3. Gate 2 resume/idempotency.
4. Gate 3 triggered I/W/I.
5. Gate 4 real Tavily research packet.
6. Gate 5 business planning graph.
7. Gate 6 PDF/DOCX export and email dry-run.
