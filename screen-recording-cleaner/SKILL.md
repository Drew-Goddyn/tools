---
name: screen-recording-cleaner
description: Build, update, or diagnose the Screen Recording Cleaner macOS tool and its installed copy. Use for this tool's compression policy, native controls, and release workflow, rather than unrelated video editing.
---

# Screen Recording Cleaner

Make screen recordings easy to share while keeping the Mac responsive. Favor
hardware encoding, small visible quality loss, and useful file sizes. Preserve
originals and let the user decide when their size and quality preferences conflict.

This skill defines the intended product. Inspect the checked-out implementation
and installed configuration before claiming a feature exists; instructions can
lead the running release. The [README](README.md) describes shipped behavior.

## Establish the task

Identify the requested outcome: advice, an instruction change, implementation,
installation, or a release. Keep work within that outcome and carry forward the
user's existing choices and authorization. Updating this skill does not itself
update the running tool.

For an existing installation, discover its paths and preferences instead of
asking the user to repeat them. For a new setup, resolve missing source, output,
and naming choices; suggest `~/Desktop/screen recordings`,
`~/Downloads/screen-recordings`, and the original basename plus ` - clean`.

## Load the relevant guidance

- **Encoding, size, quality, or performance:** read
  [encoding.md](references/encoding.md). It owns compression defaults, candidate
  selection, calibration, and hardware failure handling.
- **Preferences, graphs, previews, or user decisions:** read
  [interaction.md](references/interaction.md), plus the encoding policy when
  exposing size or quality choices.
- **Processing, validation, pause, or recovery:** read
  [preservation.md](references/preservation.md) before changing those behaviors.
- **Repository synchronization, installation, upgrade, or release:** read
  [release.md](references/release.md). It owns migration, provenance, rollback,
  and the distinction between source changes and installed behavior.

Read the references required by the task, not the entire folder automatically.
Follow their conditional links when the corresponding branch is reached.

## Finish with evidence

Instruction changes are complete when their entrypoints and references agree,
links resolve, and skill validation passes. Implementation is complete when the
changed behavior and affected preservation guarantees pass their checks. An
installed upgrade also needs the release checks; a pushed commit alone does not
prove the installed behavior.

Report the user-visible outcome, measured tradeoffs, and remaining uncertainty.
Distinguish sampled similarity, full media integrity, and actual installed
operation. CPU time is evidence about CPU use, not a measurement of fan noise,
energy, or temperature.
