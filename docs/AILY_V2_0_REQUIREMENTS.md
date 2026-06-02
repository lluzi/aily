# Aily V2.0 Requirements

Origin: Created by Codex lead agent on 2026-05-23 from the Aily V2 product definition, product-team review, and user direction to finish V2.0 requirements before development and testing.

## Status

This document is the implementation contract for Aily V2.0.

It narrows the larger V2 product vision into the first releasable product
wedge. V2.0 must prove that Aily can operate as an Obsidian-native source
foundation before expanding into full Projects, business plans, and dossiers.

V2.0 also must fully implement Aily Copilot as the operating surface for this
source foundation. Aily Copilot is not a thin demo wrapper or a later UI task;
it is the required product interface for all V2.0 user-facing workflows.

## V2.0 Product Decision

Decision:

Aily V2.0 is Obsidian-first for knowledge work.

Reason:

The user's working environment is the Obsidian vault. The plugin is where the
user should ingest, inspect, read, ask, and monitor knowledge processing. The
Aily backend remains the processing engine and state owner.

Boundary:

- Obsidian is the primary user surface for source monitoring, source reading,
  grounded Chat, and DIKIWI foundation status.
- Backend service administration, low-level diagnostics, and daemon management
  may remain outside Obsidian.
- Product-grade controls used by the plugin should live under `/api/copilot/*`
  or an equivalent plugin-safe API facade. The plugin should not depend on
  unstable internal `/api/ui/*` behavior for core product flows.

## Aily Copilot Collaboration Decision

Decision:

Aily Copilot and Aily backend must be developed as one product system in V2.0.

Reason:

The backend creates value only when the user can operate, monitor, and trust it
from Obsidian. Future development depends on a stable collaboration contract
between the plugin and backend, so V2.0 must finish the Copilot product surface
for the source-foundation workflow.

Required collaboration model:

```text
Aily Copilot UI
  -> /api/copilot product API
  -> Aily source store / DIKIWI foundation / vault writer
  -> status, citations, events, artifacts
  -> Aily Copilot UI
```

Rules:

- every V2.0 backend capability must have a corresponding Copilot affordance
- every Copilot action must call a stable product API or documented facade
- backend status and failures must be visible in Copilot
- generated artifacts must be openable from Copilot
- Copilot must not forge or manually repair backend evidence
- Copilot must not silently write to user-authored notes
- Copilot must preserve source IDs and workflow IDs across all user-visible
  actions

## V2.0 North Star

Turn messy source material into trustworthy, readable, source-linked knowledge
inside Obsidian.

V2.0 user-visible flow:

```text
drop source files / add URL
  -> high-fidelity Markdown in 00-Chaos
  -> duplicate and quality status
  -> Data
  -> Information
  -> Knowledge
  -> grounded Chat over processed sources
```

Automation boundary:

```text
Automatic:
  Data -> Information -> Knowledge

Human-controlled through Aily Copilot tools:
  Insight -> Wisdom -> Impact -> Research -> Evaluation
  -> Business Plan -> Dossier
```

This boundary is non-negotiable. Foundation processing should be automatic and
idempotent. Higher-order reasoning and value-realization workflows must be
started by explicit user instruction through Aily Copilot tools.

## V2.0 Scope

### In Scope

- Obsidian plugin as primary UI.
- Full Aily Copilot feature surface for V2.0 source-foundation workflows.
- Aily backend as source-processing and state engine.
- Source intake for local files and URLs.
- High-fidelity Markdown in `00-Chaos`.
- PDF and PPTX as required source-equivalent formats.
- URL-to-readable Markdown as a required source type.
- Word documents and videos as supported when existing paths are available, but
  not release-blocking unless explicitly selected for a gate sample.
- Duplicate detection and user notification.
- Automatic Data, Information, and Knowledge foundation processing.
- Product-grade source/status API.
- Source monitor UI in the plugin.
- Grounded Chat over processed sources with citations.
- Copilot commands, context menus, settings, status bar, notices, source
  previews, and artifact open actions required to operate V2.0.
