# Aily V1.0 Upgrade Development Plan

Date: 2026-05-17

## North Star

Aily transforms scattered, trivial knowledge into fully justified business plans.

This is the V1.0 product rule. Every architecture change should make that rule
more reliable, more traceable, or cheaper to execute.

## Authority Of This Document

This document is the authoritative development contract for Aily V1.0.

It is not a vision note, idea backlog, or loose product sketch. Future
implementation work should treat it as the primary guide for architecture,
scope, sequencing, acceptance, and migration.

How to use it:

- If a code change affects V1 behavior, it must map to a section, phase, or
  acceptance criterion in this document.
- If a proposed change conflicts with this document, update this document first
  with the rationale before changing code.
- If a feature is not described here, it is outside V1 scope unless explicitly
  added through the change-control process.
- If implementation discovers that a planned design is wrong, the correction
  must be recorded here as a new decision, not hidden only in code.
- If there is a conflict between older Aily docs and this plan, this plan wins
  for V1 unless the conflict is explicitly resolved.

Requirement language:

- `MUST` means required for V1 acceptance.
- `SHOULD` means expected unless there is a documented tradeoff.
- `MAY` means optional and must not block V1.
- `MUST NOT` means prohibited without revising this plan first.

Development rule:

No autonomous implementation agent should make broad architectural changes
without first checking whether the change preserves this document's North Star,
Core Design Principles, phase sequence, migration strategy, and Definition of
Done.

## V1.0 Product Definition

Aily V1.0 is a private, always-on knowledge and venture-planning system.

It should:

1. Continuously ingest documents, videos, links, notes, and other source files
   from a watched inbox.
2. Convert every source into a canonical Markdown package.
3. Automatically run DIKIWI's foundation stages:
   `Data -> Information -> Knowledge`.
4. Stop after Knowledge by default.
5. Provide a simple chat window where the user explains motives, goals, and
   desired synthesis tasks in natural language.
6. Use a backend Aily agent to extract topics, select Obsidian/Knowledge
   context, orchestrate prompts, and dispatch the next process.
7. Let the user manually trigger a topic-specific synthesis process:
   `Insight -> Wisdom -> Impact`.
8. Let the user attach external business plans, reports, decks, or memos as
   "second opinion" references for the triggered workflow.
9. Use Deep Research when internal knowledge is not broad or deep enough.
10. Route the Impact summary into three specialized teams:
   Technical Innovation, Engineering Assessment, and Commercial Feasibility.
11. Merge the three team outputs into a comprehensive, evidence-backed business
   plan.
12. Store all generated documents in Obsidian as the fundamental knowledge-base
   and document-management layer.
13. Export selected Obsidian Markdown documents to PDF or DOCX and deliver them
    by email as Aily's formal outbound dissemination channel.
14. Keep Aily's dedicated GUI focused on IM-style input, attachments, status,
    confirmations, and handoff links; use Obsidian for rich document browsing.

## Strategic Architecture Change

V1.0 keeps the DIKIWI name and identity, but changes the execution model.

V1.0 also standardizes the orchestration runtime on LangGraph. Aily should not
rebuild a generic agent framework from scratch. Instead, Aily should use
LangGraph for durable graph execution, checkpoints, resumable runs,
human-in-the-loop approvals, and state inspection, while keeping Aily's own
database, Obsidian vault, DIKIWI stages, research packets, and business-plan
artifacts as the product source of truth.

Current effective model:

```text
Input
  -> Data
  -> Information
  -> Knowledge
  -> Insight
  -> Wisdom
  -> Impact
  -> Reactor / Residual / Entrepreneur / Guru
```

V1.0 target model:

```text
Always-on Knowledge Foundation:

Watched Inbox
  -> Source Store
  -> Canonical Markdown
  -> Data
  -> Information
  -> Knowledge
  -> Stop

Triggered Business Planning:

Chat Motive / User Goal
  -> Aily Orchestrator Agent
  -> Topic Extraction
  -> Obsidian / Knowledge Search
  -> Insight
  -> Wisdom
  -> Impact Summary
  -> Optional Second Opinion Packet
  -> Deep Research Packets
  -> Technical Innovation Review
  -> Engineering Assessment
  -> Commercial Feasibility Review
  -> Comprehensive Business Plan
  -> Obsidian Document Vault
  -> PDF / DOCX Export
  -> Email Delivery
```

The important shift is that `Data`, `Information`, and `Knowledge` are the
durable knowledge foundation. `Insight`, `Wisdom`, and `Impact` are triggered
contextual processes, not mandatory ingestion stages.

## Core Design Principles

1. DIKIWI remains the defining product feature.
2. Ingestion must be cheap, durable, and automatic.
3. Expensive reasoning must be intentional and trigger-based.
4. Every generated claim must keep source lineage.
5. External research supplements Aily's knowledge, but never replaces Aily's
   source ledger.
6. A failed synthesis job must not imply failed ingestion.
7. The final product outcome is a business plan, not merely a note collection.
8. Obsidian remains the canonical human-readable document vault for generated
   Aily output.
9. Email delivery is the official outlet for sharing polished plans and reports
   outside Aily.
10. User motives should be captured as prompts, not hidden configuration.
11. Status should appear where the user works: in chat first, then Studio and
    Obsidian status documents.
12. LangGraph is the V1 orchestration runtime, but Aily remains the product
    owner of state, IDs, permissions, Obsidian documents, and evidence.
13. User-attached second-opinion files are independent references, not assumed
    truth.

## Non-Negotiable V1 Requirements

These requirements are not optional polish. They define whether Aily V1.0 is
the intended product.

### Product Requirements

- Aily MUST transform scattered knowledge into justified business plans.
- Aily MUST keep DIKIWI as the named methodology and product identity.
- Aily MUST automatically process new sources only through Data, Information,
  and Knowledge.
- Aily MUST keep Insight, Wisdom, Impact, Deep Research, specialist evaluation,
  business-plan synthesis, export, and email behind explicit user intent or
  approval.
- Aily MUST use Obsidian as the canonical human-readable vault for generated
  documents.
- Aily MUST provide an IM-style GUI for user input, attachments, status prompts,
  approvals, workflow summaries, and links to Obsidian.
- Aily MUST support second-opinion attachments as non-authoritative reference
  material.

### Technical Requirements

- Aily MUST use LangGraph as the V1 workflow orchestration runtime.
- Aily MUST keep Aily-managed SQLite/domain records as the canonical product
  state.
- Aily MUST treat LangGraph checkpoints as resumable execution state, not as the
  business record.
- Aily MUST preserve stable IDs across source, canonical Markdown, DIKIWI,
  workflow, research, evaluation, business-plan, Obsidian, export, and email
  records.
- Aily MUST make side-effecting workflow nodes idempotent.
- Aily MUST not send real email without explicit approval.
- Aily MUST not commit API keys or secrets.

### Evidence Requirements

- Aily MUST produce real evidence manifests for acceptance claims.
- Mocked LLM responses, fake vault writes, fake browser events, or fake graph
  events MUST NOT be used as product acceptance evidence.
- Every V1 release candidate MUST prove a real-path run through ingestion,
  Knowledge, chat-triggered planning, Obsidian output, export, and email dry-run.

## Change Control

This plan is expected to evolve, but changes must be deliberate.

Permitted changes:

- clarifying requirements without changing behavior
- adding implementation details under an existing phase
- replacing a tool when the replacement preserves the same product contract
- tightening acceptance criteria

Changes requiring explicit plan revision:

- changing the DIKIWI execution split
- removing Obsidian from the canonical document path
- replacing LangGraph as the orchestration runtime
- making Insight/Wisdom/Impact automatic again
- allowing real email sends without manual approval
- treating second-opinion files or Deep Research as unquestioned truth
- adding a large GUI surface that competes with Obsidian as document browser

Every major revision should add a short decision note:

```text
Decision:
Reason:
Alternatives considered:
Migration impact:
Acceptance impact:
```

## Current Locked Decisions

These decisions are accepted for V1 and should not be re-litigated during normal
implementation.

| Decision | Status | Rationale |
|---|---|---|
| Keep the `DIKIWI` name | Locked | The name is part of Aily's defining product identity. |
| Split automatic and triggered work | Locked | Data/Information/Knowledge are durable foundation; Insight/Wisdom/Impact require user motive. |
| Use LangGraph for orchestration | Locked | Durable workflows, checkpoints, resume, and human-in-the-loop behavior are core V1 needs. |
| Keep Aily domain records canonical | Locked | LangGraph checkpoints are execution state, not the business/product record. |
| Keep Obsidian as document vault | Locked | Obsidian is the canonical human-readable knowledge base and generated-document store. |
| Reserve `00-Chaos` | Locked | `00-Chaos` is for raw source material, extracted assets, and canonical Markdown only. |
| Use IM-style Aily GUI | Locked | Aily needs a lightweight input/control surface; Obsidian remains the document browser. |
| Treat second opinions as non-authoritative | Locked | Attached external plans inform comparison but cannot replace evidence. |
| Require email approval | Locked | Outbound dissemination is high-risk and must default to preview/manual approval. |

