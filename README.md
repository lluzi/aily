# Aily

Aily is a **single-user, Obsidian-native knowledge refinery**. You drop documents
and links into one folder; an always-on engine refines them into trustworthy,
source-linked notes inside your Obsidian vault, and — on your explicit approval —
generates higher-order synthesis (Insight / Wisdom / Impact).

The vault is the product. Everything else is the metabolism that keeps it smart.

```
drop a file/link  ─►  inbox folder  ─►  engine (extract → canonical Markdown →
                                          Data → Information → Knowledge)
                                          ─►  notes in your Obsidian vault
                                          ─►  detector recommends ripe topics
                                          ─►  you approve  ─►  Insight/Wisdom/Impact
```

## How it works

- **One intake.** Any file (PDF, Markdown, DOCX, HTML, `.eml` email, …) dropped
  into the watched inbox is hashed, de-duplicated, converted to canonical
  Markdown, and processed. URLs arrive as `.url`/`.webloc` pointer files or via
  the plugin.
- **Automatic only through Knowledge.** Ingestion runs Data → Information →
  Knowledge (`dikiwi_foundation_only_ingestion=true`). It is cheap and bounded.
- **Higher-order synthesis is earned, not automatic.** A detector watches the
  knowledge graph and *recommends* ripe topics into a candidate queue (a daily
  pass + a knowledge-growth threshold). Generation runs only when **you approve**
  a candidate, or ask for a topic explicitly. Detection makes no LLM calls.
- **Honest status.** A source that produced no notes is reported `completed_empty`
  with a reason — never a silent green "completed".

## Runtime

- FastAPI engine: `aily/main.py` (run with `uvicorn aily.main:app`)
- Config: `aily/config.py` via `.env` (see `.env.example`)
- DIKIWI pipeline: `aily/sessions/dikiwi_mind.py`, `aily/dikiwi/`
- Inbox watcher: `aily/inbox/watcher.py`
- Source store + canonical Markdown: `aily/source_store/`, `aily/processing/`
- Synthesis detection + candidate queue: `aily/synthesis/`
- Product APIs (Copilot): `aily/copilot/` under `/api/copilot` — chat, vault
  search, **source status**, **synthesis candidates**, and a mobile control page
  at `/api/copilot/control`
- Obsidian companion plugin: `obsidian-plugin/aily-copilot`
- Vault-visible status note: `99-System/Aily Status.md` (heartbeat / what's new /
  needs-you)

## Vault layout

```
00-Chaos        (source-equivalent Markdown + _inbox drop zone)
01-Data  02-Information  03-Knowledge            (automatic)
04-Insight  05-Wisdom  06-Impact                 (approval-gated)
07-Research  08-Evaluations  09-Business-Plans  10-Dossiers
99-MOC  99-System
```

## Quick start (development)

```bash
uv sync
cp .env.example .env        # set OBSIDIAN_VAULT_PATH + an LLM key
uv run uvicorn aily.main:app --port 8000
# drop a file into <vault>/00-Chaos/_inbox and watch 01–03 fill in
uv run pytest               # test suite
```

`GET /ready` reports readiness (fails loudly if the vault or LLM key is missing).

## Running it for real (home server)

See **[DEPLOY.md](DEPLOY.md)** for the Mac-Mini setup: a launchd service, a
cloud-synced drop folder (`INBOX_PATH`), Obsidian Publish for reading, optional
Tailscale for remote approval, and daily backups.

## Status & roadmap

- **[GAP.md](GAP.md)** — gap analysis (current build vs. a daily-usable product)
  and the critical path.
- **[docs/CURRENT_STATE.md](docs/CURRENT_STATE.md)** — shortest map of the code.
- Earlier directions (autonomous "Three-Mind" system, Feishu, V1/V2 planning) are
  archived under `docs/history/` and no longer describe the product.

## LLM providers

Multi-provider via `aily/llm/`. Set `LLM_PROVIDER` (`deepseek` | `kimi` | `zhipu`)
and the matching key. A single provider key drives every stage (foundation uses
the configured provider; image/vision work needs a vision-capable provider such
as Kimi).
