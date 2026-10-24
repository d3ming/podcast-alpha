#!/bin/bash
# One-time: install the launchd agent for the weekday 4 AM run. Does not use sudo.
set -euo pipefail
REPO="$HOME/projects/podcast-alpha"
DEST="$HOME/Library/LaunchAgents/com.dming.podcast-alpha.plist"
mkdir -p "$REPO/logs" "$HOME/Library/LaunchAgents"
chmod +x "$REPO/deploy/mac/run-daily.sh"
sed "s#__HOME__#$HOME#g" "$REPO/deploy/mac/com.dming.podcast-alpha.plist" > "$DEST"
plutil -lint "$DEST"
launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl print "gui/$(id -u)/com.dming.podcast-alpha" | grep -E "state|path" | head -3
cat <<MSG

Installed $DEST (weekdays 04:00).
To wake the Mac before the run, run this yourself once (needs your password):

  sudo pmset repeat wakeorpoweron MTWRF 03:55:00

Test now:  launchctl kickstart -k gui/$(id -u)/com.dming.podcast-alpha ; tail -f $REPO/logs/run-\$(date +%F).log
MSG
