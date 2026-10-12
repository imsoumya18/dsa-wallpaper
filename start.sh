#!/bin/bash
# Starts dsa-wallpaper again after ./stop.sh (it also starts by itself at login).
GUI="gui/$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"
started=0
for LABEL in com.dsa-wallpaper.overlay com.dsa-wallpaper; do
  PLIST="$AGENTS/$LABEL.plist"
  [ -f "$PLIST" ] || continue
  launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$GUI" "$PLIST" && started=1
done
if [ "$started" = 1 ]; then
  echo "▶  dsa-wallpaper running."
else
  echo "Not installed yet — run ./install.sh"
fi