- Copilot-backend event/status reconciliation for long-running jobs.
- Project mode shell for grouping source scopes, without V2.1+ synthesis output.
- Trust, privacy, cost, and external-provider visibility.
- Real-path testing with representative source files.

### Out Of Scope For V2.0

- Automatic Insight/Wisdom/Impact generation.
- Automatic Research generation.
- Automatic Evaluation generation.
- Automatic business-plan generation.
- Automatic dossier generation.
- Deep Research as a default step.
- Email delivery.
- Silent edits to user-authored notes.
- Final graph-delta percentage automation.
- Polished public onboarding beyond the minimum needed to operate V2.0.

These are deferred, not abandoned:

- V2.1: approval-based Insight/Wisdom/Impact.
- V2.2: human-invoked Research, Evaluation, Business Plan, and other decision
  artifacts.
- V2.3: human-invoked dossiers and reader experience.

## Target Users

V2.0 is for users who collect dense source material and need to turn it into a
reliable knowledge base:

- founders evaluating technical or market opportunities
- consultants preparing client research foundations
- product and innovation leads comparing directions
- technical analysts working from reports, PDFs, decks, and links
- small teams already using Obsidian for serious knowledge work

V2.0 is not optimized for casual note chat. It must be good at source-heavy
work where traceability and processing status matter.

## User-Facing Jobs

| User Job | Product Surface | Internal Layer |
|---|---|---|
| Ask | Chat mode | grounded vault Chat |
| Process Sources | DIKIWI/source monitor | Data, Information, Knowledge |
| Build Project | Projects mode source workspace | scoped sources now; V2.1+ synthesis later |

DIKIWI remains the methodology, but V2.0 UI copy should describe the human job:

```text
Process Sources: turn selected files and URLs into tracked Data, Information,
and Knowledge.
```

## Required Aily Copilot Feature Set

V2.0 is incomplete unless the following Aily Copilot features work against the
real Aily backend and the configured Obsidian vault.

### Chat

- default mode is Chat
- asks grounded questions over processed sources and Knowledge notes
- shows citations and source previews
- opens cited source artifacts in Obsidian
- reports insufficient evidence instead of inventing unsupported answers
- shows backend/provider failures in the chat surface
- shows whether external LLM providers were used

### Process Sources / DIKIWI

- user can enable or disable automatic source triggering
- user can manually ingest the active file
- user can manually ingest a URL
- user can open the Source Monitor
- user can see Data, Information, and Knowledge status per source
- user can see duplicate records and existing artifact links
- user can retry, ignore, or open failed/partial sources
- user can see conversion quality level and source-equivalence score

The only automatic DIKIWI stages in V2.0 are:

```text
Data
Information
Knowledge
```

Copilot may show readiness for later stages, but it must not run them without a
human instruction.

### Projects

Projects mode must exist in V2.0 as a source workspace, not as a full
business-plan engine.

V2.0 Projects requirements:

- create, rename, and delete project records
- attach source records, folders, tags, and notes to a project scope
- use project scope in grounded Chat retrieval
- show project source status summary
- preserve project source membership and workflow history placeholders

V2.0 Projects must not automatically run I/W/I, Deep Research, business plans,
or dossiers.

### Human-Controlled Workflow Tools

Aily Copilot is the required control surface for every post-Knowledge workflow.
These workflows may be implemented after the source-foundation slice, but their
execution model is already fixed:

| Stage | Copilot Tool Requirement | Automation Policy |
|---|---|---|
| Insight | run on selected topic, note, folder, tag, or project | explicit user instruction only |
| Wisdom | run from approved Insight or topic workflow | explicit user instruction only |
| Impact | run from approved Wisdom or topic workflow | explicit user instruction only |
| Research | run Tavily/Deep Research for a selected topic/project | explicit user instruction only |
| Evaluation | run specialist review on selected Impact/project artifact | explicit user instruction only |
| Business Plan | generate from selected project/evidence set | explicit user instruction only |
| Dossier | generate from selected plan/evidence chain | explicit user instruction only |