## V1.0 System Components

### 1. Watched Inbox

Purpose:

Provide a simple local-first input mechanism. The user drops files into a
directory, and Aily discovers them automatically.

Supported source types:

- PDF
- Office documents
- Markdown
- plain text
- images
- audio
- video
- URL link files
- future cloud/object-storage pointers

Target behavior:

- A monitor scans the inbox every few seconds.
- Each new source is registered in `SourceStore`.
- The monitor only creates durable source records and jobs.
- The monitor never runs DIKIWI inline.

Recommended paths:

```text
~/Aily/Inbox/
  files/
  links/
  media/
  processed/
  failed/
```

Implementation notes:

- Reuse the existing `aily/source_store/store.py`.
- Extend it for inbox provenance and canonical Markdown artifacts.
- Use file hash plus path metadata for deduplication.
- Treat URL files as sources. A `.url`, `.webloc`, or small `.md` file can
  contain the URL and optional user notes.

Acceptance criteria:

- Dropping a file creates a durable source record.
- Restarting Aily does not lose the source.
- Re-dropping the same file deduplicates by content hash.
- Invalid sources move to a visible failed state with a reason.

### 2. Aily Agent Chat And Status Console

Purpose:

Give the user one lightweight conversational control surface for motives,
status, and triggered synthesis. Aily should not require the user to manually
assemble technical API calls. The user should be able to say what they are
trying to do, and the backend agent should translate that motive into a
traceable topic-specific workflow.

The dedicated GUI should feel like an instant-messaging tool rather than a
large document-management application. Obsidian remains the place to browse,
read, and edit generated documents. Aily's GUI is the entry point for user input,
attachments, status prompts, confirmations, and links into Obsidian.

V1 GUI shape:

```text
Left rail:
  - current chat thread
  - recent workflow runs
  - inbox/status badge

Center:
  - chat timeline
  - user messages
  - attached file cards
  - Aily status prompts
  - workflow-plan cards
  - approval cards

Right panel:
  - current system status
  - active jobs
  - attached references
  - Obsidian document links
  - recent errors / attention items
```

Primary interaction:

```text
User motive / prompt + optional attached reference files
  -> chat message
  -> Aily Orchestrator Agent
  -> topic extraction
  -> Obsidian / Knowledge search
  -> optional second-opinion extraction
  -> proposed workflow plan
  -> user confirmation when cost or outbound effects are involved
  -> I/W/I, Deep Research, team evaluation, business-plan, export, or email job
```

The chat window should also show prompt-based notifications from the system:

- "Aily detected 3 new files and is converting them to Markdown."
- "Knowledge processing is complete for source X."
- "I found 4 candidate topics related to your motive."
- "This Deep Research run may consume Tavily quota. Confirm?"
- "The business plan is ready in Obsidian."
- "An email draft is ready for approval."

Chat input capabilities:

- plain text message
- file attachment
- URL attachment
- optional short instruction per attachment
- send / cancel
- confirm / reject / revise workflow plan
- approve / reject email send

The chat does not need to render every generated artifact in full. It should
summarize progress, show decisions, and provide links that open the relevant
Obsidian document.

The chat is not only a support UI. It is the command layer for triggered work.

Backend agent responsibilities:

- maintain conversation context
- extract candidate topics and goals
- register and classify attached files
- classify user intent as ingestion, search, I/W/I, research, evaluation,
  business-plan generation, export, email, or operations
- search Obsidian and/or the Knowledge graph for relevant context
- extract second-opinion summaries from user-attached reference files
- propose the next workflow before expensive or external actions
- compose role-specific prompts for I/W/I, Deep Research, and specialist teams
- write every accepted prompt, decision, and notification to durable history

Important distinction:

- The chat agent orchestrates work.
- DIKIWI stage agents generate stage outputs.
- Specialist team agents evaluate ideas.
- Export/email workers package and deliver documents.

Aily may internally become a small team of agents, but V1 should expose one
coherent Aily persona to the user.

Status model:

```text
idle
scanning
converting
processing_knowledge
awaiting_prompt
planning_synthesis
researching
evaluating
generating_plan
exporting
awaiting_approval
sending
attention
error
stopped
```

Status surfaces:

- chat notifications as the primary user-facing surface
- Studio status panel for detailed queue/job inspection
- Obsidian status note for persistent local visibility
- optional macOS menu bar or tray indicator later

The Obsidian status note must not use a `00-*` folder because `00-Chaos` is
already reserved for source material and canonical Markdown. Use a separate
system namespace such as `99-System/Aily Status.md`.

Acceptance criteria:

- The user can describe a motive in chat and receive extracted topics.
- The user can attach a file to a chat/task prompt.
- Attached second-opinion files are converted, stored, and linked without being
  treated as authoritative truth.
- Aily can search Obsidian/Knowledge context from those topics.
- Aily proposes an I/W/I or research workflow before running expensive work.
- Status notifications appear in the chat for ingestion and triggered jobs.
- Generated documents can be opened from chat/status links in Obsidian.
- The GUI can remain useful even if it only shows summaries, statuses, and
  links rather than full generated documents.
- Costly research and outbound email require explicit confirmation.
- Prompt, topic, decision, and job IDs are durably linked.

### 3. LangGraph Orchestration Runtime

Purpose:

Use LangGraph as Aily V1's durable agent/workflow runtime. This gives Aily a
production-grade execution spine without copying external coding agents. Aily
should define domain-specific graphs and nodes; LangGraph should provide
checkpointing, resumability, human interrupts, branching, and state inspection.

Core principle:

```text
LangGraph owns workflow execution mechanics.
Aily owns product state, IDs, permissions, artifacts, and evidence.
```

Recommended V1 graphs:

1. `SourceFoundationGraph`
   - automatic
   - started by inbox watcher, upload, URL, or text input
   - converts source to canonical Markdown
   - runs Data, Information, Knowledge
   - stops after Knowledge
2. `BusinessPlanningGraph`
   - triggered by chat motive or explicit Studio action
   - extracts topics
   - searches Obsidian and Knowledge graph
   - proposes a workflow plan
   - waits for confirmation before costly or external actions
   - optionally extracts second opinions from attached reference files
   - runs I/W/I, Deep Research, specialist evaluations, business-plan synthesis,
     Obsidian writing, export, and email drafting

`SourceFoundationGraph` node shape:

```text
register_source
  -> convert_to_markdown
  -> write_chaos_markdown
  -> run_data
  -> run_information
  -> run_knowledge
  -> write_knowledge_status
  -> notify_chat
```

`BusinessPlanningGraph` node shape:

```text
receive_motive
  -> register_attached_references
  -> extract_topics
  -> search_obsidian
  -> search_knowledge_graph
  -> extract_second_opinion
  -> draft_workflow_plan
  -> await_user_confirmation
  -> run_iwi
  -> decide_research_need
  -> run_deep_research
  -> run_specialist_evaluations
  -> synthesize_business_plan
  -> write_obsidian_documents
  -> export_pdf_docx
  -> draft_email
  -> await_send_approval
```

Local-first persistence:

- Use `langgraph` plus `langgraph-checkpoint-sqlite` for V1 local checkpoints.
- Store checkpoints under Aily's data directory, for example
  `~/.aily/langgraph_checkpoints.sqlite`.
- Keep normalized Aily domain records in Aily-managed SQLite tables.
- Treat LangGraph checkpoints as resumable execution state, not the canonical
  business record.
- Set strict checkpoint serialization controls where supported.
- Consider PostgreSQL checkpointers later only if Aily becomes multi-user or
  hosted.

Human-in-the-loop interrupts:

- workflow plan confirmation
- Tavily `pro` research approval
- specialist-team rerun approval when costs are high
- export approval
- email send approval

Node implementation rule:

LangGraph nodes should wrap existing Aily capabilities before introducing new
logic. For example, `convert_to_markdown` should call the existing processing
router/converter path; `run_data`, `run_information`, and `run_knowledge` should
reuse the existing DIKIWI stage agents; `write_obsidian_documents` should reuse
the existing Obsidian writers.

Acceptance criteria:

- A workflow can pause at confirmation and resume after user approval.
- A failed graph can resume from the last checkpoint without duplicating source,
  research, export, or email side effects.
- Every LangGraph `thread_id` maps to an Aily `workflow_run_id`.
- Every node emits Studio/chat status events.
- LangGraph checkpoints are backed up alongside Aily's own durable records.

### 4. Canonical Markdown Conversion

Purpose:

Simplify all downstream processing by making Markdown the universal internal
format.

Target artifact:

```text
source_id/
  raw.<ext>
  canonical.md
  metadata.json
  assets/
    image-001.png
    table-001.csv
    page-001.png
```

The canonical Markdown package should include:

- source title
- source type
- extraction provider
- conversion timestamp
- stable section IDs
- page/slide/time anchors when available
- links to extracted assets
- confidence/error metadata

Provider strategy:

1. Local converter first for privacy and cost control.
2. Cloud converter optional for difficult documents.
3. Provider choice must be recorded per source.

