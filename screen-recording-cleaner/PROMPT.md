# Rebuild prompt

Build me a lightweight personal macOS tool that automatically makes built-in ⌘⇧5 screen recordings easier to share on Discord.

Before building, ask where recordings should come from, where finished copies should go, and how filenames should be handled. Suggest `~/Desktop/screen recordings`, `~/Downloads/screen-recordings`, and the original basename plus ` - clean` as defaults. Own the implementation choices; keep the tool simple.

Use macOS's native launch-on-demand folder triggers. When a recording appears, wait until its size and modification time have been stable for 30 seconds and no process has it open for writing. Process it, collect arrivals during processing, then exit completely. No permanent custom listener, idle polling, or scheduled safety checks. The processor may stay active while a recording or retry is pending.

Treat 20 MB (20,000,000 bytes) as a soft target. Make one high-quality encode, preserving resolution, frame timing, and audio. Keep a useful size reduction even above the target; skip extra encoding passes just to cross that threshold. Files already below it can be copied unchanged; if encoding increases size, copy the original. Verify decoding, dimensions, duration, frame counts, and audio before publishing atomically. Leave originals untouched and never overwrite another file.

Show a compact menu-bar item only while work is pending: the recording, current stage with a step number and total, elapsed time, real frame progress where available, and when frames last advanced. Keep longer stage explanations in the README. Include actions to open finished recordings and pause the whole batch until explicitly resumed. Pausing must stop processing and leave no tool-owned processes running. Provide a one-click resume shortcut that collects unfinished and missed recordings. A failed display must not block processing.

Process only new `.mov` and `.mp4` files directly in the recording folder; ignore screenshots and nested content. Exclude recordings already present at first setup, remember completed files, and preserve that history across reruns and upgrades.

This is a personal convenience tool. An occasional missed trigger or crash can be handled with a manual rerun; keep recovery simple. Include a one-click installer and a short README covering status, rerun, pause/resume, and rollback. Verify that a real ⌘⇧5 recording reaches the destination and the tool returns to zero running processes.