Every post-Knowledge tool must show:

- requested scope
- estimated external API usage when available
- provider route
- source/evidence set
- confirmation before expensive external calls
- generated artifact location
- workflow run ID

Scheduled jobs and graph-change jobs may prepare candidate recommendations for
these tools. They must not execute the tools by themselves.

### Comment-Based Workflow Invocation

Post-Knowledge workflows must be invoked through explicit Aily Copilot tool
calls with a user comment.

The comment is not decoration. It is the task brief for the workflow and must be
persisted with the workflow run.

Minimum invocation contract:

```text
tool: run_end_to_end_value_workflow
topic: required
comment: required
scope: optional note/folder/tag/project/source IDs
requested_outputs: insight,wisdom,impact,research,evaluation,business_plan,dossier
```

The comment must explain what the user wants Aily to do for the given topic,
for example:

```text
Analyze whether this technology can support a differentiated AI product. Search
our vault first, use Tavily for missing market context, run IWI, evaluate the
technical and commercial risks, then prepare a business plan and dossier.
```

Every post-Knowledge workflow run must record:

- user comment
- topic
- selected scope
- requested outputs
- knowledge-base search query
- Tavily/deep-research query, if used
- provider routes
- source/evidence set
- workflow run ID
- generated artifact paths

### End-To-End Value Workflow Tool

Aily Copilot must support a tool that can orchestrate the complete
human-requested value workflow for a topic.

Required workflow:

```text
user tool call with topic/comment
  -> keyword search and source acquisition from Obsidian vault
  -> evidence set construction from Knowledge and source packages
  -> Tavily deep research for missing external context
  -> Insight
  -> Wisdom
  -> Impact
  -> follow-up research based on IWI outputs
  -> evaluation
  -> business plan
  -> dossier content under 10-Dossiers
```

Mandatory behavior:

- always search the local knowledge base and Obsidian vault at the start
- use the user comment to shape the search plan and output plan
- keep Vault evidence separate from Tavily evidence
- label unsupported claims and evidence gaps
- preserve source IDs, research job IDs, and workflow IDs
- write generated artifacts to the proper stage folders
- generate the final dossier from the business plan and its evidence chain
- show progress and final artifact links in Aily Copilot

Example workflow tools:

| Tool | Required Purpose |
|---|---|
| `search_knowledge_base` | search processed source packages, Data, Information, Knowledge, and Obsidian notes |
| `run_deep_research` | use Tavily for topic-specific external context |
| `run_iwi_workflow` | run Insight, Wisdom, and Impact from selected evidence |
| `run_evaluation` | evaluate IWI or project outputs with specialist criteria |
| `generate_business_plan` | synthesize selected evidence and evaluations into a plan |
| `generate_dossier` | produce the `10-Dossiers` reader artifact |
| `run_end_to_end_value_workflow` | orchestrate all required steps from comment/topic to dossier |

V2.0 does not require these tools to be fully implemented, but the API and UI
contract must reserve them so future development can add them without changing
the product model.

### Settings

Settings must expose:

- Aily backend URL and token
- DeepSeek and Kimi provider configuration
- Tavily configuration, visible but not automatic
- DIKIWI automatic trigger toggle
- watched/included/excluded source paths
- allowed source file types
- source-equivalence quality threshold
- external API visibility and logging options

### Commands And Context Menus

Required commands:

- `Aily: Open Chat`
- `Aily: Open Source Monitor`
- `Aily: Ingest Active File`
- `Aily: Ingest URL`
- `Aily: Refresh Source Status`
- `Aily: Retry Failed Source`
- `Aily: Show Source Lineage`
- `Aily: Attach Source To Project`
- `Aily: Search Knowledge Base`
- `Aily: Run Deep Research`
- `Aily: Run IWI Workflow`
- `Aily: Run End-To-End Value Workflow`

Required context menus:

- file explorer: ingest selected file, show source status, attach to project
- editor: ask about selection, trace source, attach note to project

