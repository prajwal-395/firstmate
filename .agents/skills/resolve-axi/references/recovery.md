# Recovery metadata

Generated from `edit_algebra.OP_STATUS`.

Inverse metadata is descriptive. Follow the transaction receipt and recovery policy for the actual operation; do not retry a write based only on an exit code.

| operation | inverse | self inverse | irreversible reason |
|---|---|---|---|
| `angle_plan` | `none` | False | a planner re-run has no timeline inverse; its inverse is the previous plan (C-04) |
| `audio.automation` | `none` | True | the inverse carries the previous keyframes |
| `audio.fade` | `none` | True | the inverse carries the previous envelope |
| `audio.gain` | `none` | True | the inverse carries the previous dB |
| `audio.pan` | `none` | True | the inverse carries the previous pan |
| `audio_mix` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `brand_asset` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `caption.style` | `none` | True | the inverse carries the previous style |
| `caption_fix` | `none` | True | the inverse carries the previous text |
| `clip.delete` | `clip.insert` | False | re-inserts the deleted source range |
| `clip.insert` | `clip.delete` | False | none |
| `clip.join` | `clip.split` | False | none |
| `clip.link` | `clip.unlink` | False | none |
| `clip.move` | `none` | True | the inverse moves the clip back |
| `clip.remove` | `clip.insert` | False | re-inserts the removed source range |
| `clip.replace` | `none` | True | the inverse carries the previous source |
| `clip.ripple` | `none` | True | the inverse carries the opposite delta |
| `clip.roll` | `none` | True | the inverse carries the negated delta |
| `clip.set_enabled` | `none` | True | the inverse carries the previous state |
| `clip.set_property` | `none` | True | the inverse carries the previous value |
| `clip.slide` | `none` | True | the inverse moves the clip back |
| `clip.slip` | `none` | True | the inverse carries the negated delta |
| `clip.smart_reframe` | `clip.set_property` | False | restores the measured framing properties |
| `clip.split` | `clip.join` | False | none |
| `clip.trim` | `none` | True | the inverse carries the previous in/out points |
| `clip.unlink` | `clip.link` | False | none |
| `clip_lut` | `none` | True | the inverse carries the previous LUT |
| `clip_marker.add` | `clip_marker.delete` | False | none |
| `clip_marker.delete` | `clip_marker.add` | False | re-adds the deleted marker's fields |
| `color.qualifier.adjust` | `none` | True | the inverse carries the previous qualifier params |
| `color.qualifier.sample` | `none` | True | the inverse carries the previous sampled range |
| `color.window.create` | `color.window.delete` | False | delete removes the window create placed |
| `color.window.delete` | `color.window.create` | False | create draws the window delete removed, with its shape and geometry |
| `color.window.modify` | `none` | True | the inverse carries the previous geometry |
| `color.window.track` | `none` | True | the inverse carries the previous window track |
| `compound.create` | `compound.flatten` | False | none |
| `compound.flatten` | `compound.create` | False | none |
| `drop_fragment` | `clip.insert` | False | re-inserts the dropped passage |
| `end_card` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `grade` | `none` | True | the inverse carries the previous grade |
| `grade.node_add` | `grade.node_remove` | False | none |
| `grade.node_remove` | `grade.node_add` | False | none |
| `grade.node_reorder` | `none` | True | the inverse swaps the nodes back |
| `grade.node_set` | `none` | True | the inverse carries the previous node grade |
| `hold.repair_window` | `none` | True | the inverse carries the previous window |
| `look` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `marker.add` | `marker.delete` | False | none |
| `marker.delete` | `marker.add` | False | re-adds the deleted marker's fields |
| `mask.create` | `mask.delete` | False | none |
| `mask.delete` | `mask.create` | False | then mask.edit and mask.track restore its geometry and keys |
| `mask.edit` | `none` | True | the inverse carries the previous geometry |
| `mask.track` | `none` | True | the inverse carries the previous keyframes (empty means none) and the previous geometry |
| `motion_graphic` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `multicam.angle` | `none` | True | the inverse carries the previous angle |
| `multicam.select_angle` | `none` | True | the inverse carries the previous angle |
| `music_selection` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `overlay.add` | `overlay.remove` | False | none |
| `overlay.entry_motion` | `none` | True | the inverse clears the animation |
| `overlay.position` | `none` | True | the inverse carries the previous position |
| `overlay.remove` | `overlay.add` | False | re-places the removed item |
| `overlay.replace` | `none` | True | the inverse carries the previous file |
| `overlay.resize` | `none` | True | the inverse carries the previous size |
| `overlay.swap_pixels` | `none` | True | the inverse carries the previous file |
| `picture.keyframe` | `none` | True | the inverse carries the previous keyframes |
| `plan_change` | `none` | False | a carrier delivered to a planning node; its inverse is the previous plan (C-04) |
| `redraw_closer` | `none` | True | the inverse carries the previous start |
| `retime` | `none` | True | the inverse carries the previous speed |
| `retime.segments` | `none` | True | the inverse carries the previous speed |
| `shot_selection` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `sound_effect` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `span_retime` | `none` | True | the inverse carries the previous ranges |
| `speech_selection` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `story_pacing` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `subject_effect` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `track.add` | `track.delete` | False | none |
| `track.delete` | `track.add` | False | re-adds the row with its name |
| `track.rename` | `none` | True | the inverse carries the previous name |
| `track.reorder` | `none` | True | the inverse carries the previous index |
| `transform_override` | `none` | True | the inverse carries the previous value |
| `transition` | `none` | False | transition deletion has no qualified compensation |
| `visual_effect` | `none` | False | a planner re-run has no timeline inverse (C-04) |
| `voice_isolation` | `none` | True | the inverse restores the pre-isolation state |