Candidates:

- local Docling / existing processors
- MinerU for PDFs
- Mistral OCR for PDF/image-heavy conversion
- LlamaParse for broad document conversion
- Mathpix for formula-heavy papers

Acceptance criteria:

- DIKIWI only consumes canonical Markdown, not arbitrary raw parser output.
- Every source has either a `canonical.md` or a conversion failure record.
- Conversion can be retried without duplicating the source.
- Assets survive into Data evidence anchors.

### 5. DIKIWI Foundation: Data, Information, Knowledge

Purpose:

Build the durable knowledge layer automatically.

#### Data

V1 meaning:

Data is source-grounded evidence storage.

It should contain:

- source snippets
- paraphrased evidence anchors
- page/section/time references
- table/figure/image references
- raw claim candidates
- confidence

Data should not be a broad summary.

#### Information

V1 meaning:

Information is typed extraction from Data.

It should contain:

- claims
- facts
- methods
- mechanisms
- constraints
- tradeoffs
- metrics
- questions
- concepts
- tags
- domains
- source evidence IDs

Information should be atomic and graph-ready.

#### Knowledge

V1 meaning:

Knowledge is graph structure over Information.

It should contain:

- meaningful relationships
- clusters
- contradictions
- dependencies
- reusable retrieval contexts
- source-backed subgraphs

Knowledge is the default stopping point for newly added documents.

Acceptance criteria:

- New sources reach Knowledge without running Insight/Wisdom/Impact.
- Graph nodes link back to source and canonical Markdown anchors.
- Generic tags or page labels cannot dominate the graph.
- Knowledge jobs are retryable and visible.

### 6. Triggered Insight, Wisdom, Impact

Purpose:

Run higher-order reasoning only when the user has an external motivation.

Trigger examples:

- "Run a business simulation about X."
- "Evaluate whether this idea is worth pursuing."
- "Find product opportunities around this topic."
- "Summarize what Aily knows about this market."
- "Generate a business plan from this knowledge cluster."

Input:

- user motive, topic, or prompt extracted by the Aily Orchestrator Agent
- selected graph neighborhood
- related Knowledge nodes
- matching Obsidian documents
- optional existing notes
- optional Deep Research packets

Output:

- Insight: non-obvious patterns and contradictions in context
- Wisdom: durable principles, strategic implications, and synthesis
- Impact: actionable idea summary suitable for evaluation teams

Acceptance criteria:

- I/W/I jobs are manually or explicitly triggered.
- Triggers can come from the chat agent after topic extraction and user
  confirmation.
- I/W/I outputs cite Knowledge nodes and source anchors.
- I/W/I jobs can request Deep Research when internal evidence is thin.
- I/W/I failure does not change source ingestion status.

### 7. Second Opinion References

Purpose:

Allow users to attach business plans, pitch decks, technical memos, consultant
reports, competitor analyses, or outputs from other business-planning tools as
an independent reference after Impact analysis. This gives Aily's downstream
teams another perspective without letting that external document overwrite
Aily's own evidence-backed reasoning.

Second-opinion references answer:

- What does another plan or tool believe?
- What assumptions does it make?
- What risks or opportunities does it emphasize?
- Where does it agree with Aily's internal Knowledge and Impact summary?
- Where does it disagree?
- What claims require verification through Deep Research or source evidence?

Input:

- chat/task attachment
- selected Impact summary
- current I/W/I outputs
- source Knowledge context
- optional user note explaining why the reference matters

Output:

```json
{
  "second_opinion_id": "",
  "source_id": "",
  "attached_to": "workflow_run_id",
  "document_type": "business_plan|deck|report|memo|other",
  "stance": "supportive|contradictory|mixed|unknown",
  "major_claims": [],
  "assumptions": [],
  "recommended_actions": [],
  "risks": [],
  "agreement_with_aily": [],
  "disagreement_with_aily": [],
  "claims_needing_verification": [],
  "team_relevance": {
    "technical_innovation": [],
    "engineering_assessment": [],
    "commercial_feasibility": []
  }
}
```

Truth policy:

- A second-opinion file is not trusted by default.
- Its claims are labeled as external user-provided reference material.
- It can influence questions, comparisons, and evaluation prompts.
- It cannot satisfy evidence requirements unless independently supported by
  Aily Knowledge, original source evidence, or Deep Research.
- Contradictions should be surfaced, not hidden.

Storage policy:

- The raw attached file enters `SourceStore`.
- The converted Markdown belongs under `00-Chaos` because it is source material.
- The extracted second-opinion packet belongs under a non-Chaos generated-artifact
  area, preferably `07-Research/Second-Opinions` or `08-Evaluations`.

Acceptance criteria:

- User can attach a file to a chat-triggered workflow.
- The attachment is converted to canonical Markdown and linked to the workflow.
- Aily extracts a second-opinion packet with claims, assumptions,
  disagreements, and verification needs.
- Specialist teams receive second-opinion packets as labeled references.
- Final business plans distinguish Aily evidence from second-opinion claims.

### 8. Deep Research Component

Purpose:

Extend Aily beyond the user's supplied knowledge when internal data is
insufficient in breadth or depth.

Primary V1 provider:

- Tavily Research API

Secondary or future providers:

- Exa / Websets for structured market and competitor discovery
- Firecrawl for selected URL-to-Markdown extraction
- Elicit / Semantic Scholar / OpenAlex for academic literature
- OpenAI Deep Research for high-value final synthesis if needed

Research should produce packets, not vague reports.

Target Research Packet:

```json
{
  "research_id": "",
  "topic": "",
  "trigger": "iwi|technical_innovation|engineering|commercial",
  "model": "mini|pro",
  "status": "pending|running|completed|failed",
  "claims": [],
  "evidence": [],
  "sources": [],
  "contradictions": [],
  "confidence": "",
  "freshness": "",
  "recommended_next_questions": []
}
```

Usage rules:

- Use `mini` for narrow gap-filling.
- Use `pro` for business-plan, market, competitor, technical prior-art, or
  multi-domain analysis.
- Never run research automatically for every source.
- Keep a per-run and per-day quota guard.
- Cache completed research packets by topic and role.
- Store sources and citations with the packet.
- Do not commit API keys.

Acceptance criteria:

- A research job can be created from an I/W/I prompt.
- A research job can be created from a chat motive after topic extraction and
  confirmation.
- A research job can be created from each specialist team.
- Research status is visible in Studio.
- Completed research packets are ingested into Data/Information/Knowledge as
  external evidence.

### 9. Specialist Evaluation Teams

Purpose:

Turn Impact summaries into evidence-backed business plans.

The three V1 teams are:

1. Technical Innovation
2. Engineering Assessment
3. Commercial Feasibility

Each team receives:

- Impact summary
- relevant Knowledge context
- relevant I/W/I output
- optional second-opinion packet from user-attached files
- role-specific Deep Research packet
- source lineage

#### Technical Innovation

Questions:

- Is the idea novel?
- What prior art exists?
- What technical wedge is defensible?
- What differentiates this from existing products/research?
- What technical hypothesis must be proven first?

Output:

- novelty assessment
- prior-art map
- differentiators
- invention opportunities
- technical moat hypothesis
- risk of obviousness

#### Engineering Assessment

Questions:

- Can this be built?
- What architecture would be required?
- What data, models, APIs, infrastructure, and dependencies are needed?
- What is the implementation complexity?
- What are the highest technical risks?

Output:

- feasible architecture
- MVP scope
- dependency map
- implementation milestones
- cost and runtime risks
- build/buy recommendations

#### Commercial Feasibility

Questions:

- Who has the pain?
- Who pays?
- What alternatives exist?
- What market timing supports the idea?
- What is the initial wedge?
- What proof of value would convince a buyer?

Output:

- target customer
- buyer/user distinction
- competitor landscape
- market signals
- pricing hypothesis
- go-to-market wedge
- commercial risks

Acceptance criteria:

- Each team can run independently.
- Each team can invoke Deep Research with a role-specific prompt.
- Each team can compare Aily's reasoning against second-opinion references.
- Each evaluation cites internal and external evidence separately.
- Team outputs are stored as structured artifacts.

### 10. Comprehensive Business Plan Synthesizer

Purpose:

Merge I/W/I and specialist-team outputs into one defensible business plan.

Target sections:

1. Executive Summary
2. Source Knowledge Lineage
3. Problem Definition
4. Customer And Buyer
5. Proposed Solution
6. Technical Innovation
7. Engineering Plan
8. Commercial Feasibility
9. Second Opinion Comparison
10. Market And Competitive Landscape
11. MVP Scope
12. Validation Plan
13. Risks And Kill Criteria
14. Milestones
15. Investment / Resource Estimate
16. Recommendation

Acceptance criteria:

- Every major claim links to at least one evidence source.
- Internal Aily knowledge and external Deep Research are distinguishable.
- User-provided second-opinion claims are distinguishable from verified evidence.
- The plan includes explicit uncertainty and kill criteria.
- The plan can be exported as Markdown.
- The plan is visible in Studio.