### Notifications And Status

Copilot must show:

- backend connected/disconnected
- active queue count
- source converted
- duplicate detected
- conversion failed
- DIKIWI stage failed
- provider quota/auth/network failure
- external API used

### Artifact Navigation

Copilot must be able to open:

- `00-Chaos` source package
- `01-Data` artifact
- `02-Information` artifact
- `03-Knowledge` artifact
- duplicate source's existing artifact
- source lineage view

## Primary User Workflows

### Workflow 1: Add A Source And Read Markdown

1. User drops a PDF, PPTX, or URL into the configured source intake path, or
   invokes an Obsidian command to ingest the active file/URL.
2. Aily stores the source and computes content identity.
3. If the source is new, Aily converts it to high-fidelity Markdown.
4. Aily writes or updates the source package under `00-Chaos`.
5. The plugin shows conversion status and quality level.
6. User opens the converted Markdown instead of the original PDF/deck/link.

### Workflow 1B: Copilot-Directed Intake

1. User invokes `Aily: Ingest Active File`, `Aily: Ingest URL`, or a file
   explorer context menu action.
2. Aily Copilot submits the source to the backend through `/api/copilot/*`.
3. Copilot immediately creates or refreshes a source monitor row.
4. Copilot follows backend events or polling until the source reaches a terminal
   state.
5. User can open the resulting source package or inspect the failure.

### Workflow 2: Check Foundation Processing

1. User opens the source monitor.
2. User sees each source's `Chaos`, `Data`, `Information`, and `Knowledge`
   status.
3. User can filter for `Failed`, `Partial`, `Duplicate`, `Ready`, or
   `Unprocessed`.
4. User can retry, ignore, open, compare, or attach a source to a future
   project.

### Workflow 3: Duplicate Handling

1. User adds a duplicate file.
2. Aily detects content identity or canonical Markdown identity.
3. Aily does not regenerate downstream artifacts.
4. Plugin shows a duplicate notice and links to the existing source record.
5. User can open the existing source package or ignore the duplicate.

### Workflow 4: Grounded Chat

1. User asks a question from Chat.
2. Chat retrieves relevant processed sources and Knowledge notes.
3. Chat answers with citations and source previews.
4. If evidence is insufficient, Chat says so and suggests source/status actions.

### Workflow 4B: Project-Scoped Chat

1. User creates or opens a Project.
2. User attaches source records, folders, tags, or notes to the Project.
3. Chat retrieves within the Project scope by default.
4. Chat shows when answers are constrained by the Project scope.
5. User can remove the scope and return to whole-vault Chat.

### Workflow 5: Ready For Synthesis Signal

1. Aily may analyze changed sources and knowledge neighborhoods.
2. V2.0 may show "ready for I/W/I" recommendations.
3. V2.0 must not run I/W/I automatically.
4. The recommendation becomes an input to V2.1 approval-based synthesis.

### Workflow 6: Tool-Invoked End-To-End Value Workflow

1. User calls an Aily Copilot workflow tool and supplies a topic plus comment.
2. Copilot creates a workflow plan and shows the planned steps.
3. Backend searches the local knowledge base and Obsidian vault first.
4. Backend uses Tavily/deep research only for missing external context.
5. Backend runs Insight, Wisdom, and Impact from the evidence set.
6. Backend runs follow-up research, evaluation, and business-plan generation.
7. Backend generates dossier content under `10-Dossiers`.
8. Copilot shows progress, provider usage, evidence links, and final artifact
   links.
9. User can inspect the workflow run and source chain.

## Obsidian Surfaces

| Surface | V2.0 Requirement |
|---|---|
| Right sidebar Chat | grounded questions, citations, source previews, backend status errors |
| Left/sidebar Source Monitor | source queue, duplicate state, stage status, quality level, retry/open actions |
| Command palette | ingest active file, ingest URL, refresh source status, open source monitor |
| File explorer context menu | ingest selected file, show source status |
| Editor context menu | ask about selected text, trace source for generated artifact |
| Status bar | backend connection, active queue count, provider/cost warnings |
| Obsidian notices | duplicate, failed conversion, completed conversion, backend unavailable |
| Generated notes | frontmatter lineage, backlinks, generated-by marker |
| Graph view | content-based links only; no artificial central hub links |

