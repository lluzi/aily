# Aily V2 Product Definition

Origin: Created by Codex lead agent on 2026-05-23 from user product direction in the Aily development thread.

## Purpose

Aily V2 is an Obsidian-native intelligence product.

The product is not "another AI chat plugin." The product is an Obsidian plugin
that turns Aily's backend into a serious information-processing, knowledge
formation, and value-realization system inside the user's working vault.

The plugin should feel as convenient as an everyday Copilot surface, while the
backend should behave like a disciplined knowledge factory:

```text
source material
  -> source-equivalent Markdown
  -> Data
  -> Information
  -> Knowledge
  -> Insight / Wisdom / Impact
  -> business plan / evaluation
  -> dossier
```

## Product Positioning

Aily V2 sits between three product categories:

1. Obsidian AI chat plugins.
2. AI note organization and autocomplete plugins.
3. Research, analysis, and business-planning systems.

Most Obsidian AI plugins focus on local interaction surfaces:

- Companion focuses on AI autocomplete through a Copilot-like writing interface:
  <https://github.com/rizerphe/obsidian-companion>
- AI Companion focuses on `/ai` note-level Q&A with page context:
  <https://community.obsidian.md/plugins/ai-companion>
- Note Companion emphasizes vault organization, chat with notes, YouTube
  transcription, and file operations:
  <https://www.notecompanion.ai/>
- Caret emphasizes local-first AI chat, AI Canvas, vault references, and keeping
  generated data as Markdown:
  <https://www.caretplugin.ai/>

Aily should learn from those interaction patterns, but it should not compete
only on chat, autocomplete, or note cleanup. Aily's differentiated claim is:

> Aily is an Obsidian-native knowledge production system that converts source
> material into a tracked knowledge foundation, then uses that foundation to
> produce insight, wisdom, impact, business plans, and human-readable dossiers.

## V2 North Star

Aily V2 replaces manual document hunting, PDF opening, slide scanning, and
fragmented AI prompting with a single Obsidian-native flow:

1. Collect source material.
2. Convert it into high-quality Markdown.
3. Promote it into Data, Information, and Knowledge.
4. Let users read, ask, and select topics naturally from Obsidian.
5. Trigger deeper reasoning only when it has a topic, motive, and evidence set.
6. Produce business-value artifacts that explain what was generated and why.

V2.0 implementation scope is intentionally narrower than the full V2 vision.
The first release contract is `docs/AILY_V2_0_REQUIREMENTS.md`: source intake,
high-fidelity `00-Chaos`, duplicate/status monitoring, automatic
Data/Information/Knowledge, and grounded Chat. Approval-based I/W/I, Projects,
business plans, and dossiers move into V2.1+ unless explicitly pulled forward
through a new decision.

The automation boundary is:

```text
Automatic foundation:
  Data -> Information -> Knowledge

Human-controlled Aily Copilot tools:
  Insight -> Wisdom -> Impact -> Research -> Evaluation
  -> Business Plan -> Dossier
```

The post-Knowledge stages must remain under human control. Aily may recommend
that a topic or project is ready for deeper synthesis, but execution happens
only through explicit Aily Copilot tool invocation.

## Core User Promise

Aily Copilot helps the user say:

- "I no longer open random PDFs and slides one by one."
- "Every important source becomes readable Markdown in my vault."
- "I can see whether each source has been processed into Data, Information, and
  Knowledge."
- "I can ask Aily to reason from my vault, not from vague model memory."
- "When Aily produces a business plan, I can inspect the source chain and read a
  dossier explaining the result."

## Product Surface

### 1. Aily Copilot Plugin

The Obsidian plugin is the primary product UI.

Required modes:

| Mode | Purpose | Default |
|---|---|---|
| `Chat` | Everyday Copilot interaction, grounded vault chat, source previews, lightweight help. | Yes |
| `DIKIWI` | Knowledge-foundation operations: source status, Data/Information/Knowledge retrieval, I/W/I triggers. | No |
| `Projects` | Focused execution for a product, proposal, research program, or business-plan task. | No |

The plugin should stay thin. It should present controls, context, approvals,
citations, and progress. Aily backend owns source processing, durable state,
workflow execution, lineage, deduplication, and artifact generation.

### 2. Aily Backend

The backend is the product engine.

It must provide:

- source ingestion
- high-fidelity document conversion
- duplicate detection
- status tracking
- DIKIWI stage promotion
- graph-change monitoring
- topic search and context selection
- Insight/Wisdom/Impact execution
- Deep Research augmentation
- business-plan synthesis
- dossier generation
- event logs and audit trails

### 3. Obsidian Vault

The vault is the human-readable product database.

The iCloud test/user vault is:

```text
/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily
```

The vault layout remains:

```text
00-Chaos
01-Data
02-Information
03-Knowledge
04-Insight
05-Wisdom
06-Impact
07-Research
08-Evaluations
09-Business-Plans
10-Dossiers
99-MOC
99-System
```

