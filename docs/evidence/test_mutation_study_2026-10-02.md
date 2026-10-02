# Targeted test mutation study - 2026-10-02

Twenty-five curated semantic mutations were applied temporarily across
ten core modules. Every selected test passed against the unmodified
source, then failed under its corresponding mutation. Source files were
restored after each trial. This was a focused fault-injection study,
not a new mutation-testing framework.

| Module | Temporary mutations | Tests that killed them |
| --- | --- | --- |
| `captain_edits.py` | unknown property, non-numeric value, NaN, non-positive zoom, Pan/Tilt past the rail, timecode frame field, blank reason | `test_a_refused_override_names_what_refused_it`: the matching seven rows |
| `reel_replace_guard.py` | use staging instead of the journaled touch; compare Pan/Tilt across a unit epoch | `test_first_contact_preserves_edits_against_the_right_baseline`: journaled-touch, editor-change, and unit-rescale rows; the unit-epoch mutation killed only the unit-rescale row |
| `resolve_transform.py` | omit draw gain; accept zero dimensions | `test_the_declared_row_converts_to_the_captains_hand_value`; `test_a_zero_dimension_raises_rather_than_dividing` |
| `reel_build.py` | use stream label instead of angle identity; round mute end down; ignore comparison-retirement failure | mic-bleed table's channel-row case; its losing-mic and channel-row cases; promotion-record table's `comparison-retirement` case |
| `versions/reel_versions.py` | increment the sequence number incorrectly; do not append the version | `test_a_rebuild_rolls_back_to_the_version_it_replaced` for both mutations |
| `undo_journal.py` | save an empty after-snapshot; bypass the moved-timeline refusal | the move, remove-with-look, and set-properties rows; `test_undo_refuses_a_timeline_changed_since_the_touch` |
| `reel_look.py` | keep the old shot index; accept an anchored window split by a seam | motion-remap table's `remap-to-same-shot` and `refuse-split-window` cases |
| `resolve_axi.py` | report an off-cursor false as muted | `test_audio_does_not_report_mute_off_the_cursor` |
| `reel_ending.py` | hold the exclusive source-out frame; start the freeze one frame early | `test_a_freeze_holds_the_last_frame_for_the_elements_own_length` for both mutations |
| `editor_edit_carry.py` | read enabled state as true; ignore a cut that still plays | `test_reel_7_cuts_and_disabled_graphic_are_carried_into_the_rebuild`; `test_a_cut_that_still_plays_after_the_write_refuses` |

## What the kill sets show

The related incidents now use parameterized tables without dropping
cases. The first-contact fallback mutation failed only where the
journaled touch is the right baseline; the resolution change has its
own row and its own unit-epoch mutation. In the mic table, the
stream-name error is caught by the channel-row case, while the
frame-rounding mutation is caught by both mute-window cases. The
overlap and picture rows stay green because they should not be muted.

Two pairs of distinct faults share one realistic regression scenario:
the version sequence and append mutations both fail the rollback test,
and the held-frame and freeze-start mutations both fail the freeze
test. Those tests assert separate observable properties inside the
same workflow; neither pair is a reason to keep duplicate tests.

For the transform override validation table, each of the seven invalid
inputs kills its matching guard mutation. The validation cases therefore
remain separate rows within one scenario test rather than separate
test functions.