## Functional Requirements

### R1. Source Intake

The system must support:

- watched folder intake
- manual ingest active file command
- manual ingest URL command
- plugin-triggered upload to Aily backend

Every accepted source must receive a stable `source_id`.

Acceptance:

- adding a representative source creates one source record
- adding the same source again does not create duplicate downstream artifacts
- source records survive backend restart

### R2. High-Fidelity `00-Chaos`

`00-Chaos` must contain source-equivalent Markdown packages, not loose text
dumps.

Required for V2.0:

| Source Type | V2.0 Requirement |
|---|---|
| PDF | preserve page order, headings where detectable, tables where possible, page references, useful page/slide images |
| PPTX | one Markdown section per slide, slide title, slide text, slide image asset for every slide |
| URL | title, canonical URL, capture timestamp, readable article body, source metadata |

Best-effort for V2.0:

| Source Type | V2.0 Best Effort |
|---|---|
| DOCX | headings, lists, tables, images where available |
| Video | transcript and timestamp sections when supported |

Each package must include:

- source ID
- original source path or URL
- content hash
- conversion timestamp
- conversion method
- asset references
- quality level
- link to source status record

### R3. Source-Equivalence Quality Contract

Every conversion must assign a quality level:

| Level | Meaning |
|---|---|
| `Equivalent` | good enough to replace the original for reading and processing |
| `Readable` | useful for reading, with minor missing structure or assets |
| `Partial` | usable for indexing but not sufficient for serious reading |
| `Failed` | conversion failed |
| `Unsupported` | source type not supported |

Quality score dimensions:

| Dimension | Weight |
|---|---:|
| content coverage | 30 |
| structure preservation | 20 |
| visual asset coverage | 20 |
| lineage and metadata | 15 |
| human readability | 15 |

Release gate:

- PDF and PPTX samples must reach `Equivalent` or `Readable`.
- Any `Partial`, `Failed`, or `Unsupported` sample must show a clear user action
  in the source monitor.

### R4. Data / Information / Knowledge Foundation

Every accepted new source must automatically process through:

```text
00-Chaos -> 01-Data -> 02-Information -> 03-Knowledge
```

The process must be idempotent.

Requirements:

- repeated processing of the same source must reconcile existing artifacts
- each generated artifact must reference its source ID
- stage status must be durable
- failures must include a recoverable error message

### R5. Product-Grade Source Status API

V2.0 must expose source status through a plugin-safe backend contract.

Minimum `SourceStatus` fields:

```text
source_id
display_title
source_type
original_path_or_url
content_hash
canonical_markdown_hash
duplicate_of_source_id
conversion_status
conversion_quality_level
source_equivalence_score
dikiwi_data_status
dikiwi_information_status
dikiwi_knowledge_status
artifact_paths
workflow_run_ids
last_error
last_processed_at
next_action
provider_usage
```

Minimum endpoints:

```text
GET  /api/copilot/sources
GET  /api/copilot/sources/{source_id}
POST /api/copilot/sources/{source_id}/retry
POST /api/copilot/sources/{source_id}/ignore
GET  /api/copilot/sources/duplicates
```

Status values:

```text
queued
running
ready
duplicate
partial
failed
ignored
stale
unsupported
```

### R6. Source Monitor UI

The plugin must provide a source monitor view.

Minimum columns:

| Column | Meaning |
|---|---|
| Source | title/path/type |
| Quality | Equivalent/Readable/Partial/Failed/Unsupported |
| Chaos | conversion package status |
| Data | Data stage status |
| Information | Information stage status |
| Knowledge | Knowledge stage status |
| Duplicate | duplicate source link if applicable |
| Last Run | latest processing time |
| Action | open, retry, ignore, compare, view evidence |

