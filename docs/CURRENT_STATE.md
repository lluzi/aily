# Aily Current State

Shortest trustworthy map of the codebase as it exists now. (Superseded design
docs are in `docs/history/`.)

## What Aily is

A single-user, Obsidian-native knowledge refinery: drop files/links into one
inbox folder → refined Data/Information/Knowledge notes in the vault →
approval-gated Insight/Wisdom/Impact synthesis.

## Active runtime

- Engine entry: `aily/main.py` (`uvicorn aily.main:app`)
- Config: `aily/config.py` (`.env`; see `.env.example`)
- Intake: `aily/inbox/watcher.py` → `aily/source_store/` → `aily/processing/`
  (extraction → `canonical_markdown.py`)
- Foundation pipeline (D→I→K): `aily/sessions/dikiwi_mind.py`,
  `aily/dikiwi/orchestrator.py`, `aily/dikiwi/agents/{data,information,knowledge}_agent.py`
- Higher-order agents (I/W/I): `aily/dikiwi/agents/{insight,wisdom,impact}_agent.py`
  (run only via approved/triggered synthesis)
- Graph-change readiness signal: `aily/dikiwi/network_synthesis.py`
- Synthesis detection + candidate queue: `aily/synthesis/` (detector + store)
- Writer: `aily/writer/dikiwi_obsidian.py`
- Product APIs: `aily/copilot/` under `/api/copilot` (chat, vault search,
  source status, candidates, `/control` page)
- Vault status note: `99-System/Aily Status.md` (heartbeat / what's new / needs-you)
- LLM routing: `aily/llm/provider_routes.py` (single-provider fallback)
- Evidence harness: `aily/verify/`

## Active flow

1. A file/link lands in the inbox (cloud-synced folder via `INBOX_PATH`, or
   `<vault>/00-Chaos/_inbox`); the watcher hashes/dedups and queues it.
2. The source worker extracts → canonical Markdown → DIKIWI foundation
   (Data → Information → Knowledge). Foundation-only by default.
3. A source with zero notes is reported `completed_empty` with a reason.
4. The detector (daily + knowledge-growth threshold; no LLM) recommends ripe
   subgraphs into the candidate queue.
5. The user approves a candidate (control page / candidate API) → Insight →
   Wisdom → Impact runs over that scope and writes notes. Impact is terminal.

## Vault layout

`00-Chaos` (+ `_inbox`), `01-Data`, `02-Information`, `03-Knowledge`,
`04-Insight`, `05-Wisdom`, `06-Impact`, `07-Research`, `08-Evaluations`,
`09-Business-Plans`, `10-Dossiers`, `99-MOC`, `99-System`.

## Removed / quarantined

- **Removed:** autonomous Reactor/Entrepreneur/GStack schedulers, the Feishu
  pipeline, the chaos daemon, `thinking/frameworks`, dead `gating` flow,
  `dikiwi/skills` + `memorials`. (History in `docs/history/`.)
- **Quarantined (in-tree, not on the live path):** `dikiwi/agents/residual_agent.py`.

## Tests

`uv run pytest` — real suite covering processing/email, source status, inbox,
candidate queue/detector, provider routing, terminal status, status note,
config precedence, and cut invariants.

## Not yet done

See `GAP.md` for the remaining gaps (e.g. dossier/business-plan outputs, plugin
Source-Monitor view, vision ingestion) and the critical path.