### 11. Obsidian Document Vault

Purpose:

Obsidian remains Aily's fundamental knowledge-base management tool and the
canonical human-readable storage layer for generated documents.

Generated Aily documents should be written to Obsidian first, then referenced by
Studio, export jobs, and email delivery jobs.

Documents stored in Obsidian:

- source files and canonical source Markdown under the existing Chaos namespace
- Data notes
- Information notes
- Knowledge notes
- chat prompts, accepted workflow plans, and synthesis notifications
- I/W/I summaries
- second-opinion packets
- Deep Research packets
- Technical Innovation evaluations
- Engineering Assessment evaluations
- Commercial Feasibility evaluations
- comprehensive business plans
- evidence-bound dossiers

Recommended V1 vault layout:

```text
00-Chaos/
  _assets/
  sources/
  canonical-markdown/
01-Data/
02-Information/
03-Knowledge/
04-Insight/
05-Wisdom/
06-Impact/
07-Research/
  Second-Opinions/
08-Evaluations/
09-Business-Plans/
10-Dossiers/
99-System/
```

`00-Chaos` is already assigned to raw source material, extracted assets, and
documents converted into Markdown. V1 must not introduce another `00-*` folder
for status, prompts, or operations. System-level documents should live in
`99-System`. Dossiers are the promoted final reader-facing synthesis artifact
and should live in `10-Dossiers`. The previous export/email delivery stage is
removed from V1.

Obsidian document metadata should include:

- source IDs
- prompt IDs
- topic IDs
- second-opinion IDs
- research IDs
- I/W/I run ID
- evaluation IDs
- business plan ID
- generation timestamp
- provider/model metadata
- export status
- delivery status

Acceptance criteria:

- Every major generated artifact has an Obsidian Markdown document.
- Studio links to the Obsidian-backed document, not only an in-memory payload.
- Business plans are stored in Obsidian before export or delivery.
- `00-Chaos` remains the only `00-*` namespace and stores source/canonical
  Markdown material.
- Chat status and orchestration records are written outside `00-Chaos`, for
  example under `99-System`.
- The vault can be backed up and restored as the human-readable record of Aily's
  work.

### 12. Dossier Generation

Purpose:

Provide Aily's official reader-facing learning artifact for polished,
evidence-bound ideas.

Flow:

```text
Obsidian Markdown
  -> claim extraction
  -> Vault evidence reconciliation
  -> Tavily evidence reconciliation
  -> unsupported-claim quarantine
  -> dossier in 10-Dossiers
```

Supported dossier sources:

- source-equivalent Markdown
- Data, Information, and Knowledge notes
- Insight, Wisdom, and Impact notes
- research packets
- specialist evaluations
- business plans
- Tavily search results

Acceptance criteria:

- A dossier is written under `10-Dossiers`.
- Every claim is tied to Vault evidence or Tavily evidence.
- Unsupported claims remain explicitly marked as hypotheses.
- The dossier is readable as a substantive human learning document, not a
  placeholder or internal audit dump.

## V1.0 Data Model Additions

Recommended new or extended entities:

```text
Source
CanonicalDocument
EvidenceAnchor
InformationUnit
KnowledgeEdge
ChatThread
ChatMessage
PromptNotification
TopicExtraction
WorkflowPlan
WorkflowRun
LangGraphCheckpoint
SecondOpinionReference
SecondOpinionPacket
ResearchJob
ResearchPacket
IwiRun
TeamEvaluation
BusinessPlan
ObsidianDocument
ExportJob
EmailDelivery
```

Important IDs:

- `source_id`
- `canonical_document_id`
- `evidence_id`
- `information_id`
- `knowledge_edge_id`
- `chat_thread_id`
- `message_id`
- `prompt_notification_id`
- `topic_id`
- `workflow_plan_id`
- `workflow_run_id`
- `langgraph_thread_id`
- `checkpoint_id`
- `second_opinion_id`
- `research_id`
- `iwi_run_id`
- `evaluation_id`
- `business_plan_id`
- `obsidian_document_id`
- `export_job_id`
- `email_delivery_id`

Every downstream artifact should be traceable back to:

```text
email_delivery_id
  -> export_job_id
  -> obsidian_document_id
  -> business_plan_id
  -> evaluation_id
  -> iwi_run_id
  -> workflow_run_id
  -> workflow_plan_id
  -> topic_id
  -> chat_thread_id
  -> second opinion IDs
  -> knowledge node IDs
  -> information IDs
  -> evidence IDs
  -> canonical Markdown anchors
  -> raw source IDs
```

## V1.0 API Surface

### Inbox / Source APIs

```text
GET  /api/sources
GET  /api/sources/{source_id}
POST /api/sources/{source_id}/retry
POST /api/inbox/scan
```

### Knowledge APIs

```text
GET /api/knowledge/search
GET /api/knowledge/graph
GET /api/knowledge/context
```

### Chat / Orchestration APIs

```text
GET  /api/chat/threads
POST /api/chat/threads
GET  /api/chat/threads/{chat_thread_id}
POST /api/chat/threads/{chat_thread_id}/messages
POST /api/chat/threads/{chat_thread_id}/attachments
GET  /api/chat/threads/{chat_thread_id}/notifications
POST /api/orchestrator/topic-extractions
POST /api/orchestrator/workflow-plans
POST /api/orchestrator/workflow-plans/{workflow_plan_id}/confirm
GET  /api/orchestrator/runs
GET  /api/orchestrator/runs/{workflow_run_id}
POST /api/orchestrator/runs/{workflow_run_id}/resume
GET  /api/system/status
GET  /api/system/health
```

### Trigger APIs

```text
POST /api/iwi/runs
GET  /api/iwi/runs/{iwi_run_id}
POST /api/second-opinions
GET  /api/second-opinions/{second_opinion_id}
POST /api/research/jobs
GET  /api/research/jobs/{research_id}
POST /api/business-plans
GET  /api/business-plans/{business_plan_id}
```

### Obsidian / Export / Delivery APIs

```text
GET  /api/obsidian/documents
GET  /api/obsidian/search
GET  /api/obsidian/documents/{obsidian_document_id}
POST /api/export/jobs
GET  /api/export/jobs/{export_job_id}
POST /api/email/drafts
POST /api/email/deliveries
GET  /api/email/deliveries/{email_delivery_id}
```

### Studio Control Actions

```text
scan_inbox
retry_failed_sources
send_chat_message
attach_chat_reference
extract_topics
search_obsidian_context
extract_second_opinion
propose_workflow_plan
confirm_workflow_plan
resume_workflow_run
run_iwi_summary
run_deep_research
run_team_evaluations
generate_business_plan
export_business_plan
create_email_draft
send_approved_email
```

## V1.0 Studio UX

V1 Studio should be renamed conceptually to `Aily Messenger` or `Aily Console`.
It is not meant to replace Obsidian. It is the conversational control surface for
starting work, attaching references, seeing system status, confirming actions,
and opening generated artifacts in Obsidian.

Primary surfaces:

1. Aily Chat And Status Console
2. Inbox Monitor
3. Source Ledger
4. Knowledge Graph
5. I/W/I Trigger Console
6. Research Packets
7. Team Evaluation Room
8. Business Plan Workspace
9. Obsidian Document Vault
10. Dossier Workspace
11. Operations / Evidence

Recommended V1 layout:

```text
┌──────────────────┬────────────────────────────────────┬────────────────────┐
│ Threads / Runs    │ Chat Timeline                       │ Status / Context   │
│                  │                                    │                    │
│ Current thread    │ User: motive + files                │ Idle / Working     │
│ Recent workflows  │ Aily: status prompt                 │ Active jobs        │
│ Inbox badge       │ Aily: workflow plan card            │ Attachments        │
│ Attention badge   │ User: confirm / revise              │ Obsidian links     │
│                  │ Aily: done + open in Obsidian        │ Warnings / errors  │
└──────────────────┴────────────────────────────────────┴────────────────────┘
```

Important UX rules:

- Aily's GUI is an IM-style interaction layer, not the canonical document
  browser.
- The chat should show status prompts and proactive notifications.
- The user should be able to state motives in natural language before Aily runs
  synthesis.
- The user should be able to attach a reference file to a chat/task prompt.
- Second-opinion attachments should show source status, conversion status,
  extracted claims, and truth-label warnings.
- Topic extraction should be visible, editable, and confirmable.
- Workflow plans should show which prompts, agents, Obsidian notes, Knowledge
  nodes, second-opinion references, and research jobs will be used.
- Generated documents should appear as summary cards with Obsidian links, not
  as full document editors.
- Users should be able to continue basic dialogue even when no workflow is
  running.
- The user should see what has reached Knowledge.
- Higher stages should look triggerable, not automatic.
- Research jobs should show cost/quota warnings.
- Business plans should show evidence completeness.
- Business plans should show Obsidian document status.
- Export/email actions should default to preview and manual approval.
- Failed jobs should be visible, retryable, and non-destructive.

## Development Phases

The phases below are the implementation sequence for V1. They are not merely
descriptive. Independent development should proceed in this order unless this
document is revised.

