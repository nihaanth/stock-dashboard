#!/usr/bin/env bash
# Install launchd jobs:
#   - com.stockbot.dashboard : daily 19:30 IST  (data refresh + git push)
#   - com.stockbot.retrain   : 1st of month 03:00 IST (rolling-window retrain)
#
# NOTE: intra-day live polling (com.stockbot.live) is NOT installed by this
# script. It moved to GitHub Actions (see .github/workflows/live_poll.yml)
# because launchd fires in the Mac's local time, which doesn't line up with
# IST market hours when this Mac sits in America/Chicago. The
# com.stockbot.live.plist file is kept in the repo as a fallback option for
# anyone running this on an IST-resident Mac that's awake during the day.
#
# Idempotent: re-run to update either plist.
set -euo pipefail

AUTO=/Users/nihaanthreddy/devl/Learning/Projects/stockbot/automation
DEST_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$DEST_DIR"
chmod +x "$AUTO/daily_refresh.sh" "$AUTO/monthly_retrain.sh"

install_plist () {
  local name=$1
  local src="$AUTO/$name.plist"
  local dest="$DEST_DIR/$name.plist"
  cp "$src" "$dest"
  launchctl unload "$dest" 2>/dev/null || true
  launchctl load "$dest"
  echo "  installed: $dest"
}

install_plist com.stockbot.dashboard
install_plist com.stockbot.retrain

echo
echo "Verify:"
echo "  launchctl list | grep stockbot"
echo
echo "Run now (without waiting):"
echo "  launchctl start com.stockbot.dashboard   # daily refresh"
echo "  launchctl start com.stockbot.retrain     # monthly retrain (~30 min)"
echo
echo "Logs:"
echo "  tail -f $AUTO/daily_refresh.log"
echo "  tail -f $AUTO/monthly_retrain.log"
echo
echo "Live polling: see https://github.com/nihaanth/stock-dashboard/actions"
