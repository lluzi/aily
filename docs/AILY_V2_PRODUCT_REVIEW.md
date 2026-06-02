# Aily V2 Product Definition Review

Origin: Created by Codex lead agent on 2026-05-23 from a four-role product team review of `docs/AILY_V2_PRODUCT_DEFINITION.md`.

## Review Goal

Review the Aily V2 product definition for:

1. potential product issues
2. missing details
3. unclear user value
4. technical feasibility risks
5. market-positioning weaknesses

The review did not modify the product definition. It identifies the changes
that should be made before V2 implementation planning hardens.

## Review Team

| Role | Review Focus |
|---|---|
| Product strategy reviewer | Strategic consistency, scope, V1/V2 contract conflicts, product wedge |
| Obsidian UX reviewer | Human-facing workflows, modes, surfaces, terminology, status and recovery UX |
| Technical feasibility reviewer | Backend/API readiness, state models, graph readiness, project and workflow contracts |
| Market and value reviewer | ICP, differentiation, competitor framing, business-value narrative |

## Executive Verdict

The V2 thesis is strong, but the document is not ready as an implementation
contract.

Aily's strongest position is:

> Evidence-governed source-to-decision production inside an Obsidian vault.

The current definition correctly identifies the source-to-dossier chain, but it
still presents too much internal machinery to the user, scopes V2 too broadly,
and assumes backend product contracts that do not yet exist.

The most important change is to make Aily's first V2 wedge sharper:

```text
messy source folder
  -> high-fidelity source Markdown
  -> tracked Data / Information / Knowledge foundation
  -> grounded Chat and status monitoring
  -> approved project synthesis
  -> evidence-backed business dossier
```

## Cross-Team Consensus

All reviewers converged on these points:

1. Aily should not position itself as a generic Obsidian AI chat plugin.
2. DIKIWI remains important, but user-facing language must lead with user jobs
   and artifacts.
3. The V2 definition needs a narrower first wedge.
4. `00-Chaos` high-fidelity Markdown is strategically critical and needs a
   measurable quality contract.
5. The product needs explicit source/status APIs before the plugin can present
   trustworthy monitoring.
6. Scheduled or graph-triggered I/W/I must not silently violate V1's locked
   requirement that expensive synthesis remain intentional.
7. Trust, privacy, cost, and data-egress controls must be first-class product
   requirements, not later polish.

## Priority Findings

### P0: Resolve Automatic I/W/I Conflict

Problem:

`docs/AILY_V2_PRODUCT_DEFINITION.md` allows daily or graph-threshold
Insight/Wisdom/Impact runs. V1 explicitly says Insight/Wisdom/Impact, Deep
Research, specialist evaluation, business-plan synthesis, export, and email
must stay behind explicit user intent or approval.

Risk:

This creates an architectural and product-governance conflict. It also risks
unexpected cost and user distrust.

Recommendation:

Change scheduled/graph-triggered behavior to produce readiness recommendations,
not automatic I/W/I output, unless an explicit new decision overrides V1.

Suggested product rule:

```text
Scheduled or graph-triggered jobs may identify changed topics, evidence density,
stale artifacts, and synthesis candidates. They may not run expensive I/W/I or
business-plan generation without a configured project scope and user approval.
```

### P0: Make The Obsidian-First Decision Explicit

Problem:

V2 says the Obsidian plugin is the primary product UI. V1 still describes a
lightweight Aily GUI with Obsidian as the document browser.

Risk:

Without an explicit decision, implementation may split between two control
surfaces.

Recommendation:

Add a V2 decision note:

```text
Decision: Aily V2 is Obsidian-first for knowledge work, document browsing,
source monitoring, chat, and project artifact review.
Reason: The user's canonical working environment is the Obsidian vault.
Boundary: Backend administration, service hosting, and low-level diagnostics may
remain outside Obsidian.
Migration impact: Product controls currently under /api/ui need Copilot-facing
API contracts or plugin-safe wrappers.
```

### P0: Define The First V2 Wedge

Problem:

The V2 roadmap includes conversion, dedupe, status, graph monitoring, I/W/I,
Deep Research, Projects, business plans, dossiers, Kiosk reading, and polish.
That is coherent as a long-term system, but too broad for a first release.

Recommendation:

Define V2.0 as:

```text
source -> source-equivalent Markdown -> D/I/K -> status monitor -> grounded Chat
```

Then define V2.1+ as:

```text
approved I/W/I -> Projects -> business plan -> dossier
```

If business plans and dossiers must remain in V2.0, make them the flagship
workflow and remove lower-priority surface area.

### P0: Add Product-Grade Source Status APIs

Problem:

The product definition requires source status, duplicate lookup, stage status,
retry/resume, and plugin status tables. Current Copilot routes do not expose a
complete source/status API. Source/job state exists lower in the backend, but it
is not yet a Copilot-facing product model.

Recommendation:

Define a Copilot-facing `SourceStatus` contract before implementing the UI.

Minimum fields:

```text
source_id
display_title
source_type
original_path_or_url
content_hash
canonical_markdown_hash
duplicate_of_source_id
conversion_status
dikiwi_data_status
dikiwi_information_status
dikiwi_knowledge_status
source_equivalence_score
artifact_paths
workflow_run_ids
last_error
last_processed_at
next_action
```

