# Capability concepts

Generated from `edit_algebra.OP_STATUS`.

Operation ids are editorial semantics. `defined` says the id is in the vocabulary; `compilable` says a listed backend can emit a write; `qualified` records exact live evidence; `production_enabled` is the production gate.

## Operation vocabulary

| operation | required parameters | defined | compilable | qualified | production enabled | verification contract |
|---|---|---:|---:|---:|---:|---|
| `angle_plan` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.automation` | `unique_id`, `param`, `keyframes` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.fade` | `unique_id`, `fade_in`, `fade_out` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.gain` | `unique_id`, `db` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.pan` | `unique_id`, `pan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio_mix` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `brand_asset` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `caption_fix` | `unique_id`, `text` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.delete` | `unique_id` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.insert` | `unique_id`, `track_type`, `track_index`, `record_in`, `record_out`, `media_pool_item` | yes | yes | no | no | post-write snapshot checked by verify |
| `clip.join` | `unique_id`, `other_unique_id` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.link` | `unique_id`, `with_unique_ids` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.move` | `unique_id`, `to_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.replace` | `unique_id`, `source_file` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.ripple` | `unique_id`, `delta_frames` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.roll` | `unique_id`, `delta_frames` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.set_enabled` | `unique_id`, `enabled` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.set_property` | `unique_id`, `key`, `value` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.slide` | `unique_id`, `to_frame` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.slip` | `unique_id`, `delta_frames` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.smart_reframe` | `unique_id`, `keys`, `before` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.split` | `unique_id`, `at_frame` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.trim` | `unique_id`, `in_frame`, `out_frame` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.unlink` | `unique_id` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_lut` | `unique_id`, `lut` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_marker.add` | `unique_id`, `frame`, `color`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_marker.delete` | `unique_id`, `frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.qualifier.adjust` | `unique_id`, `node_index`, `params` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.qualifier.sample` | `unique_id`, `node_index`, `point` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.create` | `unique_id`, `node_index`, `shape`, `geometry` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.delete` | `unique_id`, `node_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.modify` | `unique_id`, `node_index`, `geometry` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.track` | `unique_id`, `node_index`, `direction` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `compound.create` | `unique_id`, `child_unique_ids` | yes | no | no | no | post-write snapshot checked by verify |
| `compound.flatten` | `unique_id` | yes | no | no | no | post-write snapshot checked by verify |
| `drop_fragment` | `unique_id` | yes | no | no | no | post-write snapshot checked by verify |
| `end_card` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade` | `unique_id`, `grade` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_add` | `unique_id`, `node_index` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_remove` | `unique_id`, `node_index` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_reorder` | `unique_id`, `from_index`, `to_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_set` | `unique_id`, `node_index`, `params` | yes | yes | no | no | post-write snapshot checked by _verify_grade_node_set |
| `look` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `marker.add` | `frame`, `color`, `name` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `marker.delete` | `frame` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `mask.create` | `unique_id`, `matte` | yes | yes | no | no | post-write snapshot checked by _verify_mask_create |
| `mask.delete` | `unique_id`, `matte` | yes | yes | no | no | post-write snapshot checked by _verify_mask_delete |
| `mask.edit` | `unique_id`, `matte`, `geometry` | yes | yes | no | no | post-write snapshot checked by _verify_mask_edit |
| `mask.track` | `unique_id`, `matte`, `track` | yes | yes | no | no | post-write snapshot checked by _verify_mask_track |
| `motion_graphic` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `multicam.angle` | `unique_id`, `angle_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `music_selection` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.add` | `row`, `record_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.entry_motion` | `unique_id`, `frames` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.remove` | `unique_id` | yes | yes | no | no | post-write snapshot checked by verify |
| `overlay.resize` | `unique_id`, `width`, `height` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.swap_pixels` | `unique_id`, `file` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `picture.keyframe` | `unique_id`, `param`, `keyframes` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `plan_change` | `node`, `note` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `redraw_closer` | `unique_id`, `start` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `retime` | `unique_id`, `percent` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `retime.segments` | `unique_id`, `segments` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `shot_selection` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `sound_effect` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `span_retime` | `unique_id`, `percent` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `speech_selection` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `story_pacing` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `subject_effect` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.add` | `track_type`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.delete` | `track_type`, `track_index` | yes | yes | no | no | post-write snapshot checked by verify |
| `track.rename` | `track_type`, `track_index`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.reorder` | `track_type`, `from_index`, `to_index`, `name` | yes | yes | no | no | post-write snapshot checked by _verify_track_reorder |
| `transform_override` | `unique_id`, `key`, `value` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `transition` | `carrier`, `transition` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `visual_effect` | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `voice_isolation` | `track_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
