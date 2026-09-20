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
resume_legacy_worker() {
  /bin/launchctl enable "$target"
  if /bin/launchctl print "$target" >/dev/null 2>&1; then
    /bin/launchctl kickstart "$target"
  else
    /bin/launchctl bootstrap "$domain" "$agent"
  fi
}
worker=$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:1' "$agent")
menu="$(dirname -- "$worker")/Recording Progress.app/Contents/MacOS/RecordingProgress"
if [[ -x "$menu" ]]; then
  # The native command shares a short-lived lock with paused-menu startup.
  if "$menu" --resume "$agent"; then
    :
  else
    result=$?
    # Older installed menus did not have a resume command.
    if [[ $result -eq 64 ]]; then resume_legacy_worker; else exit "$result"; fi
  fi
else
  resume_legacy_worker
fi
if [[ "${2:-}" != "--from-menu" ]]; then
  # The paused menu waits for this event; it never polls service state.
  /bin/launchctl kill SIGUSR1 "$target.paused-menu" >/dev/null 2>&1 || true
fi
echo "Screen Recording Cleaner resumed. It will collect recordings made while paused."
