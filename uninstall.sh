#!/bin/bash
# Stops the background agents and the overlay. A wallpaper set in "wallpaper"
# mode stays as it is until you change it.
set -euo pipefail
for LABEL in com.dsa-wallpaper com.dsa-wallpaper.overlay; do
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
done
echo "✓ dsa-wallpaper removed."
