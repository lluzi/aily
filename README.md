# Aily

Aily is a private, Obsidian-native knowledge and value-production system. It
turns source material into a tracked knowledge foundation, then lets the user
explicitly trigger deeper reasoning and business artifacts from that foundation.

The current product direction is:

```text
source files / URLs / notes
  -> Source Store
  -> source-equivalent Markdown in 00-Chaos
  -> Data
  -> Information
  -> Knowledge
  -> user-approved Insight / Wisdom / Impact
  -> research / evaluations / business plan / dossier
```

## Current Runtime

- FastAPI app: `aily/main.py`
- Configuration: `aily/config.py` through pydantic-settings and `.env`
- Continuous DIKIWI entrypoint: `aily/sessions/dikiwi_mind.py`
- Durable source registry: `aily/source_store/`
- Canonical Markdown converter: `aily/processing/canonical_markdown.py`
- Optional source-foundation graph path: `aily/orchestration/source_foundation_graph.py`
- Workflow store and run state: `aily/orchestration/runs.py`, `aily/orchestration/state.py`
- Real-run evidence harness: `aily/verify/`
- Aily-Copilot backend API: `aily/copilot/`, mounted under `/api/copilot`
- Obsidian companion plugin: `obsidian-plugin/aily-copilot`

Legacy Reactor, Residual, Entrepreneur, Guru, and older gating modules still
exist, but they are no longer the primary V1 product flow. Treat them as legacy
or secondary infrastructure unless a task explicitly targets them.

## Active Flow

1. Inputs arrive through the watched inbox, Chaos ingestion, Studio upload/API
   paths, URLs, Feishu messages, or Aily-Copilot workflows.
2. Uploads and URLs are hashed and registered in the Source Store before
   extraction or LLM processing.
3. Source-equivalent Markdown is written into `00-Chaos`. For PDFs, this means
   page/slide text plus rendered page images when a renderer is available.
4. Automatic ingestion defaults to foundation-only DIKIWI:
   `Data -> Information -> Knowledge`.
5. `Insight`, `Wisdom`, `Impact`, Deep Research, evaluations, business plans,
   and dossiers are explicit value workflows, not automatic background work.
6. Generated notes and documents are stored in the numbered Obsidian vault
   layout as the human-readable product database.

## Obsidian Vault Layout

The current human-visible vault layout is:

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

The local iCloud vault used by the evidence runs is:

```text
/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily
```

## Aily-Copilot

`obsidian-plugin/aily-copilot` is an upstream-derived Obsidian Copilot fork with
native Aily backend integration.

It provides:

- grounded vault chat through `/api/copilot`
- citation-ready context envelopes
- project-scoped retrieval
- preview-first note writes
- source upload hooks
- explicit end-to-end value workflow tools

The backend workflow tool is:

```text
run_end_to_end_value_workflow
```

It records an explicit user topic/comment, searches local vault and Knowledge
context first, persists workflow state, and keeps expensive downstream work
behind explicit user approval.

## Key Commands

Start the backend:

```bash
uv run python -m aily.main
```

Run a Python import/build sanity check:

```bash
uv run python -m compileall -q aily scripts
```

Build the Obsidian plugin:

```bash
cd obsidian-plugin/aily-copilot
npm run build
```

Install the local Aily-Copilot plugin into the configured vault:

```bash
uv run scripts/install_aily_copilot_plugin.py
```

Create or inspect the V1 vault layout:

```bash
uv run scripts/setup_v1_vault_layout.py
```

Run the 20-PDF product maturity evidence harness in dry-run mode:

```bash
uv run scripts/run_20pdf_product_maturity_evidence.py --dry-run
```

Score generated `00-Chaos` source-equivalent Markdown:

```bash
uv run scripts/score_chaos_markdown_quality.py --vault-path "/Users/luzi/Library/Mobile Documents/com~apple~CloudDocs/Documents/aily"
```

## Evidence And Validation

Legacy test infrastructure was removed during the V1 redesign. Until the new
test harness lands, use:

- `uv run python -m compileall -q aily scripts`
- plugin build checks for plugin-facing changes
- real-path evidence runs under `~/.aily/runs/<run_id>/`
- deterministic vault/Chaos/evidence quality scripts

Do not claim product acceptance from mocked LLMs, mocked graph state, mocked
vault output, or fake browser events.

The current maturity harness is:

```text
scripts/run_20pdf_product_maturity_evidence.py
```

It registers all selected PDFs before downstream processing, writes `00-Chaos`
source-equivalent Markdown, runs bounded foundation processing, then executes
the full value path over the completed evidence set.

## Important Docs

- `docs/CURRENT_STATE.md` - shortest reliable map of active code paths
- `docs/AILY_V1_UPGRADE_PLAN.md` - authoritative V1 architecture and migration contract
- `docs/AILY_V2_PRODUCT_DEFINITION.md` - Obsidian-plugin product definition and V2 roadmap
- `docs/AILY_V2_PRODUCT_REVIEW.md` - product-team review of V2 risks and roadmap refinements
- `docs/AILY_V2_0_REQUIREMENTS.md` - V2.0 source-foundation implementation contract
- `docs/ARCHITECTURE_AND_VISION.md` - high-level architecture
- `docs/DIKIWI_ARCHITECTURE.md` - DIKIWI runtime and post-pipeline background
- `docs/AILY_CHAOS_ARCHITECTURE.md` - Chaos ingestion path
- `docs/prompt-improvement-spec.md` - prompt-layer direction

## Development Notes

- The codebase is async-first.
- Use `Path` objects for filesystem paths.
- Keep secrets in `.env`; never hardcode provider keys.
- Preserve the Obsidian vault as generated evidence. Do not manually repair
  vault evidence and call it product output.
- Prefer small, evidence-safe changes that align with the V1/V2 product docs.