Phase discipline:

- A phase MUST have code changes, tests, and evidence appropriate to its scope.
- A phase MUST NOT silently expand into later phases.
- A phase MAY create compatibility adapters for later migration, but it SHOULD
  not implement unrelated product behavior.
- A phase is not complete until its acceptance criteria are satisfied.
- A downstream phase SHOULD NOT depend on behavior that has not passed its phase
  acceptance gate.

Per-phase completion record:

```text
Phase:
Implemented files:
Tests run:
Evidence artifacts:
Known limitations:
Follow-up issues:
```

### Phase 0: V1 Contract And Architecture Lock

Goal:

Turn this plan into the authoritative V1 target.

Deliverables:

- `docs/AILY_V1_UPGRADE_PLAN.md`
- updated architecture docs referencing the V1 execution split
- V1 terminology glossary
- migration inventory of old DIKIWI/Reactor/Entrepreneur paths

Acceptance:

- The team can explain V1 in one diagram.
- No implementation starts without mapping to this plan.
- This document states the authoritative scope, requirements, and change-control
  process.

### Phase 1: LangGraph Runtime Foundation

Goal:

Install and isolate the V1 orchestration runtime without changing product
behavior yet.

Deliverables:

- `langgraph` dependency
- `langgraph-checkpoint-sqlite` dependency
- LangGraph checkpoint path in settings
- `aily/orchestration/` package
- `WorkflowRun` persistence table
- `WorkflowState` typed schema
- graph factory for `SourceFoundationGraph`
- graph factory for `BusinessPlanningGraph`
- checkpoint backup inclusion

Acceptance:

- A tiny smoke graph can start, checkpoint, resume, and finish.
- Each LangGraph `thread_id` maps to an Aily `workflow_run_id`.
- Existing upload, URL, DIKIWI, Studio, and Obsidian behavior remains unchanged.

### Phase 2: Inbox Watcher And Source Ledger

Goal:

Make file-directory ingestion the primary input path.

Deliverables:

- configurable inbox path
- scanner/watcher service
- source registration for files and URL link files
- deduplication and status lifecycle
- Studio source ledger updates
- app status projection for the inbox watcher

Acceptance:

- Drop a file into the inbox and see it appear in SourceStore.
- Restart Aily and the source state remains.
- Drop the same file twice and get one canonical source.

Implementation checkpoint:

- `aily.inbox.WatchedInboxService` polls `SETTINGS.inbox_path`, ignores
  transient files, waits for file stability, registers ordinary files through
  `SourceStore.store_upload()`, registers `.url`/`.uri`/`.link`/`.webloc`
  pointer files through `SourceStore.store_url()`, and queues the existing
  durable source-job worker.
- The watcher is disabled by default and starts only when
  `INBOX_WATCHER_ENABLED=true`, preserving current upload, URL, DIKIWI, Studio,
  and Obsidian behavior until V1 migration explicitly enables it.
- `/api/ui/status` includes an inbox watcher snapshot so the future Messenger
  GUI can show whether Aily is idle, scanning, or blocked.

### Phase 3: Canonical Markdown Pipeline

Goal:

Make Markdown the universal processing input.

Deliverables:

- `MarkdownConverter` interface
- local converter implementation
- optional cloud converter hook
- canonical Markdown package storage
- conversion retry path

Implementation checkpoint:

- `SourceStore` now persists one current canonical Markdown package per source
  in `source_markdown_packages`, stores the Markdown file under
  `SETTINGS.canonical_markdown_dir`, and projects package IDs/paths back onto
  source metadata.
- `CanonicalMarkdownConverter` wraps the existing extraction result with
  `MarkdownizeProcessor`, normalizes line endings, rejects empty output, and
  stores a durable package with source lineage metadata.
- Durable file, URL, and batch source jobs now create
  `canonical_markdown_created` UI events and feed DIKIWI from the canonical
  Markdown package content instead of ad hoc extracted text.
- This checkpoint is still a local converter path. The optional cloud converter
  hook remains future work and must preserve the same package contract.

Acceptance:

- PDF, Markdown, text, and URL sources produce canonical Markdown.
- Failed conversion is visible and retryable.
- DIKIWI no longer consumes ad hoc raw extraction outputs.

### Phase 4: DIKIWI Foundation Refactor

Goal:

Make automatic DIKIWI stop at Knowledge.

Deliverables:

- Data job over canonical Markdown
- Information job over Data
- Knowledge job over Information graph
- deactivation of automatic Insight/Wisdom/Impact on ingestion
- lineage from Knowledge back to source anchors

Implementation checkpoint:

- `DikiwiMind.process_input_foundation()` now runs a single input through the
  stage-latched `DATA -> INFORMATION -> KNOWLEDGE` path.
- `DikiwiMind.process_inputs_batched(..., foundation_only=True)` stops after
  Knowledge even when graph growth would previously trigger Insight/Wisdom/Impact.
- `SETTINGS.dikiwi_foundation_only_ingestion` defaults to `true`, so automatic
  ingestion uses the foundation-only path unless the drop explicitly requests
  full DIKIWI with metadata such as `dikiwi_mode="full"`.
- Durable Studio upload, URL, batch ingestion, and Chaos batch bridging pass
  through the foundation-only switch while preserving manual full-DIKIWI escape
  hatches for later triggered workflows.

Acceptance:

- New inbox sources automatically reach Knowledge.
- No automatic I/W/I or business output is generated by source ingestion.
- Knowledge graph quality checks pass.

### Phase 5: SourceFoundationGraph Migration

Goal:

Move automatic intake execution into LangGraph while reusing current source,
processing, DIKIWI, graph, UI event, and Obsidian components.

Deliverables:

- LangGraph nodes for source registration, conversion, Chaos write, Data,
  Information, Knowledge, status update, and notification
- adapter around existing source-worker job payloads
- idempotency keys for node side effects
- resume behavior for partial failures
- duplicate-side-effect tests

Implementation checkpoint:

- `SourceFoundationGraph` now has a dependency-injected runtime path that reuses
  the current durable `SourceStore`, `ProcessingRouter`,
  `CanonicalMarkdownConverter`, DIKIWI foundation ingestion entrypoint, UI event
  stream, and workflow-run store.
- The graph owns `register_source -> convert_to_markdown -> run_data ->
  run_information -> run_knowledge` as checkpointable nodes. The current DIKIWI
  service still performs the actual foundation stage execution, while graph
  nodes project stage success and source status into durable workflow state.
- Canonical Markdown conversion is idempotent at the graph boundary: if a source
  already has a Markdown package, the graph reuses it and emits
  `canonical_markdown_reused` instead of re-running extraction/conversion.
- Failure contracts now cover a Knowledge-stage failure stopping before
  downstream graph nodes, emitting `pipeline_failed`, and avoiding
  `source_ingest_completed`.
- Retry contracts now cover a new source-foundation workflow run for the same
  source reusing the existing Markdown package after an earlier failed DIKIWI
  run, so source retries do not re-extract or re-convert canonical Markdown.
- The source-worker adapter path is covered through `_process_source_job_with_foundation_graph()`,
  including durable workflow-run creation, async SQLite checkpoints, source
  completion, Markdown package persistence, and workflow lifecycle events.
- `scripts/run_source_foundation_graph_evidence.py` now produces a standard
  evidence folder for graph-backed source intake. The first passing run is
  `~/.aily/runs/2026-05-17T07-32-31Z_source_foundation_graph_offline/manifest.json`.
  It proves the local graph adapter with real files, stores, checkpoints,
  Markdown conversion, workflow runs, and UI events, but it intentionally marks
  `mocked=true` because DIKIWI/LLM, GraphDB, and Obsidian are simulated.
- Source workers can route upload and URL source jobs through the graph when
  `ORCHESTRATOR_ENABLED=true` and `ORCHESTRATOR_SHADOW_MODE=false`. The default
  remains the legacy helper path until real-path evidence certifies the graph
  runner.

Acceptance:

- Existing Studio upload and URL intake can run through `SourceFoundationGraph`.
- A failed conversion or Knowledge run can resume without duplicating sources or
  notes.
- Automatic ingestion still stops after Knowledge.

### Phase 6: Aily Chat And Orchestrator Agent

Goal:

Make natural-language motives the primary trigger mechanism for higher-order
work.

Deliverables:

- chat thread and message persistence
- prompt notification model
- status snapshot service
- Aily Orchestrator Agent
- topic extraction prompt/agent
- Obsidian and Knowledge search adapter
- workflow plan proposal and confirmation flow
- IM-style Aily Messenger shell
- chat timeline, attachment cards, status prompts, and approval cards
- Obsidian document link cards
- Obsidian system status document under `99-System`

Implementation checkpoint:

- `POST /api/ui/workflows/iwi` is the first backend entry point for
  motive-driven synthesis. It accepts a user motive plus optional Knowledge
  graph node IDs, creates a durable `triggered_iwi` workflow run, emits queued
  and execution status events, and starts the manual I/W/I runner.
