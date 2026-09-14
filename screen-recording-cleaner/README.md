# Screen Recording Cleaner

Record with **⌘⇧5**. A smaller, verified copy appears in **`~/Downloads/screen-recordings`**. The original stays in **`~/Desktop/screen recordings`**, with its filename and contents intact.

macOS launches the processor when the recording folder changes. It waits for unfinished recordings, processes them, and **exits completely when there is no work left**. There is no resident helper, periodic scan, or idle timer. This is a personal convenience tool; a missed notification or crash can require a manual rerun.

A small **film icon in the menu bar appears while work is pending**. Click it to see the recording, current step, elapsed time, and actual frame progress during compression and playback verification. It disappears when processing finishes.

To rebuild this tool with an agent, use the [rebuild prompt](PROMPT.md).

## One-click install

1. [Download this repository as a ZIP](https://github.com/Drew-Goddyn/tools/archive/refs/heads/main.zip) and extract it, or clone it.
2. Open this folder and **double-click `Install.command`**. Keep the folder together.

The installer sets up Homebrew, Python 3.11+, and FFmpeg if needed, then installs one login job for your macOS account. Homebrew may ask for your password or Apple's Command Line Tools; its [current system requirements](https://docs.brew.sh/Installation) apply. It compiles the small menu-bar display with the Swift compiler from Apple's Command Line Tools before changing an existing service. Run as your normal user, without `sudo`. Apple Silicon has been tested; the installer also recognizes Intel Homebrew paths.

**Allow macOS's Desktop and Downloads folder prompts for Python when they appear.** The first save may wait for that permission. The installer does not grant itself privacy permissions or configure Full Disk Access.

Setup sets Screenshot's save location to the recording folder. macOS shares that setting between screenshots and recordings, so screenshots go there too; the processor ignores them. Existing installations retain their configured folders. Close an already-open Screenshot toolbar before recording again.

Terminal, Codex, and this downloaded package can all be closed afterward. The installed copy lives in `~/Library/Application Support/Screen Recording Cleaner`. Run `Install.command` again to upgrade or resume interrupted setup without resetting history.

## Everyday behavior

- New immediate `.mov` and `.mp4` files are eligible. Screenshots, hidden files, symlinks, and nested folders are ignored.
- A recording must keep the same size and modification time for **30 seconds** and have no open writer before processing starts. Encoding and validation take additional time.
- Finished names end in `- clean`. Destination files are never overwritten. Recordings present before first installation stay excluded.
- **20,000,000 bytes is a soft target**, with **picture quality and processing time taking priority**. A large reduction is useful even when the result stays above 20 MB; the cleaner keeps that result without extra size-targeting passes.
- Notifications are requested for failures and files retained above 20 MB. Routine completion is quiet.

## Quality and original protection

Files already under the target are copied exactly, preserving their container. Larger standard SDR recordings are encoded as H.264 MP4 at the original resolution and frame timing, using CRF 18 and four encoder threads. AAC and ALAC audio are copied; other audio formats are converted to AAC. HDR, rotated, and higher-bit-depth recordings keep their original format.

The cleaner makes one high-quality encode and keeps it, even above 20 MB. It does not run additional bitrate-targeted encodes or comparisons just to cross that threshold. If encoding increases size, the original is copied.

Validation checks decoding, dimensions, duration, decoded frame counts, audio track/channel counts, and whether the source changed. Publication is atomic and never replaces a file. Saved publication intent allows interrupted publication to recover without duplicating a finished copy.

## How it starts and stops

One `launchd` job uses `WatchPaths` for the recording folder and `RunAtLoad` for a reconciliation at login or resume. It runs with background priority and low-priority disk access. It has no `KeepAlive`, `StartInterval`, or calendar schedule.

The Python processor scans for work and owns all history, encoding, and publication. While a known recording is unfinished, it waits for its next readiness or retry deadline. A temporary native directory watch can wake that wait when another file arrives. Failed recordings retain the existing retry backoff, up to one hour. Deleting a pending recording removes that work. Once nothing is pending, the processor closes its watch and exits; FFmpeg runs only during processing.

Files arriving during encoding are collected before exit. Duplicate notifications are harmless because history prevents repeated output. A startup scan collects recordings made while the job was stopped.

**Delivery is best effort.** Apple's `launchd.plist` manual warns that `WatchPaths` notifications can be missed. A final folder check reduces the shutdown gap but cannot eliminate it. A crash, a notification missed at shutdown, or a removed/replaced recording folder can leave a recording waiting for the next folder change, login, or manual rerun. There is no automatic crash-restart loop or periodic recovery check. Originals and processing history remain available for recovery. A corrupt unfinished file can keep the processor waiting on retries until it is fixed or removed from the watched folder.

This tradeoff keeps the current folders and a fully exiting processor. A persistent listener or an inbox that empties after processing would provide stronger automatic recovery, at the cost of a different workflow. They are deliberately outside this version. See [Apple's launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) and the local `man launchd.plist` for the native trigger behavior.

## Status and recovery

**Usually, just click the film icon in the menu bar.** It shows:

- The current recording and step: waiting for the recording to finish, inspecting, compressing, verifying, or saving.
- A percentage and frame count during compression and playback verification. The percentage belongs to that step; validation follows compression.
- Time spent in the step and when the frame count last advanced. If frames stop advancing for a minute, it says so without claiming the process is definitely stuck. Inspection and copying have no percentage because those operations do not report frame progress.
- **Open finished recordings**, which opens the configured output folder.

The display takes no keyboard focus, opens no window, and adds no Dock icon. It starts only for known work, receives updates through a pipe, and exits when the processor closes that pipe or dies. It does not watch folders or run an idle schedule. One delivery thread waits on pipe events while the display is active, retaining the newest snapshot if the display is slow. A failed display does not block processing. Set `"show_progress": false` in the installed `config.json` to disable it for subsequent runs.

Read processing history and the most recent run:

```sh
python3 "$HOME/Library/Application Support/Screen Recording Cleaner/cleaner.py" \
  --config "$HOME/Library/Application Support/Screen Recording Cleaner/config.json" status
```

`runner.state: idle` means that run finished. `waiting` means a recording or retry was pending. The saved snapshot can be stale after a crash; check whether macOS currently has a process running:

```sh
launchctl print "gui/$(id -u)/local.screen-recording-cleaner"
```

When idle, it should report `state = not running` with no PID and `last exit code = 0`.

**If a recording has not appeared, rerun processing:**

```sh
launchctl kickstart "gui/$(id -u)/local.screen-recording-cleaner"
```

This leaves an already-running encode alone. Check for a macOS Downloads permission prompt if saving appears stuck. Errors are in the status output and the installed `launchd.log` or rotating `state/events.log`. Do not delete `state/state.json`: it contains processing history and protects the old archive from reprocessing.

Pause until next login:

```sh
launchctl bootout "gui/$(id -u)/local.screen-recording-cleaner"
```

Resume and collect any missed recordings:

```sh
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/local.screen-recording-cleaner.plist"
```

For a pause across logins, move that plist outside `~/Library/LaunchAgents` after pausing, and restore it before resuming. Recordings and history stay intact.

## Upgrade and rollback

`Install.command` waits for an active encode, takes the existing history lock, backs up the installed programs and launch configuration, and replaces them. A previous resident listener is removed. The new job starts after the lock is released. Folder settings and the latest processing history are preserved.

The menu display is built before cutover. A compiler failure leaves the current service running. Rollback restores the previous display, or removes it when rolling back to a version that did not have one.

Setup checks that the processor starts or finishes successfully. If activation fails after replacement, it restores the prior service. Interrupted upgrades can resume from the same package. Keep this package to run these management commands:

```sh
# Restore the previous programs and launch configuration, retaining current history.
python3 install.py rollback

# Restore Screenshot's save location from before first setup.
python3 install.py restore-capture-location
```

Backups and upgrade progress live in the installed `backups/` and `upgrade.json`. Rollback never copies an old ledger over newer results. It restores the previous scheduling behavior, which may include a resident listener or polling. Restoring the Screenshot preference is separate and respects a location you selected yourself after installation.

## Development and verification

```sh
python3 -m unittest -v test_progress test_cleaner test_install test_upgrade test_setup
python3 -m unittest -v test_progress_native
python3 -m unittest -v test_lifecycle
python3 verify_idle.py --output /tmp/recording-cleaner-idle.json
```

The first suite tests encoding, quality, history, publication, installation, actual FFmpeg progress, and broken or backpressured display pipes; launchctl and preference commands are doubled in installer tests. A paced generated clip proves intermediate frame advances without depending on a large fixture. The lifecycle suite creates a temporary real macOS launch job and generated movies under `/private/tmp`, then unloads it. It checks actual creation/copy/rename triggers, slow writes, overlapping arrivals, completion, and recovery after a stopped or crashed run. A command sandbox may require normal macOS service access for these tests.

Build the display with `python3 build_progress.py`. The opt-in native display tests briefly show a test menu item, check normal exit on pipe closure, and kill an isolated parent to check crash cleanup. They do not click or visually inspect the menu. For desktop verification, check the menu during real processing, including the change from compression to verification. The display uses [Apple's native status-item API](https://developer.apple.com/documentation/appkit/nsstatusitem) and [FFmpeg's documented progress output](https://ffmpeg.org/ffmpeg.html). It receives full snapshots with the time frames actually advanced, so delayed display updates do not pretend a frame just advanced.

The idle observer is manually invoked and never installed as a service. It requires five quiet minutes with no job PID, display or worker-group processes, launches, scans, waits, or status/log changes. It fails if recording activity occurs during that window. These checks demonstrate the tested cases; they do not turn `WatchPaths` into guaranteed delivery.

A folder notification can arrive just after a run exits and cause one more no-work run. Let notifications from the last recording or deletion finish before observing idle; this is distinct from a recurring check.
