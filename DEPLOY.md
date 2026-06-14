# Deploying Aily on a Mac Mini home server

Aily is a personal knowledge refinery: you drop files/links into one folder, an
always-on Mac Mini digests them into your Obsidian vault, and you read the result
(Obsidian Sync, or Obsidian Publish for browser access). This is the move-in guide.

## Topology

```
phone / laptop / anywhere        cloud folder (Dropbox/iCloud/Drive)        Mac Mini (always on)
  share file or link  ─────────►  Aily-Inbox/  ◄──── sync client ─────────►  watched inbox
                                   .processed/  (originals = backup)          Aily engine (launchd)
                                                                              writes notes → vault
                                                                                      │
                                                                                      ▼
                                                                          Obsidian Sync / Publish (read)
```

The Mini is the **only** machine that runs the engine and holds `~/.aily` state.
Capture is just "save a file into one synced folder"; reading is your vault.

## 1. Prerequisites (on the Mini)

- A logged-in user session (a launchd *agent* needs an active session — fine for a standby Mini left logged in).
- [uv](https://docs.astral.sh/uv/) installed.
- This repo cloned, then: `uv sync`
- Your Obsidian vault present on the Mini (via Obsidian Sync or a file syncer).

## 2. Configure

```bash
cp .env.example .env
```
Set at minimum:
- `OBSIDIAN_VAULT_PATH` — absolute path to the vault on the Mini.
- `LLM_PROVIDER` + the matching key (`DEEPSEEK_API_KEY` / `KIMI_API_KEY` / `LLM_API_KEY`).
- `INBOX_PATH` — **the cloud drop folder** (e.g. `/Users/you/Dropbox/Aily-Inbox`). When set, this wins over the in-vault inbox, which is what the cloud-drop model wants.

Check readiness any time: `curl -s http://127.0.0.1:8000/ready` (reports `ready:false` with reasons if vault/key are missing).

## 3. The cloud drop folder

Pick one and point `INBOX_PATH` at the local folder it syncs to:

- **Dropbox via [Maestral](https://maestral.app)** (recommended — lightweight, headless-friendly): `brew install maestral`, link your account, then `INBOX_PATH=~/Dropbox/Aily-Inbox`.
- **Google Drive / S3 via [rclone](https://rclone.org)**: schedule `rclone move gdrive:Aily-Inbox ~/Aily-Inbox` on a `launchd`/cron timer into the watched folder.
- **iCloud Drive**: works, but disable "Optimize Mac Storage" or it evicts files the watcher can't read.

How it behaves: drop a file → engine ingests it → the original is moved to `<inbox>/.processed/` (which syncs back, so the drop folder self-empties as confirmation **and** `.processed/` is your off-machine backup of originals). Links: drop a `.url`/`.webloc` file (a 3-step iOS Shortcut can write one).

## 4. Install the engine as a service

```bash
scripts/install_service.sh      # installs + loads com.aily.engine (launchd)
```
- Starts at login, restarts on crash, logs to `~/.aily/logs/engine.{out,err}.log`.
- Verify: `curl -s http://127.0.0.1:8000/ready | python3 -m json.tool`
- Remove: `scripts/uninstall_service.sh`

## 5. See that it's working — no terminal needed

The engine writes **`99-System/Aily Status.md`** into the vault every 60s:
heartbeat, queue counts, recently refined sources, anything needing your attention
(empty/failed sources), and pending synthesis candidates. Open that note on your
phone or Publish site to confirm "my brain is alive" and "what's new."

## 6. Reading on other devices

- **Obsidian Sync**: the vault (and `99-System/Aily Status.md`) appears on phone/laptop.
- **Obsidian Publish**: publish for browser access. Note Publish is push-on-demand from the Obsidian app — run Obsidian on the Mini as a login item and auto-publish on a timer. Set a site password; consider publishing only refined folders (`01`–`06`), not raw `00-Chaos`.

## 7. Remote control (optional)

To approve synthesis candidates or chat from your phone, the engine API must be
reachable. Put the Mini on [Tailscale](https://tailscale.com), set
`UI_AUTH_ENABLED=true` + a strong `UI_AUTH_TOKEN`, run uvicorn with `--host 0.0.0.0`
(edit the plist), and point the plugin / a browser at `http://<mini-tailscale>:8000`.
**Do not expose the port to the public internet.**

## 8. Backups (do this)

- `~/.aily` holds all derived state (graph, source store, candidates). The engine
  takes a daily snapshot to `~/.aily/backups/`; also enable **Time Machine** on the Mini.
- Your raw originals are backed up in the cloud `.processed/` folder.
- The vault itself is backed up by Obsidian Sync / your file syncer.

## Troubleshooting

- Nothing processes → check `~/.aily/logs/engine.err.log` and `/ready`.
- A source shows **empty** in the status note → it had too little extractable text
  (image-only PDFs need a vision model / Kimi key).
- Service not running → `launchctl list | grep aily`, then re-run `install_service.sh`.