- `GET /api/ui/workflows` exposes recent workflow runs from `WorkflowRunStore`,
  while the Studio status payload now reports active workflow-runner tasks.
- `DikiwiMind.process_triggered_iwi()` reconstructs selected Knowledge graph
  context, seeds the completed Information and Knowledge stage results, and
  executes only `INSIGHT -> WISDOM -> IMPACT`.
- This checkpoint is the backend trigger lane for the future Messenger and
  Orchestrator. Chat threads, confirmation cards, second-opinion attachments,
  and the full IM shell remain follow-up deliverables.

Acceptance:

- User can enter a motive and receive extracted topics.
- Aily can map topics to Obsidian documents and Knowledge nodes.
- Aily proposes the next workflow before running I/W/I or Deep Research.
- Status notifications appear for ingestion, conversion, Knowledge processing,
  research, evaluation, plan generation, export, and email approval.
- The GUI supports basic dialogue and file attachment without requiring the user
  to leave the chat surface.
- Generated artifacts are linked out to Obsidian instead of duplicated as full
  document views.
- No status or prompt document is written into `00-Chaos` unless it is source
  material.

### Phase 7: BusinessPlanningGraph Migration

Goal:

Make the chat/orchestrator workflow a LangGraph graph with durable confirmation
interrupts.

Deliverables:

- `BusinessPlanningGraph` implementation
- chat/task attachment registration
- topic extraction node
- Obsidian/Knowledge search nodes
- second-opinion extraction node
- workflow-plan node
- confirmation interrupt
- run status projection into chat, Studio, and `99-System`
- resume endpoint wired to LangGraph command/resume semantics

Acceptance:

- User chat creates a persisted workflow run.
- User can attach a reference file to the run.
- The run pauses for confirmation before I/W/I or Deep Research.
- The run resumes after confirmation and preserves all IDs.

### Phase 8: I/W/I Trigger System

Goal:

Make Insight, Wisdom, and Impact a manual/topic-specific process.

Deliverables:

- `IwiRun` model
- trigger API
- chat/orchestrator trigger adapter
- selected Knowledge context builder
- Obsidian context search input
- I/W/I output artifacts
- Studio trigger console

Acceptance:

- User can trigger I/W/I for a topic.
- User can trigger I/W/I from chat after topic extraction.
- Output cites Knowledge nodes and source evidence.
- Failed I/W/I run does not alter source ingestion status.

### Phase 9: Second Opinion Reference Intake

Goal:

Convert user-attached external plans and reports into labeled second-opinion
packets for downstream evaluation teams.

Deliverables:

- `SecondOpinionReference` model
- `SecondOpinionPacket` schema
- attachment-to-source-store adapter
- canonical Markdown conversion for attached references
- second-opinion extraction prompt/agent
- Obsidian writer for second-opinion packets
- Studio second-opinion viewer

Acceptance:

- A chat/task attachment becomes a source record and canonical Markdown package.
- Aily extracts claims, assumptions, agreements, disagreements, and verification
  needs.
- The packet is labeled as user-provided reference, not verified truth.
- Specialist teams can consume the packet as optional context.

### Phase 10: Tavily Deep Research Integration

Goal:

Add external research as a controlled enrichment job.

Deliverables:

- Tavily client wrapper
- `ResearchJob` and `ResearchPacket` persistence
- mini/pro model selection
- quota/budget guard
- structured output schema
- polling worker
- Studio research status view

Acceptance:

- Aily can submit a research topic to Tavily and poll results.
- The orchestrator can submit a confirmed research topic from chat.
- Completed results include sources and structured claims.
- Research packets are stored and can be fed back into Knowledge.
- No API key is committed.

### Phase 11: Specialist Team Evaluations

Goal:

Create the three evaluation lanes.

Deliverables:

- Technical Innovation evaluator
- Engineering Assessment evaluator
- Commercial Feasibility evaluator
- role-specific research prompts
- structured evaluation artifacts

Acceptance:

- Each team can evaluate an Impact summary independently.
- Each team can optionally request Deep Research.
- Each team can use second-opinion packets as labeled comparison material.
- Each output separates internal evidence from external evidence.

### Phase 12: Business Plan Synthesizer

Goal:

Generate the final V1 product artifact.

Deliverables:

- business plan schema
- synthesizer prompt/agent
- Markdown export
- Studio plan viewer
- evidence completeness score

Acceptance:

- A complete plan is generated from one I/W/I run and three evaluations.
- The plan includes source lineage, risks, and kill criteria.
- The plan includes a second-opinion comparison when reference files were
  attached.
- The plan can be re-generated after new research or evaluations.
- The plan is written to Obsidian as the canonical business-plan document.

### Phase 13: Obsidian Vault Integration

Goal:

Make Obsidian the canonical generated-document store for V1.

Deliverables:

- V1 vault layout
- explicit reservation of `00-Chaos` for source and canonical Markdown material
- document writer for Research, Evaluation, I/W/I, and Business Plan artifacts
- document writer for Second Opinion packets
- document writer for prompt notifications and status under `99-System`
- metadata/frontmatter contract
- Studio links to Obsidian-backed documents
- backup/restore coverage for generated documents

Acceptance:

- Every major generated artifact is written to Obsidian.
- A business plan can be reopened from Obsidian and mapped back to its run.
- `00-Chaos` remains source/canonical-Markdown storage, not a general system
  folder.
- Obsidian remains usable even if Studio is unavailable.

### Phase 14: Evidence-Bound Dossiers

Goal:

Turn Vault-grounded knowledge into human-readable dossiers with explicit source
lineage and no unsupported claims.

Deliverables:

- dossier generation under `10-Dossiers`
- claim-to-evidence mapping
- Vault and Tavily source citations
- unsupported-claim quarantine
- readability and source-lineage scoring

Acceptance:

- A dossier can be regenerated from the Vault without manually authored content.
- Every substantive claim maps to Vault evidence or Tavily evidence.
- Unsupported hypotheses are labeled as unresolved, not presented as facts.
- The dossier is readable as a learning document for a human reviewer.

### Phase 15: Operations, Security, And Evidence

Goal:

Make V1 safe and inspectable for real use.

Deliverables:

- provider usage accounting
- Tavily quota guard
- chat/orchestrator audit log
- audit log for research and business-plan generation
- backup/restore coverage for new artifacts
- health checks for watcher, converter, worker, and research queue
- evidence manifests for triggered business-plan runs
- email transport health check
- export toolchain health check

Acceptance:

- Every business-plan run writes an evidence folder.
- Quota-limited APIs fail closed with visible errors.
- Backup includes source ledger, Markdown packages, graph, research packets,
  evaluations, Obsidian documents, exported files, email delivery records, and
  plans.

### Phase 16: V1 Release Gate

Goal:

Prove the new architecture works end-to-end.

Release scenario:

1. Drop a new PDF into the inbox.
2. Aily converts it to Markdown.
3. Aily runs Data, Information, Knowledge automatically.
4. Aily posts chat/status notifications that Knowledge is ready.
5. User describes a motive in the chat window.
6. User optionally attaches an external business-plan reference.
7. Aily extracts topics and searches Obsidian/Knowledge context.
8. Aily extracts a labeled second-opinion packet from the attachment.
9. User confirms the proposed I/W/I + research workflow.
10. Aily runs I/W/I for the selected business topic.
11. Aily runs Tavily Deep Research.
12. The three teams run role-specific evaluations.
13. Aily generates a business plan.
14. Aily writes the business plan to Obsidian.
15. Aily exports the business plan to PDF and DOCX.
16. Aily creates a dry-run email with the exported attachments.
17. Aily Messenger/Console shows source, knowledge, chat prompts, topics,
    second opinions, research, evaluations, Obsidian document links, exports,
    email draft, and final plan status.

Acceptance:

- Real file.
- Real Markdown conversion.
- Real graph writes.
- Real LLM calls.
- Real chat/orchestrator topic extraction.
- Real second-opinion attachment conversion and extraction.
- Real Tavily research call, when quota is available.
- Real Studio view.
- Real Obsidian business-plan document.
- Real PDF and DOCX exports.
- Real email dry-run, with optional approved send in a safe test mailbox.
- Durable evidence manifest.

## Suggested Implementation Order

1. Add LangGraph dependencies and checkpoint settings.
2. Add V1 config flags and data models.
3. Add workflow-run persistence and status projection.
4. Build watched inbox scanner.
5. Build canonical Markdown package storage.
6. Route current upload/URL inputs through the same source ledger.
7. Extract source-processing functions from `aily/main.py` into service modules.
8. Build `SourceFoundationGraph` around existing source/conversion/DIKIWI code.
9. Refactor automatic DIKIWI to stop at Knowledge.
10. Add chat threads, prompt notifications, and status snapshots.
11. Build `BusinessPlanningGraph` with topic extraction and context search.
12. Add chat/task reference attachments.
13. Add second-opinion extraction and packet storage.
14. Add workflow-plan confirmation/resume.
15. Add I/W/I manual trigger through LangGraph.
16. Add Tavily Research integration with quota controls.
17. Add specialist-team evaluation artifacts.
18. Add business-plan synthesizer.
19. Write all generated artifacts to Obsidian.
20. Add PDF/DOCX export.
21. Add email draft and delivery workflow.
22. Recenter Studio into the IM-style Aily Messenger workflow.
23. Add V1 evidence runner.
24. Deprecate or quarantine legacy automatic post-Impact paths.

