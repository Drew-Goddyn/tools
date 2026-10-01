# Media preservation and worker lifecycle

## Source and publication

Process eligible new `.mov` and `.mp4` files directly in the configured recording
folder. Preserve existing exclusions for screenshots, hidden files, symlinks,
nested content, and recordings present at first setup. Keep completed-recording
history across reruns and upgrades.

Wait for size and modification time to remain stable for 30 seconds and for the
writer to close the file. Check the source signature again before publication.
Leave original contents and names intact. Publish a verified result atomically,
never overwrite an existing output, and preserve recovery of an interrupted
publication without duplicate delivery.

## Media validation

Preserve dimensions, display orientation, frame timing, and audio under the
default policy. Keep HDR, rotated, and higher-bit-depth sources on the exact-copy
path until a compatible conversion is separately implemented and verified.

Count decoded source frames during encoding. Decode the complete output once and
compare dimensions, duration, audio tracks/channels, decoded frame count, and
individual frame timing. Preserve the existing timestamp tolerance, including
variable frame timing; inspect the validator rather than substituting container
frame counts or weakening the check for a new encoder. An exact copy needs a byte
comparison and a full decode.

Audio tracks that can be preserved should remain copied. Retain the existing
validated conversion path for incompatible audio and check every output track.
Quality sampling does not replace any of these integrity checks.

An explicitly requested resize or frame-rate conversion needs its own declared
transform and validation against that transform. Preserve the unchanged default
validator and the original file; label the chosen change in the result.

## Event-driven operation

Retain native launch-on-demand folder triggers. Collect arrivals during processing
and reconcile unfinished or missed work on login/manual resume. Enabled idle means
zero running tool processes: no permanent custom listener, idle polling, or
scheduled safety checks. A worker may wait for a known unfinished recording or a
bounded retry, using the existing event-driven wakeup behavior.

A saved user decision is not runnable retry work. Follow
[interaction.md](interaction.md) when adding needs-choice handling, so it neither
blocks the batch nor starts recurring work while waiting for attention.

This remains a personal convenience tool. A missed trigger or crash may require a
manual rerun; preserve simple recovery without promising guaranteed delivery.

## Pause and recovery

Pause the whole batch until explicitly resumed. Terminate and reap Python/encoder
work, including sample encoders, rather than suspending it in memory. An interrupted
encode may restart from its original on resume. Keep the small native Paused/Resume
menu available, including after login, without recurring worker checks or timers.

Either Resume action must recover interrupted and new recordings; preserve the
one-click shortcut as a fallback. A display failure must not obstruct safe processing
or turn an unanswered decision into approval. Preserve pause state across upgrades.

When changing these paths, exercise corrupt media, truncated output, VFR, audio,
source mutation, publication interruption, pause during sampling/encoding, and
restart with pending work. Originals and unrelated completed output must remain
unchanged. Use [release.md](release.md) for installed upgrade and rollback checks.