## Functional Definition

### F1. Source Aggregation And High-Fidelity Markdown

`00-Chaos` becomes the final information aggregator.

From V2 onward, documents in `00-Chaos` should be high-quality,
source-equivalent Markdown. This is not a loose text extraction folder.

Supported inputs should include:

- PDFs
- PowerPoint decks
- Word documents
- web URLs
- videos and transcripts
- notes and pasted text

Required output quality:

- the Markdown preserves the actual source structure
- slide decks include every slide's meaningful content
- slide/PDF visual screenshots are attached or linked where needed
- headings, tables, bullets, captions, and references remain readable
- the original source hash and source-store ID are recorded
- converted Markdown is good enough for serious long-form reading

The user should be able to choose an article, topic, or document inside the
vault instead of manually opening PDFs, decks, or browser tabs.

### F2. Source Perception, Deduplication, And Status

Aily must have excellent perception of every input document before promoting it.

For every source, Aily should track:

- source ID
- original path or URL
- source type
- content hash
- extracted text hash
- canonical Markdown hash
- conversion status
- DIKIWI status
- duplicate status
- source-equivalence score
- last processed time
- next required action

If a duplicate document enters the monitored folder, Aily must not regenerate
all downstream artifacts. It should notify the user that the source is a
duplicate and show the existing source record and generated artifacts.

### F3. Automatic Data / Information / Knowledge Foundation

Every accepted source in `00-Chaos` should automatically move through:

```text
00-Chaos -> 01-Data -> 02-Information -> 03-Knowledge
```

This process must be idempotent. Re-running the same source should reconcile
status, not create redundant knowledge.

The plugin must expose a monitorable status view:

| Source | Chaos | Data | Information | Knowledge | Duplicate | Last Run | Action |
|---|---|---|---|---|---|---|---|

The purpose is operational trust: the user needs to know which valuable
documents have become usable knowledge and which ones are blocked.

### F4. Knowledge Network Monitoring

The knowledge foundation is not just a pile of notes. It is a monitored network.

Aily must track graph change from:

- new source nodes
- new Data nodes
- new Information nodes
- new Knowledge nodes
- changed links
- changed tags
- changed typed relationships
- changed source lineage

Knowledge network monitoring may produce readiness signals for later synthesis:

- changed topics
- affected source groups
- evidence density
- stale artifacts
- unresolved gaps
- estimated cost

These signals do not execute Insight/Wisdom/Impact by themselves. They inform
human-controlled Aily Copilot tools.

### F5. Insight / Wisdom / Impact Execution

Insight, Wisdom, and Impact are human-controlled workflows.

- user invokes a tool or command from Copilot
- user includes a comment that acts as the task brief for the requested topic
- user supplies topic, question, selected note, folder, tag, or project
- plugin converts the request into search/retrieval commands
- backend retrieves relevant Obsidian context
- DIKIWI engine performs Insight, Wisdom, and Impact synthesis
- outputs are written to `04-Insight`, `05-Wisdom`, and `06-Impact`

Scheduled jobs and graph-change analysis may prepare candidate recommendations,
but they must not write I/W/I artifacts without explicit user instruction.

Required Copilot commands:

- `Run DIKIWI on this topic`
- `Run DIKIWI on selected note`
- `Run DIKIWI on folder`
- `Explain why this topic is ready for I/W/I`
- `Show evidence used for this Insight`
- `Show unresolved evidence gaps`
- `Run Research on this topic`
- `Run Evaluation on this artifact`
- `Generate Business Plan from this project`
- `Generate Dossier from this plan`
- `Run End-To-End Value Workflow`

The end-to-end value workflow must begin with local knowledge-base and Obsidian
vault search, then use Tavily/deep research for external context, then run
Insight/Wisdom/Impact, evaluation, business-plan generation, and dossier
generation from the resulting evidence chain.

### F6. Business Plan And Evaluation

Impact is not the final user value. It is the input to value realization.

After Impact, Projects mode should support:

- formal business plans
- technical proposals
- product design proposals
- investment or commercial evaluations
- custom executive documents
- technical documentation

Business-plan generation must combine:

- DIKIWI Knowledge
- Insight/Wisdom/Impact artifacts
- project scope
- user motive
- Deep Research results when needed
- specialist evaluation outputs

Every business plan must include:

- thesis
- evidence base
- assumptions
- market and customer logic
- technical feasibility
- commercial feasibility
- risks
- unresolved questions
- next actions
- source lineage

### F7. Dossier As The Final Reader Artifact

`10-Dossiers` is the final learning and explanation layer.

The dossier is not an email draft and not a short memo. It is a deep, readable
artifact for human learning and decision support.

Every dossier should answer:

