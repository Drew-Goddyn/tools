# Preferences and user decisions

## Separate size from quality

Use [encoding.md](encoding.md) for policy defaults and quality calibration. Give
users an editable preferred size and flexible upper bound, with a separate hard
limit only when needed. Expose quality as Close to original, Balanced, and Smaller
and softer. Keep resolution and smoothness changes separate from those labels.

The default interaction is to finish automatically when saved preferences can be
met and ask when they conflict. Support a user-selected review-every-recording
mode. Discover existing preferences during upgrades; setup only asks for missing
choices. A decision applies to the current recording unless the user explicitly
selects Save as my default.

## Show the tradeoff

Use a compact native preferences/review window reachable from the existing menu
and an on-demand launcher when the worker is idle. Keep implementation details
such as codecs and metric names in optional details; lead with what the user gets.

The review graph plots file size against calibrated visual similarity. Mark the
preferred size, flexible boundary, and selected quality preference. Plot measured
sample results as such; label projected full-file sizes and interpolated curves
as estimates. Do not draw a reassuring quality curve without supporting samples.

Selecting a candidate should show:

- Estimated or actual file size and expected processing time.
- A synchronized source/output preview and a native-scale text or detail crop.
- Whether resolution or motion would change.

Recommend the smallest candidate that satisfies the selected quality. If none
fits, offer a larger result, an explicitly lower quality choice, or the original.
Put resizing, reduced frame rate, compatibility changes, and software encoding
behind explicit choices with their consequences. Keeping the original can itself
leave a size preference unmet; say so.

## Make decisions recoverable

Save a needs-choice state distinct from encoding failures and automatic retries.
Notify once, let later recordings proceed, and exit the worker when no runnable
work remains. Closing a window, ignoring a notification, or a failed display is
not a choice to lower quality or use software.

Persist enough structured data to reopen the decision: source identity/signature,
policy revision, candidate settings and measurements, current recommendation,
and the user's selected action. Keep candidates associated with their source;
invalidate stale choices when the source changes. Apply decisions atomically and
make repeated clicks harmless before waking the on-demand worker.

Keep pending choices accessible across restart without requiring a resident
listener or polling. An explicitly opened review window may remain open; pending
decisions alone must not keep Python or an encoder alive. Release encoder resources
before waiting for the user.

## Preserve the existing controls

Keep the compact film menu during processing, real stage/frame progress, elapsed
time, and the last time frames advanced. Describe actual candidate/validation work
rather than showing invented progress or a fixed stage count that no longer fits.
Keep Open finished recordings and batch Pause/Resume available. The pause and idle
contract is in [preservation.md](preservation.md); load it when changing their
implementation.

Verify the choices users can actually make: saved preferences, an unmet target,
a dismissed decision, repeated/stale input, UI failure, restart, and pause while
previews or encoding are active. Distinguish programmatic native-control checks
from a physical click or a newly captured recording.
