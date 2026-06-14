# aily

## Purpose

Aily is a **single-user, Obsidian-native knowledge refinery** (FastAPI engine).
It ingests files/links dropped into one inbox folder, refines them through the
DIKIWI foundation pipeline (**Data → Information → Knowledge**, automatic and
bounded), and — only on explicit user approval — generates higher-order
synthesis (**Insight → Wisdom → Impact**) plus decision artifacts. Notes are
written into a numbered Obsidian vault.

The autonomous "Three-Mind" engines (Reactor/Entrepreneur/Guru), Feishu
ingestion, and the chaos daemon have been **removed**; their docs live in
`docs/history/`.

## Key files

| Path | Description |
|------|-------------|
| `aily/main.py` | FastAPI engine: lifespan, source worker, inbox watcher, detection + heartbeat loops, router mounts |
| `aily/config.py` | Pydantic `SETTINGS` — env vars, `resolved_inbox_path`, `resolved_vault_path` |
| `aily/sessions/dikiwi_mind.py`, `aily/dikiwi/` | DIKIWI pipeline + agents + orchestrator + gates |
| `aily/source_store/`, `aily/processing/` | Durable source store + extraction → canonical Markdown |
| `aily/inbox/watcher.py` | Single-folder intake (file + URL-pointer) |
| `aily/synthesis/` | Synthesis candidate queue + detector (recommend-only) |
| `aily/copilot/` | Product APIs under `/api/copilot` (chat, source status, candidates, `/control`) |
| `obsidian-plugin/aily-copilot` | Obsidian companion plugin |

## For AI agents

- Config lives in `aily/config.py` (pydantic-settings); the app reads `.env`.
  Never hardcode keys. `.env` is git-ignored.
- **Tooling:** use `uv` for Python (`uv run …`, `uv run pytest`) — system Python
  does not match the project env. The plugin uses Node 22 + npm.
- **Tests:** `uv run pytest` (a real suite exists). Plugin: `npx tsc --noEmit` +
  `npx eslint`. Never run `npm run dev`.
- Verify changes by compiling + importing (`uv run python -m compileall -q aily`)
  and, where it matters, a real boot (`uvicorn aily.main:app`).

## Patterns

- Async-first; `logger = logging.getLogger(__name__)`; `Path` not strings.
- Provider routing in `aily/llm/provider_routes.py` falls back to the configured
  provider when a workload's preferred provider has no key (single-provider
  setups just work).
- Honest terminal status: zero-note sources are `completed_empty` with a reason.

## Reference

`README.md` · `DEPLOY.md` · `GAP.md` · `docs/CURRENT_STATE.md`. Anything in
`docs/history/` is superseded and does not describe the current product.