Minimum endpoints:

```text
GET  /api/copilot/sources
GET  /api/copilot/sources/{source_id}
POST /api/copilot/sources/{source_id}/retry
POST /api/copilot/sources/{source_id}/ignore
GET  /api/copilot/sources/duplicates
```

### P1: Replace Internal Mode Language With User Jobs

Problem:

`Chat`, `DIKIWI`, and `Projects` expose implementation concepts. `DIKIWI` and
`I/W/I` are especially opaque for new users.

Recommendation:

Keep internal mode names if needed, but define user-facing jobs:

| User Job | Internal Layer |
|---|---|
| Ask | Chat / grounded vault interaction |
| Process Sources | DIKIWI foundation monitoring |
| Build Project | Projects, I/W/I, research, business artifacts |

DIKIWI should remain visible as the methodology and evidence pipeline, but the
first UI sentence should be human-facing:

```text
Process Sources: turn selected documents into tracked Data, Information, and
Knowledge.
```

### P1: Add A First-Run Workflow

Problem:

The product definition does not explain the path from install to first value.

Recommendation:

Add a first-run happy path:

```text
1. Install Aily Copilot in Obsidian.
2. Connect to Aily backend.
3. Choose or confirm the watched source folder.
4. Drop a PDF/deck/URL.
5. Watch conversion and D/I/K status.
6. Open the source-equivalent Markdown.
7. Ask a grounded question.
8. Create a Project from the source or topic.
9. Run approved synthesis.
10. Open the business plan or dossier.
```

### P1: Define Obsidian Surfaces

Problem:

The document promises an Obsidian-native product but does not map features to
Obsidian affordances.

Recommendation:

Add a surface map:

| Surface | Product Use |
|---|---|
| Right sidebar chat | Ask, project interaction, evidence explanations |
| Left/sidebar source monitor | source queue, stage status, duplicates, retry |
| Command palette | run DIKIWI, generate dossier, refresh source status |
| File explorer context menu | ingest selected file, attach to project, trace source |
| Editor context menu | run on selected text, explain claim, add to project |
| Status bar | backend status, active queue, provider/cost warnings |
| Obsidian notices | duplicate, failure, completion, approval required |
| Generated notes | readable artifacts with frontmatter and backlinks |
| Graph view | content-based connections, not artificial hub links |

### P1: Make Source-Equivalent Markdown Measurable

Problem:

The term is central but underspecified. Current implementation is strongest for
some PDF paths; PPTX/Word/URL/video quality is not uniformly guaranteed.

Recommendation:

Define quality contracts by source type:

| Source Type | V2.0 Minimum |
|---|---|
| PDF | text structure, page/slide images when useful, source metadata, page references |
| PPTX | one section per slide, slide image asset, speaker notes if available |
| Word | headings, tables, lists, images, footnotes where available |
| URL | title, canonical URL, readable article body, capture time, media references |
| Video | transcript, timestamp sections, key visual screenshots when available |

Add status levels:

```text
Equivalent
Readable
Partial
Failed
Unsupported
```

### P1: Define Trust, Privacy, And Cost Policy

Problem:

The product uses external APIs for LLMs and Tavily, but the V2 definition does
not say what leaves the machine, when, or how the user controls cost.

Recommendation:

Add a policy section:

- show provider used for each run
- show whether source text is sent externally
- default Deep Research to approval-required
- show estimated token/cost before expensive workflows
- log traffic metadata without exposing secrets
- never store API keys in generated notes
- show quota/provider failures in the source monitor
- require user approval before email/export delivery

### P1: Add Artifact Naming And Lineage Rules

Problem:

Generated artifact folders are defined, but naming, frontmatter, update,
regeneration, and cleanup rules are not.

Recommendation:

Every generated artifact should include frontmatter like:

```yaml
---
aily_generated: true
artifact_type: knowledge
source_ids:
  - src_...
workflow_run_id: wf_...
created_by: aily
created_at: 2026-05-23T00:00:00Z
lineage:
  chaos: path/to/source.md
  data: path/to/data.md
  information: path/to/information.md
---
```

Rules needed:

- deterministic filenames
- backlinks to source package
- duplicate prevention
- regeneration creates a new version or updates only managed regions
- user-authored notes are never silently overwritten

### P1: Clarify Business Artifact Hierarchy

Problem:

The V2 doc treats business plans, evaluations, dossiers, export, and email as
related but not hierarchically distinct.

Recommendation:

Define:

| Artifact | Product Role |
|---|---|
| Impact | DIKIWI synthesis output |
| Evaluation | specialist review layer |
| Business plan | decision artifact |
| Technical proposal | implementation/engineering artifact |
| Dossier | explanation and learning artifact |
| Export/email | delivery channel, approval-gated |

### P2: Strengthen Competitive Positioning

Problem:

The V2 doc lists competitors but underestimates how much they already do.
Copilot, Smart Plugins, Note Companion, and Caret all cover meaningful parts of
vault chat, context, workflows, organization, graph/context layers, local-first
storage, and media transcription.

