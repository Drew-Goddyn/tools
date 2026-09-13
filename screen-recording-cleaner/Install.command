#!/bin/bash
# Finder entry point. All paths are relative to this downloaded package.
set -euo pipefail

bundle=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
brew_installer=""
finish() {
    result=$?
    trap - EXIT
    if [[ -n "$brew_installer" ]]; then rm -f -- "$brew_installer"; fi
    if [[ $result -ne 0 ]]; then
        printf '\nSetup stopped. Fix the error above, then run this installer again. Recording history is retained.\n'
    fi
    if [[ -t 0 ]]; then read -r -p 'Press Return to close this window. ' _ || true; fi
    exit "$result"
}
trap finish EXIT

if [[ $(uname -s) != Darwin ]]; then printf 'This tool requires macOS.\n' >&2; exit 1; fi
if [[ $EUID -eq 0 ]]; then printf 'Run this installer as your normal macOS user, without sudo.\n' >&2; exit 1; fi

export PATH="${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}:/opt/homebrew/bin:/usr/local/bin"
printf 'Installing Screen Recording Cleaner for this macOS account.\n'
printf 'Setup will configure Screenshot to save in the watched folder; macOS uses that location for screenshots too.\n\n'

if ! command -v brew >/dev/null 2>&1; then
    printf 'Homebrew is needed for Python and FFmpeg. Its official installer will explain its changes.\n'
    brew_installer=$(mktemp -t recording-cleaner-homebrew)
    curl --fail --location --proto '=https' --tlsv1.2 \
        https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh --output "$brew_installer"
    /bin/bash "$brew_installer"
fi
brew=$(command -v brew)
brew_prefix=$("$brew" --prefix)
export PATH="$brew_prefix/bin:$PATH"

python="$brew_prefix/bin/python3"
if [[ ! -x "$python" ]] || ! "$python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    "$brew" install python
fi
if [[ ! -x "$brew_prefix/bin/ffmpeg" || ! -x "$brew_prefix/bin/ffprobe" ]]; then
    "$brew" install ffmpeg
fi

"$python" "$bundle/install.py" setup
printf '\nInstalled. Record with Command-Shift-5 as usual. Clean copies arrive in Downloads/screen-recordings.\n'
printf 'Allow the macOS Desktop/Downloads folder prompts for the helper when they appear.\n'
printf 'The first copy waits for 30 seconds of stability, then any encoding and validation time.\n'
printf 'The processor exits completely when no unfinished recordings or retries remain.\n'
