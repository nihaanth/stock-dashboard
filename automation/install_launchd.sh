#!/usr/bin/env bash
# Install the launchd job so daily_refresh.sh runs daily at 19:30 IST.
# Idempotent: re-run to update the plist.
set -euo pipefail

SRC=/Users/nihaanthreddy/devl/Learning/Projects/stockbot/automation/com.stockbot.dashboard.plist
DEST="$HOME/Library/LaunchAgents/com.stockbot.dashboard.plist"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$SRC" "$DEST"

# Reload — unload first (safe if not loaded), then load.
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "Installed: $DEST"
echo
echo "Verify with:"
echo "  launchctl list | grep stockbot"
echo
echo "To run now (without waiting for 19:30):"
echo "  launchctl start com.stockbot.dashboard"
echo
echo "Logs:"
echo "  tail -f /Users/nihaanthreddy/devl/Learning/Projects/stockbot/automation/daily_refresh.log"