Minimum filters:

- all
- ready
- processing
- failed
- duplicate
- partial
- unsupported
- unprocessed

### R7. Grounded Chat

Chat is the default user mode.

Requirements:

- query processed sources and Knowledge notes
- show citations/source previews
- indicate when evidence is insufficient
- allow "open source" from answer citations
- show backend unavailable/provider failure clearly

Acceptance:

- a question answerable from processed source material cites the source
- a question not supported by the vault does not invent an answer

### R8. Artifact Naming And Lineage

Every generated artifact must include frontmatter.

Minimum frontmatter:

```yaml
---
aily_generated: true
artifact_type: chaos|data|information|knowledge
source_ids:
  - src_...
workflow_run_id: wf_...
created_by: aily
created_at: 2026-05-23T00:00:00Z
lineage:
  chaos: 00-Chaos/example.md
  data: 01-Data/example.md
  information: 02-Information/example.md
  knowledge: 03-Knowledge/example.md
---
```

Rules:

- deterministic filenames
- no SHA-only user-facing note titles
- generated notes backlink to source packages
- regeneration must update managed artifacts or create versioned artifacts
- user-authored notes must not be silently overwritten

### R9. Trust, Privacy, And Cost Controls

The plugin must expose:

- backend connection state
- provider used for each external LLM run
- whether source text was sent to an external API
- Tavily usage when web research is invoked
- failure reasons for provider quota/auth/network errors
- token/cost estimate for expensive future workflows where available

V2.0 defaults:

- Deep Research is not automatic
- I/W/I is not automatic
- business-plan generation is not automatic
- real email sending is forbidden
- API keys must never be written into generated notes

### R10. Readiness Signals

V2.0 may calculate readiness for future I/W/I, but it must not perform
expensive synthesis automatically.

A readiness signal may include:

- changed source count
- affected topics
- evidence density
- stale Knowledge notes
- unresolved conversion failures
- estimated run cost
- suggested project scope

### R11. Post-Knowledge Tool Boundary

All stages after Knowledge must be initiated by Aily Copilot tools and explicit
human instruction.

Post-Knowledge stages:

```text
Insight
Wisdom
Impact
Research
Evaluation
Business Plan
Dossier
```

Requirements:

- no post-Knowledge workflow may auto-run from file intake alone
- no post-Knowledge workflow may auto-run from a graph-change threshold alone
- user instruction must be recorded with the workflow run
- workflow scope must be visible before execution
- generated artifacts must be linked back to source IDs and prior stage IDs
- failed or partial workflows must be visible in Aily Copilot

### R12. Workflow Orchestration Contract

Post-Knowledge tools must be orchestratable from Aily Copilot.

Minimum backend contract:

```text
POST /api/copilot/workflows/plan
POST /api/copilot/workflows/run
GET  /api/copilot/workflows/{workflow_run_id}
GET  /api/copilot/workflows/{workflow_run_id}/events
POST /api/copilot/workflows/{workflow_run_id}/cancel
```

Minimum workflow request:

```json
{
  "tool": "run_end_to_end_value_workflow",
  "topic": "string",
  "comment": "string",
  "scope": {
    "project_id": "optional",
    "source_ids": [],
    "note_paths": [],
    "folders": [],
    "tags": []
  },
  "requested_outputs": [
    "insight",
    "wisdom",
    "impact",
    "research",
    "evaluation",
    "business_plan",
    "dossier"
  ]
}
```

Minimum workflow response:

```json
{
  "workflow_run_id": "wf_...",
  "status": "planned|running|waiting_for_confirmation|completed|failed|cancelled",
  "topic": "string",
  "comment": "string",
  "planned_steps": [],
  "current_step": "string",
  "evidence_summary": {},
  "provider_usage": {},
  "artifact_paths": []
}
```

The first workflow step must always be local knowledge-base and Obsidian vault
search. Tavily/deep research may supplement that evidence, but must not replace
it.

