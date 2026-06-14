#!/usr/bin/env bash
# Stop and remove the Aily engine launchd service.
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.aily.engine.plist"
if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✅ Removed com.aily.engine"
else
  echo "Nothing to remove (no $PLIST)."
fi
