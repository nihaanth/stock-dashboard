#!/usr/bin/env bash
# Install launchd jobs:
#   - com.stockbot.dashboard : daily 19:30 IST  (data refresh + git push)
#   - com.stockbot.retrain   : 1st of month 03:00 IST (rolling-window retrain)
#   - com.stockbot.live      : every 2 min, Mon-Fri 09:00-15:58 IST (NSE announcements)
# Idempotent: re-run to update any plist.
#
# To regenerate the (large) StartCalendarInterval array in com.stockbot.live.plist:
#   python3 -c '
#   for wd in range(1,6):
#     for h in range(9,16):
#       for m in range(0,60,2):
#         if h==15 and m>58: continue
#         print(f"<dict><key>Weekday</key><integer>{wd}</integer>"
#               f"<key>Hour</key><integer>{h}</integer>"
#               f"<key>Minute</key><integer>{m}</integer></dict>")'
set -euo pipefail

AUTO=/Users/nihaanthreddy/devl/Learning/Projects/stockbot/automation
DEST_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$DEST_DIR"
chmod +x "$AUTO/daily_refresh.sh" "$AUTO/monthly_retrain.sh" "$AUTO/live_refresh.sh"

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
install_plist com.stockbot.live

echo
echo "Verify:"
echo "  launchctl list | grep stockbot"
echo
echo "Run now (without waiting):"
echo "  launchctl start com.stockbot.dashboard   # daily refresh"
echo "  launchctl start com.stockbot.retrain     # monthly retrain (~30 min)"
echo "  launchctl start com.stockbot.live        # one-shot intra-day poll"
echo
echo "Logs:"
echo "  tail -f $AUTO/daily_refresh.log"
echo "  tail -f $AUTO/monthly_retrain.log"
echo "  tail -f $AUTO/live_refresh.log"
