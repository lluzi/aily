<!-- Generated: 2026-04-26 | Updated: 2026-04-26 -->

# aily

## Purpose

Aily is a **Three-Mind knowledge system** built as a FastAPI app with a background job queue. It continuously processes incoming messages, files, and URLs through a DIKIWI 6-stage LLM pipeline (Data → Information → Knowledge → Insight → Wisdom → Impact), writes Zettelkasten notes to an Obsidian vault, and runs innovation/business evaluation loops. Supports three execution modes: single-document, batched, and graph-driven incremental processing.

## Key Files

| File | Description |
|------|-------------|
| `aily/config.py` | Pydantic SETTINGS singleton — all env vars and defaults |
| `aily/main.py` | FastAPI entry point, lifespan context, job dispatch |
| `requirements.txt` | Python dependencies |
| `pyproject.toml` | Project metadata and tool config |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `aily/` | Core application code |
| `docs/` | V1 architecture and design references |
| `scripts/` | Minimal daemon and evidence helpers |

## For AI Agents

### Working In This Directory
- All configuration lives in `aily/config.py` via pydantic-settings
- The app reads `.env` at startup; never hardcode API keys
- Legacy test infrastructure has been removed for the Aily V1 redesign.
- Until V1 test infrastructure lands, validate with import/build checks and manual evidence only.

### Common Patterns
- Async-first codebase — all I/O is async
- Dataclasses for configs, Pydantic for settings
- `logger = logging.getLogger(__name__)` in every module
- `Path` objects everywhere, never string paths

## Dependencies

### Internal
- `aily/` — entire application is self-contained

### External
- `fastapi` — HTTP server
- `pydantic-settings` — Configuration
- `httpx` / `aiohttp` — HTTP clients
- `aiosqlite` — Async SQLite
- `watchfiles` — File watching (Chaos daemon)
- `apscheduler` — Scheduled jobs
- `pdfplumber` / `docling` / `mineru` — Document extraction

<!-- MANUAL: -->
