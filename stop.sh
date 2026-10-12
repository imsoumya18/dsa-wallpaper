#!/bin/bash
# Hides the overlay and pauses auto-refresh until you log in again
# (or run ./start.sh). Nothing is uninstalled.
GUI="gui/$(id -u)"
for LABEL in com.dsa-wallpaper.overlay com.dsa-wallpaper; do
  launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
done
echo "⏸  dsa-wallpaper stopped. It starts again at your next login, or run ./start.sh."