- What is this plan or insight about?
- Why did Aily generate it?
- Which sources were used?
- Which DIKIWI stages contributed?
- What did Deep Research add?
- What claims are strongly supported?
- What claims are tentative?
- What should a human reader learn from it?
- What decisions or actions does this enable?

The dossier must be more readable than raw stage outputs. It should feel like a
conversation with a senior domain expert over coffee: high-level, dense,
evidence-backed, and written for a human reader.

### F8. Aily Kiosk Reading Workflow

Aily needs a reading workflow for serious long-form consumption.

The user should be able to:

- browse recently converted high-quality Markdown in `00-Chaos`
- filter by date, source type, topic, project, and processing status
- open source-equivalent Markdown instead of original PDFs/decks
- see the source's DIKIWI promotion status
- send the source into a topic workflow
- ask Chat to explain, compare, or summarize it with citations

This can initially be implemented as Obsidian commands and sidebar panels. A
dedicated visual interface can come later.

## Required Plugin Tools

### Chat Tools

- search vault
- read note
- explain source
- show citations
- trace claim
- open generated artifact

### DIKIWI Tools

- ingest selected file
- show source status
- run Data/Information/Knowledge
- run Insight/Wisdom/Impact on topic
- show graph delta
- show stage lineage
- show duplicate record

### Project Tools

- create project
- set project scope
- attach selected sources
- run Deep Research
- generate business plan
- generate technical proposal
- generate dossier
- show review/evaluation results

### Chaos Tools

- list recent source-equivalent Markdown
- filter unprocessed sources
- filter duplicates
- filter source conversion failures
- open source package
- compare source to converted Markdown

## Settings Needed For V2

### Common

- Aily backend URL
- Aily backend token
- default mode
- default chat provider
- default dossier provider
- Tavily API settings

### DIKIWI

- automatic trigger on new files
- watched folders
- excluded folders
- allowed source types
- daily I/W/I schedule
- graph-change trigger threshold
- duplicate notification behavior
- retry policy for failed conversions
- source-equivalence quality threshold

### Projects

- project root folder
- default business-plan template
- default technical-document template
- Deep Research enabled by default
- specialist evaluation enabled by default
- dossier generation enabled after plan

## Roadmap From V1 To V2

### V2.0-A: Product Reframing

Deliverables:

- V2 product definition
- mode taxonomy: Chat, DIKIWI, Projects
- settings taxonomy
- updated development plan

Gate:

- Aily is described as an Obsidian-native knowledge production product, not as a
  standalone bot or generic chat plugin.

### V2.0-B: High-Fidelity `00-Chaos`

Deliverables:

- source-equivalent Markdown converter contract
- slide/PDF screenshot asset convention
- source package metadata
- duplicate source notification
- status UI/API for conversion quality

Gate:

- a human can read converted Markdown instead of opening the original PDF/deck
  for a representative sample.

### V2.0-C: Foundation Monitor

Deliverables:

- source status API
- stage status API
- plugin status table
- retry/resume action
- duplicate lookup

Gate:

- user can tell which `00-Chaos` files reached Knowledge and which did not.

### V2.0-D: DIKIWI Tool Layer

Deliverables:

- topic-to-search command planner
- `Run DIKIWI on topic`
- selected-note/folder/tag workflow triggers
- graph delta display
- I/W/I lineage display

Gate:

- user can trigger I/W/I from Copilot and inspect evidence used.

### V2.0-E: Projects Value Realization

Deliverables:

- project-scoped DIKIWI context
- Deep Research packet integration
- business-plan generator
- technical-document generator
- evaluation panel

Gate:

- project produces a substantive business plan or technical proposal with
  source lineage and unresolved assumptions.

### V2.0-F: Dossier Layer

Deliverables:

- dossier generation after plan/evaluation
- claim-to-evidence table
- creation history section
- reader-oriented narrative section
- quality scoring

Gate:

- dossier is readable as a deep learning artifact and explains how the plan was
  created.

### V2.0-G: Product Polish

Deliverables:

- Kiosk browsing commands/panel
- progress notifications
- failure recovery UX
- concise onboarding
- settings simplification

Gate:

- a real user can ingest, monitor, reason, generate, and review without reading
  backend logs.

## V2 Non-Goals

- Aily should not become a generic replacement for every Obsidian AI plugin.
- Aily should not auto-run expensive I/W/I or business planning without motive,
  schedule, or graph-change trigger.
- Aily should not silently edit user-authored notes.
- Aily should not treat Deep Research as more authoritative than the vault.
- Aily should not create central graph nodes that exist only as artificial hubs.

## Acceptance Principle

A V2 feature is complete only when a user can observe the evidence chain:

```text
source
  -> source-equivalent Markdown
  -> Data
  -> Information
  -> Knowledge
  -> I/W/I, if triggered
  -> business plan, if requested
  -> dossier, if requested
```

Completion cannot be claimed from file existence alone. The generated artifacts
must be readable, substantive, source-linked, and inspectable from Obsidian.
