#!/usr/bin/env bash
# Install the Aily engine as a launchd service (macOS). Runs the FastAPI engine
# 24/7, restarts on crash, starts at login. Idempotent.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ] || [ ! -x "$UV" ]; then
  echo "❌ uv not found. Install it first: https://docs.astral.sh/uv/" >&2
  exit 1
fi
UVDIR="$(dirname "$UV")"
LOGDIR="$HOME/.aily/logs"
PLIST="$HOME/Library/LaunchAgents/com.aily.engine.plist"

if [ ! -f "$REPO/.env" ]; then
  echo "⚠️  No .env found at $REPO/.env — copy .env.example and set OBSIDIAN_VAULT_PATH + an LLM key first." >&2
fi

mkdir -p "$LOGDIR" "$HOME/Library/LaunchAgents"
sed -e "s|@UV@|$UV|g" -e "s|@UVDIR@|$UVDIR|g" -e "s|@REPO@|$REPO|g" -e "s|@LOGDIR@|$LOGDIR|g" \
  "$REPO/scripts/com.aily.engine.plist.template" > "$PLIST"
plutil -lint "$PLIST" >/dev/null

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✅ Installed and loaded com.aily.engine"
echo "   repo:  $REPO"
echo "   uv:    $UV"
echo "   logs:  $LOGDIR/engine.{out,err}.log"
echo
echo "Verify (give it a few seconds to boot):"
echo "   curl -s http://127.0.0.1:8000/ready | python3 -m json.tool"
echo "Stop/remove:  scripts/uninstall_service.sh"