Recommendation:

Use a sharper contrast:

| Plugin Category | Typical Value | Aily Value |
|---|---|---|
| Chat/RAG plugins | ask notes questions | build auditable decisions from sources |
| Organization plugins | clean/tag notes | govern source processing and stage status |
| Canvas/workflow tools | explore ideas | produce decision artifacts with lineage |
| Research tools | gather web context | merge external research with vault evidence |

Add target users:

- founders
- consultants
- product and innovation leads
- technical analysts
- investment or market researchers
- small teams already using Obsidian for knowledge work

Explicitly exclude casual note chat as the primary wedge.

### P2: Rename Or Define Kiosk

Problem:

`Aily Kiosk` is promising but unclear. It may sound like a separate platform,
while the actual user need is a source library or reading queue.

Recommendation:

Either:

1. rename it to `Source Library` or `Reading Queue`, or
2. keep `Aily Kiosk` as a branded panel and define it precisely.

Minimum definition:

```text
The Source Library is a sidebar/full-page Obsidian view for recently converted
source-equivalent Markdown. It filters by date, type, topic, status, duplicate,
project, and source quality. Opening a source shows the Markdown package,
assets, lineage, and available actions.
```

## Technical Contract Gaps

The technical reviewer identified these missing contracts:

1. `SourceStatus` API for source and stage state.
2. source-equivalence score and quality fields in durable state.
3. duplicate lookup exposed to Copilot.
4. graph snapshots or graph delta records.
5. Copilot workflow BFF for I/W/I, business planning, and status/events.
6. durable project memberships and workflow history.
7. research job records for Tavily/Deep Research.
8. dossier output that states whether external research was included, skipped,
   unavailable, or failed.

## Recommended V2 Roadmap Revision

### V2.0: Source Foundation

Goal:

Prove that Aily can convert messy source material into trustworthy,
source-linked Markdown and Knowledge inside Obsidian.

Deliverables:

- source-equivalent Markdown for PDF first, PPTX next
- SourceStatus API
- duplicate notification
- D/I/K status monitor
- grounded Chat over processed sources
- source artifact lineage

Gate:

- user can drop a representative source, read converted Markdown, see D/I/K
  status, ask a cited question, and inspect source lineage.

### V2.1: Approved DIKIWI Synthesis

Goal:

Let users intentionally run Insight/Wisdom/Impact on a topic, note, folder, or
project.

Deliverables:

- readiness signals
- topic-to-search planner
- approval-required I/W/I run
- graph delta explanation
- evidence gaps
- workflow run status

Gate:

- user can approve a topic run and inspect the evidence used for I/W/I.

### V2.2: Projects And Decision Artifacts

Goal:

Turn processed knowledge into business and technical outputs.

Deliverables:

- durable project membership
- project workflow history
- Deep Research job model
- evaluation artifacts
- business plan generator
- technical proposal generator

Gate:

- project output includes source lineage, assumptions, risks, and unresolved
  gaps.

### V2.3: Dossier And Reader Experience

Goal:

Make generated decisions readable, teachable, and reviewable.

Deliverables:

- dossier generation
- claim-to-evidence table
- creation history
- source library / reading queue
- quality scoring

Gate:

- dossier can be read as a substantive human learning artifact and audited back
  to the source chain.

## Open Product Decisions

1. Is V2 explicitly Obsidian-first, or does Aily retain a separate primary GUI?
2. Should daily I/W/I ever auto-run, or only create approval-required readiness
   recommendations?
3. What is the first source-equivalent MVP: PDF only, or PDF plus PPTX?
4. Should the plugin call `/api/ui/*`, or should all product controls live under
   `/api/copilot/*`?
5. Are `Chat`, `DIKIWI`, and `Projects` final user-facing labels, or should
   DIKIWI move behind user-facing labels like `Process Sources`?
6. Is the first public wedge source foundation, or evidence-backed business
   dossiers?
7. What is the default external API policy for DeepSeek, Kimi, and Tavily?

## Recommended Edits To Product Definition

Before starting large implementation work, revise
`docs/AILY_V2_PRODUCT_DEFINITION.md` to add:

1. `Who This Is For`
2. `Primary User Workflows`
3. `Obsidian Surfaces`
4. `Trust, Privacy, And Cost Policy`
5. `Source-Equivalent Markdown Quality Contract`
6. `Artifact Naming And Lineage Rules`
7. `Product Decision Notes`
8. `Revised V2.0/V2.1/V2.2/V2.3 Roadmap`

## External Positioning References

- Copilot Vault QA: <https://www.obsidiancopilot.com/en/docs/vault-qa>
- Smart Plugins: <https://smartconnections.app/>
- Note Companion: <https://www.notecompanion.ai/>
- Note Companion community listing: <https://community.obsidian.md/plugins/fileorganizer2000>
- Caret: <https://caretplugin.ai/>
- Caret docs: <https://caretplugin.ai/docs>

These references show that the market already contains strong vault chat,
organization, local-first, context, graph, workflow, and media-ingestion claims.
Aily should therefore compete on governed source-to-decision production with
auditable lineage.
