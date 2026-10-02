# Edit preservation scenarios

`tests/unit/resolve/test_editor_edit_carry.py` holds the compact
first-contact scenario table. `library/tools/reel_replace_guard.py`
owns the baseline choice and comparison; `library/tools/editor_edit_carry.py`
owns carrying accepted edits onto staging.

## The incident

On Reel 09, the live timeline matched the `after` snapshot from its
2026-10-01 touch, but the next staging changed framing, subtitle cuts,
and a disabled graphic. With no recorded timeline snapshot, first
contact compared live against staging. That made Ren's own rebuild look
like editor work and could carry old values back onto the new cut.

The touch journal's `after` snapshot is Ren's last known state. First
contact uses it when there is no newer build snapshot, so only changes
made after that touch count as editor changes. A newer build snapshot
supersedes the touch and staging becomes the baseline.

## Pan/Tilt units

On 2026-10-02, changing the project resolution multiplied stored
Pan/Tilt values by four while the picture stayed in place. The touch
journal does not record a transform-unit epoch, so those values cannot
be compared across that change. First contact reads live Pan/Tilt for
the matching item and compares the remaining properties normally.

## Preserved cases

The parameterized test covers four cases: untouched since the journaled
touch, a real enabled-state edit after the touch, a newer build that
supersedes the touch, and a resolution-unit rescale that is not an edit.
The production contract remains in `detect_editor_changes`; this file
keeps the incident context out of executable test branches.
