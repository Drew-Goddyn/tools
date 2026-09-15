#!/bin/bash
set -euo pipefail

agent="${1:-$HOME/Library/LaunchAgents/local.screen-recording-cleaner.plist}"
if [[ ! -f "$agent" ]]; then
  echo "Screen Recording Cleaner is not installed. Run Install.command first."
  exit 1
fi
label=$(/usr/libexec/PlistBuddy -c 'Print :Label' "$agent")
case "$label" in
  local.screen-recording-cleaner|local.screen-recording-cleaner.test.*) ;;
  *) echo "Unrecognized recording cleaner job; left it alone."; exit 1 ;;
esac
domain="gui/$(id -u)"
target="$domain/$label"
/bin/launchctl enable "$target"
if /bin/launchctl print "$target" >/dev/null 2>&1; then
  /bin/launchctl kickstart "$target"
else
  /bin/launchctl bootstrap "$domain" "$agent"
fi
echo "Screen Recording Cleaner resumed. It will collect recordings made while paused."
