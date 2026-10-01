# Repository and installed release

## Discover current state

Find the repository checkout and installation rather than assuming they match.
Use current files, service arguments, configuration, and the installation journal
to establish provenance. The default macOS support directory is
`~/Library/Application Support/Screen Recording Cleaner`; user configuration may
choose other recording/output folders. Recheck live state immediately before an
upgrade, including pause, running work, pending decisions, and local edits.

Keep the repository as the source of the installed release. Retain a one-click
installer and brief user instructions for status, rerun, pause/resume, and rollback.

## Implement one consistent release

Use [encoding.md](encoding.md), [interaction.md](interaction.md), and
[preservation.md](preservation.md) as the corresponding implementation branches
are reached. Keep defaults in one authoritative policy and have the UI reflect it.
The skill describes product intent; the README describes behavior actually shipped.
Revise conflicting instructions in place instead of adding exceptions beneath them.

Keep `PROMPT.md` a short handoff to the skill, not a second copy of the specification.
Update the skill references when product decisions change. Update runtime code,
installer/upgrader, affected tests, and user documentation together when shipping
those decisions. Preserve historical measurements as historical and label new proof
by revision and execution context.

For compression changes, benchmark original captures under matched scheduling.
Include analysis, encoding, validation, and publication in complete processing
time; report the capture-stability wait separately. The current Standard launchd
class with nice level 10 and low-priority disk access is the measured baseline;
Background throttling confounded earlier results. Measure under the actual service
context as well as isolated media checks.

Target a clear speed and CPU reduction while meeting the selected quality profile.
Calibrate budgets from those results, not from encoder-only timing. Report missed
performance goals rather than weakening integrity checks or hiding analysis cost.
Read [calibration.json](calibration.json) only when these prior measurements help
select or interpret the next experiment.

## Migrate without losing history

Use the existing locked, journaled upgrade mechanism. Extend it for versioned size,
quality, and interaction preferences. Preserve folders, filenames, notification
choices, pause state, archive exclusions, and completed-recording history. For this
user's agreed update, replace the legacy soft-size default with the policy in
[encoding.md](encoding.md); preserve other users' explicit custom limits.

Make preference migration and decision-state changes reversible. Inspect rollback:
at the initial hardware-policy rewrite, it backed up configuration but restored
programs and service definitions without restoring changed preferences. Avoid
blindly restoring an old history snapshot, which could discard newer deliveries.
Preserve later user preference edits and unresolved decisions when reverting the
migration. Define how the prior runtime safely handles those decisions before
claiming rollback works.

Record the source revision, installed-file hashes, effective policy version, and
migration outcome in structured installation evidence. Install the exact tested
revision; avoid a separate hand-patched installed implementation.

## Verify the requested change

For skill/documentation changes, run the skill validator, check reference links,
review the complete diff and current-versus-intended behavior claims, and confirm
that the pushed revision contains the intended files. Those changes alone do not
require certifying the media implementation.

For consequential implementation, obtain a fresh independent review with the
user's scope and preservation criteria. Run affected repository checks, real-media
cases, and native lifecycle checks. Include a slightly oversize preferred result,
a larger candidate selected for quality, impossible limits, hardware access failure,
UI dismissal, stale choices, bounded attempts, and continued batch processing.
Investigate failures; compare the original revision before labeling one pre-existing.

For an installed upgrade, verify real watched-folder delivery, pending choices,
pause/resume, idle exit, preserved history, and rollback before confirming the
journal. Ensure the setup path does not automatically confirm after startup alone.
If the user has left processing paused, keep it paused: complete isolated delivery
and rollback checks, verify the installed paused state and hashes, and leave live
delivery and journal confirmation pending until an explicitly requested resume.
Report that boundary instead of waking the live queue merely to finish a check.

Keep synthetic, copied-fixture, native UI, and fresh keyboard-capture proof distinct.
Read installed hashes/settings back after installation and the remote commit after
a requested push. Report which copy changed and what was actually verified.