## Testing Requirements

V2.0 tests must use real representative source files, not mocked source
content.

Representative source set:

- at least 3 PDFs
- at least 2 PPTX files, if available
- at least 2 URLs
- at least 1 intentional duplicate

The source set may be selected from:

```text
/Users/luzi/aily_chaos/pdf
```

or another user-approved source folder.

## V2.0 Quality Gates

### Gate 0: Configuration

Evidence required:

- backend URL configured
- vault path configured
- source intake path configured
- DeepSeek/Kimi/Tavily settings visible but secrets redacted
- plugin installed in the iCloud vault

Pass condition:

- user can open Obsidian and see Aily Copilot connected to the backend.

### Gate 1: Source Intake And Duplicate Detection

Evidence required:

- source records for selected files/URLs
- content hashes
- duplicate source record
- duplicate user notification/log event

Pass condition:

- duplicate input does not regenerate downstream artifacts.

### Gate 2: High-Fidelity `00-Chaos`

Evidence required:

- converted Markdown packages
- slide/page images for PPTX/PDF samples where required
- quality level and score
- human readability review

Pass condition:

- representative PDF/PPTX outputs are readable without opening originals.

### Gate 3: Data / Information / Knowledge

Evidence required:

- Data artifacts
- Information artifacts
- Knowledge artifacts
- source IDs and lineage frontmatter
- workflow or job records

Pass condition:

- each accepted new source reaches Knowledge or shows a recoverable failure.

### Gate 4: Source Monitor

Evidence required:

- plugin source monitor screenshot or structured UI output
- status API responses
- retry/ignore/open actions visible
- failed/duplicate/ready filters

Pass condition:

- user can tell which sources reached Knowledge and what action is needed for
  failures.

### Gate 5: Grounded Chat

Evidence required:

- chat question based on processed source
- cited answer
- source preview/open-source path
- unsupported-question behavior

Pass condition:

- Chat answers from vault evidence and refuses unsupported claims.

### Gate 6: Trust And Cost Visibility

Evidence required:

- provider usage metadata
- redacted secret handling
- external API usage indicator
- provider failure handling, if simulated by invalid config

Pass condition:

- user can see which external services were used without exposing secrets.

### Gate 7: Aily Copilot End-To-End Operation

Evidence required:

- Copilot command palette entries for source intake and source monitor
- Copilot context menu entries for active/selected files
- source monitor populated from real `/api/copilot/*` backend state
- Chat opens source citations and generated artifacts
- Project source workspace can scope grounded Chat without running V2.1+
  synthesis
- post-Knowledge tool controls are either available with human confirmation or
  clearly marked as future/disabled without auto-running

Pass condition:

- a user can operate the complete V2.0 source-foundation workflow from Aily
  Copilot without reading backend logs or manually calling backend endpoints.

## Development Order

1. SourceStatus backend contract.
2. source-equivalent Markdown quality fields and package metadata.
3. duplicate detection API and notification path.
4. D/I/K stage status reconciliation.
5. Copilot API client coverage for all V2.0 product endpoints.
6. source monitor plugin view.
7. Copilot commands, context menus, notices, status bar, and artifact open
   actions.
8. Project source workspace and project-scoped Chat retrieval.
9. grounded Chat citation polish.
10. real-path test harness and gate evidence scripts.

## Required Future Documents Before V2.1

- Aily V2.1 DIKIWI synthesis requirements
- Aily V2.2 Projects and business artifact requirements
- Aily V2.3 dossier and reader-experience requirements

## Non-Negotiable Acceptance Principle

A V2.0 feature is complete only when the user can observe:

```text
source
  -> source-equivalent Markdown
  -> Data
  -> Information
  -> Knowledge
  -> grounded answer or visible failure
```

File existence is not enough. The artifacts must be readable, source-linked,
idempotent, and inspectable from Obsidian.

A backend-only implementation is not complete. V2.0 acceptance requires that
the same capability is operable from Aily Copilot with visible status,
recoverable failures, and artifact navigation.
