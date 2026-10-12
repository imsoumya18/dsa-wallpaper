#!/bin/bash
# Installs dsa-wallpaper.
#   overlay mode   : builds the overlay app and runs it in the background
#   wallpaper mode : sets the rendered image as the desktop picture
# In both modes a background agent re-renders whenever the sheet is saved.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.dsa-wallpaper"
OVERLAY_LABEL="com.dsa-wallpaper.overlay"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
OVERLAY_PLIST="$AGENTS/$OVERLAY_LABEL.plist"
PY="$DIR/.venv/bin/python"
GUI="gui/$(id -u)"

cd "$DIR"

if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Created config.json — set \"sheet_path\" in it, then run ./install.sh again."
  exit 1
fi

command -v python3 >/dev/null || { echo "python3 not found. Run: xcode-select --install"; exit 1; }

echo "→ Setting up Python environment"
[ -x "$PY" ] || python3 -m venv .venv
"$PY" -m pip install -q --upgrade pip pillow

cfg() { "$PY" - "$1" <<'PY'
import sys, dsa_wallpaper as m
c = m.load_config(); k = sys.argv[1]
print(m.resolve(c[k]) if k == "sheet_path" else c[k])
PY
}
SHEET="$(cfg sheet_path)"
MODE="$(cfg mode)"
LEVEL="$(cfg overlay_level_offset)"
[ -f "$SHEET" ] || { echo "Sheet not found: $SHEET"; exit 1; }

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }
mkdir -p "$AGENTS" out

# stop anything from a previous install
launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
launchctl bootout "$GUI/$OVERLAY_LABEL" 2>/dev/null || true
rm -f "$OVERLAY_PLIST"

if [ "$MODE" = "overlay" ]; then
  echo "→ Building overlay app"
  command -v swiftc >/dev/null || { echo "swiftc not found. Run: xcode-select --install"; exit 1; }
  swiftc -O -swift-version 5 overlay/Overlay.swift -o out/dsa-overlay

  cat > "$OVERLAY_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$OVERLAY_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$DIR/out/dsa-overlay")</string>
    <string>$(xml "$DIR/out/overlay.png")</string>
    <string>$(xml "$DIR/out/screen.json")</string>
    <string>$LEVEL</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$(xml "$DIR/out/overlay-log.txt")</string>
  <key>StandardErrorPath</key><string>$(xml "$DIR/out/overlay-log.txt")</string>
</dict>
</plist>
EOF
  echo "→ Starting overlay (it measures your screen)"
  rm -f out/screen.json
  launchctl bootstrap "$GUI" "$OVERLAY_PLIST"
  for _ in $(seq 1 20); do [ -f out/screen.json ] && break; sleep 0.25; done
  [ -f out/screen.json ] || echo "  (screen size not reported yet — using a fallback)"
fi

echo "→ Rendering"
"$PY" dsa_wallpaper.py

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$PY")</string>
    <string>$(xml "$DIR/dsa_wallpaper.py")</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml "$DIR")</string>
  <key>WatchPaths</key>
  <array>
    <string>$(xml "$SHEET")</string>
    <string>$(xml "$DIR/config.json")</string>
    <string>$(xml "$DIR/out/screen.json")</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>3</integer>
  <key>StandardOutPath</key><string>$(xml "$DIR/out/log.txt")</string>
  <key>StandardErrorPath</key><string>$(xml "$DIR/out/log.txt")</string>
</dict>
</plist>
EOF

echo "→ Registering background agent"
launchctl bootstrap "$GUI" "$PLIST"

echo "✓ Done ($MODE mode). It refreshes every time you save:"
echo "  $SHEET"
