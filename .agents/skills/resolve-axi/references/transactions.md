# Transaction capability map

Generated from `edit_algebra.OP_STATUS`.

Before a write, use AXI's available typed operation path and its dry-run/guarded mode. Backend support is not permission to substitute another editorial operation.

## Compilable operations

| operation | required parameters | defined | compilable | qualified | production enabled | verification contract |
|---|---|---:|---:|---:|---:|---|
| `clip.delete` | `unique_id` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.insert` | `unique_id`, `track_type`, `track_index`, `record_in`, `record_out`, `media_pool_item` | yes | yes | no | no | post-write snapshot checked by verify |
| `clip.link` | `unique_id`, `with_unique_ids` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.move` | `unique_id`, `to_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.replace` | `unique_id`, `source_file` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.set_enabled` | `unique_id`, `enabled` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.set_property` | `unique_id`, `key`, `value` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.smart_reframe` | `unique_id`, `keys`, `before` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip.unlink` | `unique_id` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_marker.add` | `unique_id`, `frame`, `color`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `clip_marker.delete` | `unique_id`, `frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_add` | `unique_id`, `node_index` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_remove` | `unique_id`, `node_index` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `grade.node_set` | `unique_id`, `node_index`, `params` | yes | yes | no | no | post-write snapshot checked by _verify_grade_node_set |
| `marker.add` | `frame`, `color`, `name` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `marker.delete` | `frame` | yes | yes | yes | yes | post-write snapshot checked by the operation-specific algebra verifier |
| `mask.create` | `unique_id`, `matte` | yes | yes | no | no | post-write snapshot checked by _verify_mask_create |
| `mask.delete` | `unique_id`, `matte` | yes | yes | no | no | post-write snapshot checked by _verify_mask_delete |
| `mask.edit` | `unique_id`, `matte`, `geometry` | yes | yes | no | no | post-write snapshot checked by _verify_mask_edit |
| `mask.track` | `unique_id`, `matte`, `track` | yes | yes | no | no | post-write snapshot checked by _verify_mask_track |
| `overlay.add` | `row`, `record_frame` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.entry_motion` | `unique_id`, `frames` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.remove` | `unique_id` | yes | yes | no | no | post-write snapshot checked by verify |
| `overlay.resize` | `unique_id`, `width`, `height` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `overlay.swap_pixels` | `unique_id`, `file` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `picture.keyframe` | `unique_id`, `param`, `keyframes` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `retime` | `unique_id`, `percent` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `retime.segments` | `unique_id`, `segments` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.add` | `track_type`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.delete` | `track_type`, `track_index` | yes | yes | no | no | post-write snapshot checked by verify |
| `track.rename` | `track_type`, `track_index`, `name` | yes | yes | no | no | post-write snapshot checked by the operation-specific algebra verifier |
| `track.reorder` | `track_type`, `from_index`, `to_index`, `name` | yes | yes | no | no | post-write snapshot checked by _verify_track_reorder |
