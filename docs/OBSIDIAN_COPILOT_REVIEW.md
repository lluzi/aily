# Obsidian Copilot Review For Aily-Copilot

Origin: Created by Codex lead agent on 2026-05-23.
Scope: Design and code review notes from `https://github.com/logancyang/obsidian-copilot`.
License note: Obsidian Copilot is AGPL-3.0. Aily should learn from product and
architecture patterns, but should not copy implementation code into Aily unless
we intentionally accept AGPL obligations.

## Executive Read

Obsidian Copilot is a strong Obsidian-native AI assistant. Its product strength
comes from keeping the user inside the vault: chat, mentions, local search,
project context, citations, memory, tools, and previewed file edits are all
available where the notes live.

Aily should not fork it as the main product foundation. Aily's differentiator is
deeper information processing: PDF-to-canonical-Markdown intake, DIKIWI,
multi-model routing, Tavily research, evidence gates, dossier generation,
business synthesis, graph quality review, and quality scoring. Those should
remain in Aily's backend. The product layer should borrow Copilot's interaction
patterns and expose them through an Aily-native Obsidian companion plugin.

## Reviewed Areas

- `README.md`: product positioning and user workflows.
- `docs/context-and-mentions.md`: context sources, mentions, and user control.
- `docs/vault-search-and-indexing.md`: lexical and semantic search behavior.
- `docs/projects.md`: project-scoped context and chat history.
- `docs/agent-mode-and-tools.md`: tool UX and autonomous agent loop.
- `docs/custom-commands.md`: reusable user prompt workflows.
- `designdocs/CONTEXT_ENGINEERING.md`: L1-L5 layered prompt envelope.
- `designdocs/CITATION_IMPLEMENTATION.md`: inline source citation pipeline.
- `designdocs/TOOLS.md`: tool registry, schemas, categories, tool prompting.
- `src/search/v3/README.md`: chunked lexical retrieval, filters, graph boosts.
- `src/context/*`: context envelope, compaction, block registry, chat history
  compaction.
- `src/tools/*`: tool registry, vault search, note read, file tree, time, memory,
  and CLI tools.

## Findings

### Strength: Vault Is Treated As The Product Surface

Copilot is not just a backend service that happens to write Markdown. It is an
interactive vault product: active note context, selected text, note mentions,
folder mentions, tag mentions, URL context, web tab context, and file editing
all live in the Obsidian UX.

Implication for Aily:

- Aily should keep the heavy intelligence in FastAPI, but expose it through an
  Obsidian companion UI.
- The companion plugin should call Aily APIs rather than duplicate DIKIWI logic.
- The iCloud vault should be the default user-visible work surface.

### Strength: Context Envelope Reduces Prompt Chaos

Copilot's L1-L5 model is the right abstraction:

- L1: stable system prompt.
- L2: previous context library.
- L3: current-turn context.
- L4: compact chat history.
- L5: user request.

Implication for Aily:

- Dossiers, business plans, and chat responses should be generated from a
  structured context envelope with stable layer hashes.
- Aily should stop treating prompt assembly as opaque string concatenation.
- Every generated answer should carry a context manifest: search result IDs,
  note paths, source hashes, and layer hashes.

### Strength: Search Has Explicit Guarantees

Copilot search v3 separates guaranteed filter results from scored search:

- `[[note]]` title mentions are guaranteed.
- `#tag` matches are guaranteed.
- time-window retrieval has its own path.
- lexical search works without paid embeddings.
- semantic search is optional.
- folder and graph signals boost results.

Implication for Aily:

- Aily's first retrieval layer should be lexical and deterministic.
- Later semantic retrieval should be additive, not required for basic use.
- Query results should label why a note was included: exact title, tag, time,
  lexical score, backlink, shared source, or graph edge.

### Strength: Citations Are A Product Feature

Copilot has a source catalog, stable source IDs, fallback sources, citation
normalization, and renderer support.

Implication for Aily:

- Aily dossiers and vault chat should cite every substantive claim.
- Citation IDs should map to vault-relative paths and evidence excerpts.
- Dossier verification should fail if a claim cannot be traced to Vault or
  Tavily evidence.

### Strength: Tool Registry Gives Product Control

Copilot tools have schemas, categories, settings metadata, prompt instructions,
and enablement rules.

Implication for Aily:

- Aily should define product tools for `vaultSearch`, `readNote`,
  `graphNeighborhood`, `traceClaim`, `generateDossier`, `runDikiwi`,
  `runBusinessPlan`, and `qualityScore`.
- Tool metadata should include safety class, vault mutation behavior, timeout,
  and whether user confirmation is required.

### Strength: Preview-First Writing Protects User Trust

Copilot previews write/edit changes before applying them.

Implication for Aily:

- Aily must not directly rewrite human vault notes from chat.
- Generated documents can be created under controlled Aily folders.
- Edits to user-authored notes should be proposed as patches with preview,
  accept, reject, and revert states.

### Risk: Plugin-Centric Architecture Is Not Enough For Aily

Copilot's architecture is built around an Obsidian plugin and front-end state.
That is correct for Copilot, but insufficient for Aily's backend workflows and
evidence requirements.

Implication for Aily:

- Do not fork Copilot as the main implementation.
- Build Aily APIs first, then a thin Obsidian plugin.
- Keep workflow state in durable Aily stores and evidence runs, not only plugin
  chat state.

### Risk: Search Quality Can Still Drift Without Evaluation

Copilot has tests for retrieval mechanics, but Aily needs product-specific
quality scoring: source coverage, note readability, graph connection quality,
claim support, and dossier usefulness.

Implication for Aily:

- Each Aily-Copilot milestone needs tests that inspect content quality, not just
  endpoint existence.
- Vault chat answers need source-grounded review.
- Dossiers need readability and citation completeness scoring.

## Borrowed Patterns For Aily

1. Layered context envelope.
2. Deterministic lexical vault search before semantic search.
3. Guaranteed retrieval for explicit note, folder, tag, and time references.
4. Citation catalog and inline evidence IDs.
5. Tool registry with schema plus behavioral prompt guidance.
6. Project-scoped context.
7. Preview-first vault mutations.
8. Memory split between user preferences and factual evidence.
9. Agent status events that show tool calls and retrieval progress.
10. Context compaction that preserves structure and recoverability.

## Aily-Specific Product Direction

Aily-Copilot should become:

- an Obsidian-native chat surface for the Aily vault;
- a grounded retrieval and citation system;
- an interface for triggering DIKIWI and dossier generation;
- a project workspace for source collections and business reasoning;
- a review surface for graph quality, note readability, and evidence coverage.

The product promise is not "chat with notes." It is "reason from your vault,
generate high-quality materials, and prove where every idea came from."
