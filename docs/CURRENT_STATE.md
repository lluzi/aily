# Aily Current State

This file is the shortest trustworthy map of the codebase as it exists now.

## Active Runtime

- App bootstrap: `aily/main.py`
- Legacy GUI: removed for the Aily V1 UI redesign
- Continuous pipeline entrypoint: `aily/sessions/dikiwi_mind.py`
- DIKIWI runtime coordination: `aily/dikiwi/orchestrator.py`
- DIKIWI graph-trigger selector: `aily/dikiwi/network_synthesis.py`
- Post-pipeline proposal synthesis: `aily/dikiwi/agents/residual_agent.py`
- Innovation scheduler: `aily/sessions/reactor_scheduler.py`
- Business evaluation scheduler: `aily/sessions/entrepreneur_scheduler.py`
- GStack and Guru planning: `aily/sessions/gstack_agent.py`
- Chaos ingestion bridge: `aily/chaos/dikiwi_bridge.py`
- Chaos daemon entrypoint: `scripts/run_chaos_daemon.py`
- Real-run evidence primitives: `aily/verify/evidence.py`
- Evidence run registry/API: `aily/verify/run_registry.py`
- Durable source store: `aily/source_store/store.py`
- Canonical Markdown package converter: `aily/processing/canonical_markdown.py`
- V1 watched inbox plumbing: `aily/inbox/watcher.py`
- Optional SourceFoundationGraph intake path: `aily/orchestration/source_foundation_graph.py`
- Manual V1 I/W/I trigger: `/api/ui/workflows/iwi`
- Persistent Studio event log: `aily/ui/events.py`
- Provider route and timeout control: `aily/llm/provider_routes.py`, `aily/llm/llm_router.py`
- Provider capability matrix: `aily/llm/provider_capabilities.py`
- Hosted-mode guardrails: `aily/security/`
- Aily-Copilot backend API: `aily/copilot/`, mounted under `/api/copilot`
- Aily-Copilot Obsidian companion plugin:
  `obsidian-plugin/aily-copilot`, now a full upstream-derived fork of
  `logancyang/obsidian-copilot` with the upstream React chat surface preserved
  and an added native Aily chain mode, installed and enabled in the iCloud
  vault

## Active Flow

1. Input enters through Feishu WebSocket, the chaos bridge, queue-driven jobs, Aily Studio uploads, Aily Studio URL submission, or the V1 watched inbox.
2. Studio uploads and URLs are hashed and persisted into the source store before extraction or downstream work.
3. V1 automatic ingestion defaults to foundation-only mode through `SETTINGS.dikiwi_foundation_only_ingestion=true`; file, URL, Studio, and Chaos batch ingestion stop after `KNOWLEDGE` unless a drop explicitly requests full DIKIWI.
4. The older incremental graph-growth trigger remains available for full DIKIWI runs, but Insight/Wisdom/Impact are treated as triggered synthesis work rather than default ingestion work.
5. After IMPACT, Reactor and Entrepreneur components still exist as legacy higher-order engines that V1 should wrap or quarantine behind the new orchestrator design.
6. Notes are written into the numbered Obsidian vault layout.
7. Aily-Copilot can search and read the configured vault, build citation-ready
   context envelopes, expose content-based graph/relevant-note navigation,
   scope retrieval through local projects, and stage preview-first note writes.
8. The Aily-Copilot companion plugin is installed and enabled in the iCloud
   vault. It uses Obsidian Copilot's upstream chat UI and routes the `Aily`
   chat mode through Aily backend chat APIs with vault citations.

## Active Vault Layout

- `00-Chaos`
- `01-Data`
- `02-Information`
- `03-Knowledge`
- `04-Insight`
- `05-Wisdom`
- `06-Impact`
- `07-Research`
- `08-Evaluations`
- `09-Business-Plans`
- `10-Dossiers`
- `99-MOC`
- `99-System`

## Reference Docs

- `README.md` - repo entrypoint and current workflow overview
- `docs/AILY_V1_UPGRADE_PLAN.md` - authoritative Aily V1 upgrade and migration guide
- `docs/AILY_V2_PRODUCT_DEFINITION.md` - Obsidian-plugin product definition,
  feature taxonomy, positioning, and V2 roadmap
- `docs/AILY_V2_PRODUCT_REVIEW.md` - product-team review of V2 definition,
  risks, open decisions, and recommended roadmap revision
- `docs/AILY_V2_0_REQUIREMENTS.md` - implementation contract for the V2.0
  source-foundation release
- `docs/ARCHITECTURE_AND_VISION.md` - high-level system map
- `docs/DIKIWI_ARCHITECTURE.md` - current DIKIWI runtime and post-pipeline flow
- `docs/AILY_CHAOS_ARCHITECTURE.md` - current chaos ingestion and bridge path
- `docs/AI_INNOVATION_METHODOLOGIES.md` - framework reference
- `docs/prompt-improvement-spec.md` - prompt design direction and prompt-layer changes

## V1 Test Infrastructure Status

- Legacy test suites, integration mocks, report artifacts, and ad hoc runner scripts have been removed.
- Aily V1 needs a newly designed GUI plus test/evidence harness aligned with LangGraph workflows, Obsidian persistence, provider research packets, exports, and email delivery.
- Until the V1 harness lands, use lightweight import/build sanity checks and real-path manual evidence only; do not claim acceptance from mocked LLM, mocked graph, mocked vault, or fake browser events.

## Experimental Or Quarantined

- `aily/dikiwi/skills/` is shipped in-tree but is not part of the active production path.
- `aily/dikiwi/memorials/` is shipped in-tree but is not wired into the active runtime.
- `aily/gating/` remains as older/secondary infrastructure and fallback material, not the primary DIKIWI path.
- Provider benchmarking and smoke-check scripts have been removed with the legacy test harness; V1 should reintroduce provider evaluation as part of the redesigned evidence system.
