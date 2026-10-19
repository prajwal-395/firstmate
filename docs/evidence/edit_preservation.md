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

## Native state preservation contract (VC-31)

Each replacement report carries a preservation matrix. `full_preservation`
is false while any property is only partly observable or unsupported. A
successful row/count comparison does not prove that every Resolve-native
property survived.

| Property | Class | Evidence and limit |
|---|---|---|
| Source identity and source windows | Supported | `reel_read` supplies source identity and frame windows; the guard compares snapshots. |
| Placement, duration, and track | Supported | Record spans and track identity are read and compared by replacement guards. |
| Linked groups | Unsupported | The replacement snapshot does not inventory Resolve link-group identity. |
| Transform and crop | Partially observable | Edit-page properties are read; Pan/Tilt depend on the project-resolution unit. Crop/mask state is not completely represented. |
| Enabled state | Supported | A failed enabled-state read refuses the snapshot. |
| Clip and timeline markers | Partially observable | Both planes are read and carried where anchors resolve; unanchored notes cannot be proven equivalent. |
| Grades and node state | Partially observable | CDL, group, and node LUT summaries are read, but they are not a complete grade graph or guaranteed native restore. A non-default observed grade that differs or disappears on the matching staged passage now refuses replacement. |
| Fusion comps and animation | Partially observable | Comp names/count, media windows, and selected matte geometry/keyframes are read. Arbitrary comp tools and animation are not fully captured. A changed observed comp summary refuses replacement. |
| Audio effects, automation, and routing | Unsupported | The replacement snapshot has no complete inventory of these properties. |
| Manual edits | Partially observable | Cuts, trims, moves, enabled state, transforms, and some markers carry. Other edits refuse through `protect_editor_changes`; no complete representation of every native operation exists. |
| Sign-offs | Supported | Durable sign-offs are checked before replacement and require explicit supersession. This is approval state, not a Resolve-native clip property. |

Unsupported and partial classes are never silently promoted into a full
preservation claim. Where the reader observes non-default grade, Fusion, or
composite state, the guard compares it against the unique staged item for the
same source passage and refuses if it is missing or differs. This covers the
Reel 26 lost-look shape when the look is represented in the observable
snapshot; it does not claim to detect state outside the reader's coverage.

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
