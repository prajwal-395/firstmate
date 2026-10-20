# Verification contracts

Generated from `edit_algebra.OP_STATUS`.

The contract names the operation's read-back verifier. A successful API return alone is not verification.

## Operation contracts

| operation | implementation status | required parameters | defined | compilable | qualified | production enabled | verification contract |
|---|---|---|---:|---:|---:|---:|---|
| `angle_plan` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.automation` | no_executor | `unique_id`, `param`, `keyframes` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.fade` | implemented_unqualified | `unique_id`, `fade_in`, `fade_out` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.gain` | implemented_unqualified | `unique_id`, `db` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio.pan` | no_executor | `unique_id`, `pan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `audio_mix` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `brand_asset` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `broll.insert` | implemented_qualified | `unique_id`, `track_type`, `track_index`, `record_in`, `record_out`, `source_in`, `source_out`, `clip` | yes | yes | yes | yes | post-write snapshot checked by _verify_broll_insert |
| `caption.style` | implemented_qualified | `unique_id`, `style` | yes | yes | yes | yes | post-write snapshot checked by _verify_caption_style |
| `caption_fix` | implemented_unqualified | `unique_id`, `text` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.delete` | implemented_qualified | `unique_id` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.insert` | implemented_unqualified | `unique_id`, `track_type`, `track_index`, `record_in`, `record_out`, `media_pool_item` | yes | yes | no | no | post-write snapshot checked by verify |
| `clip.join` | no_executor | `unique_id`, `other_unique_id` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.link` | implemented_unqualified | `unique_id`, `with_unique_ids` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.move` | implemented_unqualified | `unique_id`, `to_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.remove` | implemented_qualified | `unique_id` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.replace` | implemented_unqualified | `unique_id`, `source_file` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.ripple` | implemented_unqualified | `unique_id`, `delta_frames` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.roll` | implemented_unqualified | `unique_id`, `delta_frames` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.set_enabled` | implemented_unqualified | `unique_id`, `enabled` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.set_property` | implemented_qualified | `unique_id`, `key`, `value` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.slide` | implemented_unqualified | `unique_id`, `to_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.slip` | implemented_unqualified | `unique_id`, `delta_frames` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.smart_reframe` | implemented_unqualified | `unique_id`, `keys`, `before` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.split` | implemented_unqualified | `unique_id`, `at_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.trim` | implemented_unqualified | `unique_id`, `in_frame`, `out_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.unlink` | implemented_unqualified | `unique_id` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_lut` | no_executor | `unique_id`, `lut` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_marker.add` | implemented_unqualified | `unique_id`, `frame`, `color`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_marker.delete` | implemented_unqualified | `unique_id`, `frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.qualifier.adjust` | no_executor | `unique_id`, `node_index`, `params` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.qualifier.sample` | no_executor | `unique_id`, `node_index`, `point` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.create` | implemented_unqualified | `unique_id`, `node_index`, `shape`, `geometry` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.delete` | no_executor | `unique_id`, `node_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.modify` | implemented_unqualified | `unique_id`, `node_index`, `geometry` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `color.window.track` | implemented_unqualified | `unique_id`, `node_index`, `direction` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `compound.create` | no_executor | `unique_id`, `child_unique_ids` | yes | no | no | no | post-write snapshot checked by verify |
| `compound.flatten` | no_executor | `unique_id` | yes | no | no | no | post-write snapshot checked by verify |
| `drop_fragment` | no_executor | `unique_id` | yes | no | no | no | post-write snapshot checked by verify |
| `end_card` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade` | no_executor | `unique_id`, `grade` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_add` | implemented_unqualified | `unique_id`, `node_index` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_remove` | implemented_unqualified | `unique_id`, `node_index` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_reorder` | no_executor | `unique_id`, `from_index`, `to_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_set` | implemented_unqualified | `unique_id`, `node_index`, `params` | yes | yes | no | no | post-write snapshot checked by _verify_grade_node_set |
| `hold.repair_window` | implemented_unqualified | `unique_id` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `look` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `marker.add` | implemented_unqualified | `frame`, `color`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `marker.delete` | implemented_unqualified | `frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `mask.create` | implemented_unqualified | `unique_id`, `matte` | yes | yes | no | no | post-write snapshot checked by _verify_mask_create |
| `mask.delete` | implemented_unqualified | `unique_id`, `matte` | yes | yes | no | no | post-write snapshot checked by _verify_mask_delete |
| `mask.edit` | implemented_unqualified | `unique_id`, `matte`, `geometry` | yes | yes | no | no | post-write snapshot checked by _verify_mask_edit |
| `mask.track` | implemented_unqualified | `unique_id`, `matte`, `track` | yes | yes | no | no | post-write snapshot checked by _verify_mask_track |
| `motion_graphic` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `multicam.angle` | no_executor | `unique_id`, `angle_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `multicam.select_angle` | implemented_unqualified | `unique_id`, `speaker_id`, `assignment_basis`, `source_file`, `source_in_frame`, `source_out_frame`, `sync_evidence_id` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `music_selection` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.add` | implemented_unqualified | `row`, `record_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.entry_motion` | implemented_unqualified | `unique_id`, `frames` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.position` | implemented_unqualified | `unique_id`, `dx`, `dy` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.remove` | implemented_unqualified | `unique_id` | yes | yes | no | no | post-write snapshot checked by verify |
| `overlay.replace` | implemented_unqualified | `unique_id`, `file` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.resize` | implemented_unqualified | `unique_id`, `width`, `height` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.swap_pixels` | implemented_unqualified | `unique_id`, `file` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `picture.keyframe` | implemented_unqualified | `unique_id`, `param`, `keyframes` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `plan_change` | planning_only | `node`, `note` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `redraw_closer` | no_executor | `unique_id`, `start` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `reel.opening` | implemented_unqualified | `unique_id`, `brief`, `edits` | yes | yes | no | no | post-write snapshot checked by _verify_reel_opening |
| `retime` | implemented_unqualified | `unique_id`, `percent` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `retime.segments` | implemented_unqualified | `unique_id`, `segments` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `shot_selection` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `sound_effect` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `span_retime` | no_executor | `unique_id`, `percent` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `speech_selection` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `story_pacing` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `subject_effect` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.add` | implemented_qualified | `track_type`, `name` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `track.delete` | implemented_unqualified | `track_type`, `track_index` | yes | yes | no | no | post-write snapshot checked by verify |
| `track.rename` | implemented_unqualified | `track_type`, `track_index`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.reorder` | implemented_unqualified | `track_type`, `from_index`, `to_index`, `name` | yes | yes | no | no | post-write snapshot checked by _verify_track_reorder |
| `transform_override` | no_executor | `unique_id`, `key`, `value` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `transition` | implemented_unqualified | `carrier`, `transition` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `visual_effect` | planning_only | `plan` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `voice_isolation` | no_executor | `track_index` | yes | no | no | no | post-write snapshot checked by the operation-specific algebra verifier |