## Migration Plan From Current Code

### Migration Strategy

This is not a rewrite. V1 should migrate Aily by extracting the current runtime
into explicit services, then wrapping those services as LangGraph nodes.

The first milestone is "same behavior, new conductor." Only after that should
we change product semantics such as stopping automatic DIKIWI at Knowledge.

Migration rules:

- Preserve working behavior until a replacement path has tests and evidence.
- Prefer adapters over rewrites during early phases.
- Move orchestration out of `aily/main.py`, but keep FastAPI app wiring there.
- Extract domain services before wrapping them as LangGraph nodes.
- Keep legacy paths behind feature flags until the V1 path proves equivalent or
  intentionally supersedes them.
- Delete or quarantine legacy paths only after the V1 replacement has acceptance
  evidence.
- Do not change prompts, data schemas, and orchestration runtime in the same
  patch unless the phase explicitly requires it.

### Keep As Product Infrastructure

- `aily/source_store/store.py`
- `aily/processing/markdownize.py`
- `aily/processing/router.py` as a temporary conversion adapter
- `aily/sessions/dikiwi_mind.py` as the compatibility facade for DIKIWI while
  stages are extracted
- `aily/dikiwi/network_synthesis.py`
- `aily/graph/db.py`
- `aily/ui/events.py`
- `aily/verify/`
- `aily/writer/obsidian.py`
- `aily/writer/dikiwi_obsidian.py`

### Add New V1 Packages

Recommended package layout:

```text
aily/orchestration/
  __init__.py
  state.py
  runs.py
  checkpoint.py
  source_foundation_graph.py
  business_planning_graph.py
  nodes/
    source_nodes.py
    conversion_nodes.py
    dikiwi_nodes.py
    chat_nodes.py
    context_nodes.py
    second_opinion_nodes.py
    research_nodes.py
    evaluation_nodes.py
    business_plan_nodes.py
    obsidian_nodes.py
    export_nodes.py
    email_nodes.py

aily/chat/
  store.py
  schemas.py
  service.py

aily/research/
  tavily_client.py
  store.py
  schemas.py
  quota.py

aily/second_opinion/
  store.py
  schemas.py
  extractor.py

aily/business/
  evaluations.py
  plans.py
  synthesizer.py

aily/export/
  markdown_exporter.py
  email_delivery.py
```

### Upgrade Existing Components

| Component | Current Role | V1 Upgrade |
|---|---|---|
| `requirements.txt` / `pyproject.toml` | App dependencies | Add `langgraph` and `langgraph-checkpoint-sqlite`; keep FastAPI/aiosqlite stack. |
| `aily/config.py` | Settings singleton | Add inbox paths, LangGraph checkpoint path, orchestrator flags, research budgets, export/email settings. |
| `aily/main.py` | FastAPI entrypoint plus too much orchestration | Keep app wiring, middleware, lifecycle; move processing/orchestration logic into services and graph runners. |
| `aily/source_store/store.py` | Durable raw source and source job store | Add inbox provenance, canonical Markdown artifact records, workflow/run lineage, and idempotency keys. |
| `aily/processing/router.py` | File/URL extraction adapter | Promote to canonical Markdown conversion service; preserve processor compatibility. |
| `aily/ui/router.py` upload handling | Studio file intake | Extend or mirror for chat/task attachments and second-opinion reference files. |
| `aily/chaos/*` | Existing file watcher/processors | Reuse processors; align watcher with V1 inbox and SourceStore instead of parallel queues. |
| `aily/sessions/dikiwi_mind.py` | Complete DIKIWI runner and compatibility hub | Split into stage-level service methods; add `run_foundation_only`; make I/W/I callable only by triggered graph. |
| `aily/dikiwi/agents/*` | Stage agents | Keep and wrap as LangGraph nodes; do not rewrite stage prompts during early migration. |
| `aily/dikiwi/orchestrator.py` | Current DIKIWI stage orchestrator | Keep inside DIKIWI service or reduce to stage transition helper after LangGraph owns workflow routing. |
| `aily/graph/db.py` | Knowledge graph | Add search/context helpers needed by topic extraction and BusinessPlanningGraph. |
| `aily/ui/events.py` | Studio event stream | Reuse for graph-node status; add workflow-run and prompt-notification event types. |
| `aily/ui/router.py` | Studio HTTP/WebSocket routes | Add chat, workflow, system status, research, export, and email endpoints; keep existing `/api/ui/*` until migration completes. |
| New V1 GUI package | Studio prototype | Build an IM-style Aily Messenger shell with chat timeline, attachment cards, status prompts, approval cards, workflow run list, and Obsidian links. |
| `aily/writer/dikiwi_obsidian.py` | DIKIWI vault writer | Preserve numbered DIKIWI folders; enforce `00-Chaos` reservation and add `99-System` writer support. |
| `aily/sessions/reactor_scheduler.py` | Legacy innovation scheduler | Wrap or quarantine; migrate useful logic into Technical Innovation evaluator. |
| `aily/sessions/entrepreneur_scheduler.py` / `gstack_agent.py` | Legacy business evaluator | Wrap useful GStack/Guru logic into Commercial Feasibility and Business Plan synthesis; stop autonomous daily scheduling by default. |
| `aily/agent/*` | Legacy simple planner agents | Quarantine or adapt small pieces into chat/topic extraction tests; do not use as V1 core. |
| `aily/verify/*` | Evidence primitives | Redesign the V1 evidence harness around LangGraph checkpoints, workflow runs, chat prompts, research packets, exports, and email drafts. |

### LangGraph Node Wrapping Rules

- Nodes must call Aily services; they should not duplicate business logic.
- Nodes must be idempotent where they create side effects.
- Every side-effecting node must write an Aily record before or immediately
  after the side effect.
- Every node must emit status through `aily/ui/events.py`.
- Every node must carry `workflow_run_id`, `langgraph_thread_id`, and relevant
  domain IDs.
- Nodes must use Aily's quota and approval services before Tavily, export, or
  email actions.
- Second-opinion nodes must label attached-file claims as user-provided external
  reference material.

### Autonomous Development Checklist

Before changing code, an implementation agent MUST answer:

1. Which phase does this change advance?
2. Which existing modules does it touch?
3. Which new modules, if any, does it introduce?
4. Which IDs must be preserved?
5. Which side effects require idempotency?
6. Which tests prove the change?
7. Which real evidence, if any, is required before claiming acceptance?
8. Which legacy path remains active, wrapped, disabled, or quarantined?

After changing code, the agent MUST update or report:

- changed files
- tests run
- evidence artifacts created
- remaining risks
- whether the phase acceptance gate is satisfied

### Concrete Migration Steps

1. Add dependencies and settings:
   - `langgraph`
   - `langgraph-checkpoint-sqlite`
   - `orchestrator_enabled`
   - `langgraph_checkpoint_db_path`
   - `inbox_path`
   - `research_daily_budget`
   - `email_delivery_enabled`
2. Add `aily/orchestration/runs.py`:
   - create `workflow_runs`
   - map `workflow_run_id` to LangGraph `thread_id`
   - expose run status snapshots
3. Add `aily/orchestration/state.py`:
   - typed state for source foundation runs
   - typed state for business-planning runs
4. Extract current source job functions from `aily/main.py`:
   - `_process_upload_source_job`
   - `_process_url_source_job`
   - `_source_worker_loop`
   - `_handle_ui_upload`
   - `_handle_ui_url`
   into a source-intake service that can be called by both FastAPI and
   LangGraph nodes.
5. Add a DIKIWI foundation service:
   - wrap Data, Information, Knowledge stage calls
   - expose `run_foundation_only(source_id, canonical_document_id)`
   - preserve existing `DikiwiMind.process_input()` temporarily for backwards
     compatibility.
6. Introduce `SourceFoundationGraph` behind a feature flag:
   - first run it in shadow mode next to the current path
   - compare source status, graph counts, Obsidian notes, and UI events
   - then switch uploads/URLs/inbox jobs to the graph path.
7. Add chat persistence and APIs:
   - `ChatThread`
   - `ChatMessage`
   - `PromptNotification`
   - `TopicExtraction`
   - `WorkflowPlan`
8. Introduce `BusinessPlanningGraph`:
   - start from chat motive
   - attach optional reference files
   - search Obsidian and graph
   - extract second-opinion packets
   - create a workflow plan
   - interrupt for confirmation
   - resume into I/W/I, research, evaluations, and plan synthesis.
9. Migrate post-Knowledge behavior:
   - disable automatic I/W/I on ingestion
   - disable autonomous Reactor/Entrepreneur scheduling by default
   - re-enable useful logic only through BusinessPlanningGraph nodes.
10. Extend Studio:
    - IM-style chat/status console
    - workflow run timeline
    - attachment cards
    - confirmation cards
    - Obsidian link cards
    - research quota warnings
    - export/email approval UI.
11. Extend evidence:
    - include LangGraph thread/checkpoint IDs
    - include chat prompt and confirmed workflow plan
    - include no-duplicate-side-effect proof after resume.

### Quarantine

- old automatic post-Impact proposal path until it maps cleanly to V1
- experimental `aily/dikiwi/skills/`
- experimental `aily/dikiwi/memorials/`
- older gating modules that are not part of the active V1 path

## Test Strategy

Testing is part of the product contract. Tests prove that the implementation
matches this plan; they do not redefine the plan.

Test discipline:

- Unit tests prove local contracts.
- Integration tests prove subsystem wiring.
- Real acceptance tests prove product behavior.
- A mocked test can support development but cannot close a V1 acceptance gate.
- Every migration phase should start with the narrowest useful test and end with
  the broadest practical evidence for that phase.

### Fast Tests

- chat message and attachment-card rendering
- status prompt rendering
- approval-card state transitions
- Obsidian link-card rendering
- LangGraph smoke graph checkpoint/resume
- workflow run persistence and thread mapping
- idempotency keys for side-effecting graph nodes
- SourceStore deduplication
- inbox scanner
- URL link parsing
- Markdown package creation
- Data/Information/Knowledge contracts
- chat thread and prompt-notification persistence
- topic extraction schema
- second-opinion packet schema
- status snapshot lifecycle
- workflow-plan confirmation contract
- I/W/I trigger model
- research packet schema
- business plan schema

### Integration Tests

- basic dialogue -> chat response/status prompt
- file attachment through chat -> source record
- `SourceFoundationGraph` file -> Markdown -> Knowledge
- `SourceFoundationGraph` resume after conversion failure
- inbox file -> Markdown -> Knowledge
- URL link file -> fetch -> Markdown -> Knowledge
- chat prompt -> topic extraction -> Obsidian/Knowledge context search
- chat attachment -> canonical Markdown -> second-opinion packet
- workflow plan confirmation -> I/W/I trigger
- `BusinessPlanningGraph` interrupt/resume around confirmation
- status notifications for ingestion and triggered jobs
- I/W/I trigger from selected Knowledge context
- Tavily client with recorded "disabled/no key" behavior
- quota guard rejects over-budget research
- business plan generation from stored team outputs
- Obsidian document writing for generated artifacts
- Markdown-to-PDF and Markdown-to-DOCX export from Obsidian documents
- email dry-run generation with exported attachments

### Real Acceptance Tests

- one real browser flow using the IM-style chat surface
- one real PDF through inbox to Knowledge
- one real LangGraph checkpoint/resume in the source-foundation path
- one real URL link file through inbox to Knowledge
- one real chat motive through topic extraction and workflow confirmation
- one real BusinessPlanningGraph pause/resume at workflow confirmation
- one real attached second-opinion document converted and extracted
- one real Tavily `mini` research packet
- one real I/W/I + three-team + business-plan run
- one real business plan written to Obsidian
- one real PDF and DOCX export from Obsidian Markdown
- one email dry-run with attachments
- one Studio browser flow showing the full V1 lifecycle

## Evidence Requirements

Evidence is mandatory for acceptance. If a behavior cannot be reproduced from
the evidence folder, it is not accepted as V1 behavior.

Every V1 acceptance run should produce:

```text
~/.aily/runs/<run_id>/
  manifest.json
  command.txt
  environment.json
  source-manifest.json
  canonical-markdown/
  graph-before.json
  graph-after.json
  langgraph-checkpoints.json
  workflow-run.json
  chat-thread.json
  prompt-notifications.jsonl
  topic-extractions.json
  second-opinion-packets.json
  workflow-plan.json
  research-packets.json
  team-evaluations.json
  business-plan.md
  obsidian-documents.json
  exports/
    business-plan.pdf
    business-plan.docx
  email-draft.json
  delivery-record.json
  ui-events.jsonl
  llm-calls.jsonl
  failures.json
```

## Key Risks

### Scope Creep

Risk:

V1 could become "automate everything" again.

Control:

Only Data/Information/Knowledge run automatically. Everything after Knowledge is
triggered.

### Research Cost

Risk:

Deep Research can consume quota quickly.

Control:

Use budget guards, cache packets, default to `mini`, and require explicit
triggering for `pro`.

### Evidence Dilution

Risk:

External research could pollute the user's own knowledge graph.

Control:

Mark external research as external. Keep source origin and confidence explicit.

### Second Opinion Contamination

Risk:

User-attached plans or reports may be persuasive but wrong. If Aily treats them
as truth, downstream evaluations and business plans could inherit unsupported
claims.

Control:

Label second-opinion content as user-provided external reference material.
Extract assumptions, claims, agreements, disagreements, and verification needs.
Require independent support from Aily Knowledge, source evidence, or Deep
Research before a second-opinion claim becomes a business-plan claim.

### Agent Overreach

Risk:

The backend Aily agent may treat a vague chat message as permission to run
expensive research, generate formal plans, or prepare outbound email.

Control:

Separate intent extraction from execution. Require workflow-plan confirmation
before Deep Research, team evaluation, export, or email delivery. Persist the
user motive, extracted topics, confirmed plan, and all job IDs.

### LangGraph Over-Abstraction

Risk:

Aily could move too much domain logic into graph nodes and become difficult to
test, debug, or migrate.

Control:

Keep graph nodes thin. Put product logic in Aily services with direct unit and
integration tests. Use LangGraph for durable execution, branching, interrupts,
and resume; do not use it as the canonical data model.

### Duplicate Side Effects After Resume

Risk:

A resumed graph could duplicate Obsidian notes, Tavily research jobs, exports,
or email sends.

Control:

Every side-effecting node needs an idempotency key and a durable Aily record.
Before executing a side effect, the node must check whether the corresponding
record already exists for the same `workflow_run_id`, node name, and input hash.

### Vault Namespace Collision

Risk:

Status notes, prompt logs, or operational files could pollute `00-Chaos`, which
is already assigned to source files and canonical Markdown.

Control:

Reserve `00-Chaos` for raw/canonical source material only. Store status,
notifications, orchestration logs, and operational documents under `99-System`
or another non-`00` namespace.

### Business Plan Hallucination

Risk:

The final plan may sound convincing without enough evidence.

Control:

Require evidence links, confidence, contradictions, and kill criteria.

### Outbound Email Mistakes

Risk:

Aily could accidentally send unfinished, private, or incorrect business plans to
external recipients.

Control:

Default to dry-run previews, require manual approval for real sends, record every
delivery in Obsidian, and keep attachment hashes in the audit log.

### Legacy Path Confusion

Risk:

Existing automatic Reactor/Entrepreneur paths may conflict with V1.

Control:

Quarantine or wrap legacy paths behind the new triggered business-plan workflow.

## V1.0 Definition Of Done

Aily V1.0 is complete when:

- LangGraph runs the source-foundation and business-planning workflows with
  durable checkpoints and resumable state.
- Aily has an IM-style GUI for chat, attachments, status prompts, approvals,
  workflow summaries, and Obsidian links.
- The watched inbox is the primary source intake path.
- Every accepted source gets a canonical Markdown package.
- New sources automatically reach Knowledge and stop.
- Aily has a chat window that captures motives, shows status prompts, extracts
  topics, and proposes workflows.
- Users can attach second-opinion files to chat/task prompts, and Aily extracts
  labeled reference packets without treating them as absolute truth.
- The backend orchestrator can search Obsidian/Knowledge and dispatch confirmed
  I/W/I, research, evaluation, plan, export, and email jobs.
- Insight/Wisdom/Impact are triggered by topic/prompt.
- Tavily Deep Research can enrich I/W/I and specialist-team work.
- Technical Innovation, Engineering Assessment, and Commercial Feasibility
  produce separate structured evaluations.
- Aily generates a comprehensive business plan with evidence lineage.
- All generated documents are written to Obsidian.
- Business plans can be exported from Obsidian Markdown to PDF and DOCX.
- Aily can create a reviewed email draft with exported attachments.
- Aily Messenger/Console shows the full lifecycle.
- All acceptance claims have real evidence manifests.

## Long-Term Development Posture

After V1.0, Aily should continue evolving from this contract rather than from
ad hoc feature impulses.

Long-term rules:

- Preserve the North Star unless the product itself is redefined.
- Keep Obsidian as the human-readable knowledge base unless a future plan
  explicitly replaces it.
- Keep expensive reasoning intentional and auditable.
- Keep external evidence labeled by origin and confidence.
- Keep the GUI lightweight unless there is a strong reason to compete with
  Obsidian.
- Prefer small, phase-aligned changes over large rewrites.
- Treat autonomous agents as contributors to the plan, not owners above the
  plan.

Future versions may add cloud sync, hosted deployment, richer collaboration, or
additional research providers, but they should inherit the V1 discipline:
traceable inputs, durable workflows, explicit approvals, evidence-backed claims,
and business-plan output.
