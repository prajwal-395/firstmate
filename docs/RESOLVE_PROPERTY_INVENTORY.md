# Resolve property inventory

Every Resolve property an edit can modify directly or indirectly, and what a
read can prove about it. This is the inventory PRES-001 asks for: an
inventory of PROPERTIES, not of operations - `library/tools/edit_algebra.py`
already enumerates the operations and `docs/RESOLVE_WRITE_INVENTORY.md`
already renders them.

The enforcement half lives in the code: `library/tools/resolve_property_inventory.py`
holds the enumeration, `library/tools/reel_read.py` reads it, and
`tests/unit/resolve/test_resolve_property_inventory.py` fails when the
enumeration and the operations disagree.

## What the three support classes mean

- `readable` - a read returns the property and the read can be trusted.
- `partially_readable` - a read returns SOME of the property and claims no
  more.
- `unreadable` - Resolve's scripting API exposes no getter, so the property
  cannot be observed at all.

## What a missing value means

`measure()` never collapses "Resolve did not say" into "the value is zero".
A reading is one of:

- `measured` - a value somebody set.
- `default` - a value equal to the property's CONFIRMED Resolve default.
- `unavailable` - the read could not establish the property, with the reason.

A default is a value Resolve itself chose; a measurement is a value
somebody set; an unavailable reading is nothing at all. A preservation check
that treats the third as the first restores a default over an approved value
it never saw.

## Where the state lives

`reel_read.clip_detail` is the ONE reader of a clip, and it now records the
groups it did not record before: the native fades (`GetFades`), the linked
PARTNERS' own state rather than only the group's member ids, and the color
node count. `resolve_property_inventory.clip_surface` projects any read into
the per-property surface; `timeline_surface` does the same for a whole
timeline and reports what could not be read at all.

The surface is DERIVED from a read, never a second read. It exists so that
"Resolve did not say" travels with the state instead of being flattened into
it by the first consumer that wants a number.

<!-- BEGIN GENERATED RESOLVE PROPERTY INVENTORY -->
## Resolve property inventory (generated)

Generated from `library/tools/resolve_property_inventory.py` by `python3 -m library.tools.resolve_property_inventory --write`. `readable` means a read returns the property and can be trusted; `partially_readable` means a read returns SOME of it and claims no more; `unreadable` means Resolve's scripting API exposes no getter, so the property cannot be observed at all.

An unreadable property is a fact about Resolve. A preservation claim that covers one is a claim about a value nobody could read.

| property | group | read by | support | confirmed default | modified by |
|---|---|---|---|---|---|
| `clip.native_id` | identity | `TimelineItem.GetUniqueId()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten` |
| `clip.name` | identity | `TimelineItem.GetName()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten` |
| `clip.media_pool_item_id` | identity | `MediaPoolItem.GetMediaId()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten` |
| `clip.source_file` | identity | `MediaPoolItem.GetClipProperty('File Path')` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten` |
| `clip.source_frames` | identity | `MediaPoolItem.GetClipProperty('Frames')` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten` |
| `clip.record_in` | placement | `TimelineItem.GetStart()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |
| `clip.record_out` | placement | `TimelineItem.GetEnd()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |
| `clip.duration` | placement | `TimelineItem.GetDuration()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |
| `clip.source_in_frame` | source | `TimelineItem.GetSourceStartFrame()` | readable | none established | `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.replace`, `clip.insert`, `retime`, `retime.segments`, `span_retime` |
| `clip.source_out_frame` | source | `TimelineItem.GetSourceEndFrame()` | readable | none established | `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.replace`, `clip.insert`, `retime`, `retime.segments`, `span_retime` |
| `clip.left_offset` | source | `TimelineItem.GetLeftOffset()` | readable | none established | `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.replace`, `clip.insert`, `retime`, `retime.segments`, `span_retime` |
| `clip.right_offset` | source | `TimelineItem.GetRightOffset()` | readable | none established | `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.replace`, `clip.insert`, `retime`, `retime.segments`, `span_retime` |
| `clip.track_type` | track | `TimelineItem.GetTrackTypeAndIndex()` | readable | none established | `track.add`, `track.delete`, `track.rename`, `track.reorder`, `clip.move`, `clip.insert`, `clip.remove`, `clip.delete` |
| `clip.track_index` | track | `TimelineItem.GetTrackTypeAndIndex()` | readable | none established | `track.add`, `track.delete`, `track.rename`, `track.reorder`, `clip.move`, `clip.insert`, `clip.remove`, `clip.delete` |
| `clip.track_name` | track | `Timeline.GetTrackName()` | readable | none established | `track.add`, `track.delete`, `track.rename`, `track.reorder`, `clip.move`, `clip.insert`, `clip.remove`, `clip.delete` |
| `transform.Pan` | transform | `TimelineItem.GetProperty()` | partially_readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.Tilt` | transform | `TimelineItem.GetProperty()` | partially_readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.ZoomX` | transform | `TimelineItem.GetProperty()` | readable | `1.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.ZoomY` | transform | `TimelineItem.GetProperty()` | readable | `1.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.ZoomGang` | transform | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.RotationAngle` | transform | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.AnchorPointX` | transform | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.AnchorPointY` | transform | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.Pitch` | transform | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.Yaw` | transform | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.FlipX` | transform | `TimelineItem.GetProperty()` | readable | `False` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.FlipY` | transform | `TimelineItem.GetProperty()` | readable | `False` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.Scaling` | transform | `TimelineItem.GetProperty()` | readable | none established | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `transform.CropRetain` | transform | `TimelineItem.GetProperty()` | readable | none established | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `picture.keyframe`, `clip.insert`, `clip.replace`, `angle_plan`, `multicam.angle`, `multicam.select_angle` |
| `crop.CropLeft` | crop | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `clip.insert`, `clip.replace` |
| `crop.CropRight` | crop | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `clip.insert`, `clip.replace` |
| `crop.CropTop` | crop | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `clip.insert`, `clip.replace` |
| `crop.CropBottom` | crop | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `clip.insert`, `clip.replace` |
| `crop.CropSoftness` | crop | `TimelineItem.GetProperty()` | readable | `0.0` | `clip.set_property`, `clip.smart_reframe`, `transform_override`, `clip.insert`, `clip.replace` |
| `composite.Opacity` | composite | `TimelineItem.GetProperty()` | readable | `100.0` | `clip.set_property`, `clip.insert`, `clip.replace` |
| `composite.CompositeMode` | composite | `TimelineItem.GetProperty()` | readable | `'Normal'` | `clip.set_property`, `clip.insert`, `clip.replace` |
| `retime.speed_percent` | retime | `TimelineItem.GetSpeed()` | readable | `100.0` | `retime`, `retime.segments`, `span_retime`, `clip.set_property`, `clip.insert`, `clip.replace` |
| `retime.RetimeProcess` | retime | `TimelineItem.GetProperty()` | readable | none established | `retime`, `retime.segments`, `span_retime`, `clip.set_property`, `clip.insert`, `clip.replace` |
| `retime.MotionEstimation` | retime | `TimelineItem.GetProperty()` | readable | none established | `retime`, `retime.segments`, `span_retime`, `clip.set_property`, `clip.insert`, `clip.replace` |
| `enablement.enabled` | enablement | `TimelineItem.GetClipEnabled()` | readable | `True` | `clip.set_enabled`, `clip.insert`, `clip.replace` |
| `markers.clip` | markers | `TimelineItem.GetMarkers()` | readable | none established | `marker.add`, `marker.delete`, `clip_marker.add`, `clip_marker.delete`, `clip.insert`, `clip.replace`, `clip.delete`, `clip.remove` |
| `markers.timeline` | markers | `Timeline.GetMarkers()` | readable | none established | `marker.add`, `marker.delete`, `clip_marker.add`, `clip_marker.delete`, `clip.insert`, `clip.replace`, `clip.delete`, `clip.remove` |
| `markers.media_pool` | markers | `MediaPoolItem.GetMarkers()` | readable | none established | `marker.add`, `marker.delete`, `clip_marker.add`, `clip_marker.delete`, `clip.insert`, `clip.replace`, `clip.delete`, `clip.remove` |
| `linked.item_ids` | linked | `TimelineItem.GetLinkedItems()` | readable | none established | `clip.link`, `clip.unlink`, `clip.insert`, `clip.replace`, `clip.delete` |
| `linked.audio_state` | linked | `TimelineItem.GetLinkedItems()` | partially_readable | none established | `clip.link`, `clip.unlink`, `clip.insert`, `clip.replace`, `clip.delete` |
| `fades.item_in` | fades | `TimelineItem.GetFades()` | partially_readable | `0` | `audio.fade`, `audio_mix`, `clip.insert`, `clip.replace` |
| `fades.item_out` | fades | `TimelineItem.GetFades()` | partially_readable | `0` | `audio.fade`, `audio_mix`, `clip.insert`, `clip.replace` |
| `fades.audio_in` | fades | `TimelineItem.GetFades()` | partially_readable | `0` | `audio.fade`, `audio_mix`, `clip.insert`, `clip.replace` |
| `fades.audio_out` | fades | `TimelineItem.GetFades()` | partially_readable | `0` | `audio.fade`, `audio_mix`, `clip.insert`, `clip.replace` |
| `keyframes.Pan` | keyframes | `TimelineItem.GetKeyframes()` | partially_readable | none established | `picture.keyframe`, `clip.smart_reframe`, `transform_override` |
| `keyframes.Tilt` | keyframes | `TimelineItem.GetKeyframes()` | partially_readable | none established | `picture.keyframe`, `clip.smart_reframe`, `transform_override` |
| `keyframes.ZoomX` | keyframes | `TimelineItem.GetKeyframes()` | partially_readable | none established | `picture.keyframe`, `clip.smart_reframe`, `transform_override` |
| `keyframes.ZoomY` | keyframes | `TimelineItem.GetKeyframes()` | partially_readable | none established | `picture.keyframe`, `clip.smart_reframe`, `transform_override` |
| `transitions.in` | transitions | `TimelineItem.AddTransition()` | unreadable | none established | `transition`, `clip.insert`, `clip.replace` |
| `transitions.out` | transitions | `TimelineItem.AddTransition()` | unreadable | none established | `transition`, `clip.insert`, `clip.replace` |
| `effects.list` | effects | `no getter` | unreadable | none established | `look`, `visual_effect`, `clip.set_property` |
| `effects.lut` | effects | `TimelineItem.GetLUT(node)` | partially_readable | none established | `look`, `visual_effect`, `clip.set_property` |
| `color.cdl` | color | `TimelineItem.GetCDL()` | readable | none established | `clip_lut`, `grade`, `grade.node_add`, `grade.node_remove`, `grade.node_reorder`, `grade.node_set`, `color.window.create`, `color.window.delete`, `color.window.modify`, `color.window.track`, `color.qualifier.sample`, `color.qualifier.adjust`, `look`, `clip.insert`, `clip.replace` |
| `color.group` | color | `TimelineItem.GetColorGroup()` | readable | none established | `clip_lut`, `grade`, `grade.node_add`, `grade.node_remove`, `grade.node_reorder`, `grade.node_set`, `color.window.create`, `color.window.delete`, `color.window.modify`, `color.window.track`, `color.qualifier.sample`, `color.qualifier.adjust`, `look`, `clip.insert`, `clip.replace` |
| `color.node_count` | color | `TimelineItem.GetNodeGraph().GetNumNodes()` | readable | none established | `clip_lut`, `grade`, `grade.node_add`, `grade.node_remove`, `grade.node_reorder`, `grade.node_set`, `color.window.create`, `color.window.delete`, `color.window.modify`, `color.window.track`, `color.qualifier.sample`, `color.qualifier.adjust`, `look`, `clip.insert`, `clip.replace` |
| `color.node_luts` | color | `TimelineItem.GetNodeGraph().GetLUT(node)` | partially_readable | none established | `clip_lut`, `grade`, `grade.node_add`, `grade.node_remove`, `grade.node_reorder`, `grade.node_set`, `color.window.create`, `color.window.delete`, `color.window.modify`, `color.window.track`, `color.qualifier.sample`, `color.qualifier.adjust`, `look`, `clip.insert`, `clip.replace` |
| `fusion.comp_count` | fusion | `TimelineItem.GetFusionCompCount()` | readable | none established | `mask.create`, `mask.delete`, `mask.edit`, `mask.track`, `compound.create`, `compound.flatten`, `hold.repair_window`, `visual_effect`, `subject_effect`, `motion_graphic`, `brand_asset`, `end_card`, `overlay.add`, `overlay.remove`, `overlay.replace`, `overlay.resize`, `overlay.position`, `overlay.entry_motion`, `overlay.swap_pixels`, `clip.insert`, `clip.replace` |
| `fusion.comp_names` | fusion | `TimelineItem.GetFusionCompNameList()` | readable | none established | `mask.create`, `mask.delete`, `mask.edit`, `mask.track`, `compound.create`, `compound.flatten`, `hold.repair_window`, `visual_effect`, `subject_effect`, `motion_graphic`, `brand_asset`, `end_card`, `overlay.add`, `overlay.remove`, `overlay.replace`, `overlay.resize`, `overlay.position`, `overlay.entry_motion`, `overlay.swap_pixels`, `clip.insert`, `clip.replace` |
| `fusion.media_windows` | fusion | `FusionComp.GetInput('MediaIn')` | partially_readable | none established | `mask.create`, `mask.delete`, `mask.edit`, `mask.track`, `compound.create`, `compound.flatten`, `hold.repair_window`, `visual_effect`, `subject_effect`, `motion_graphic`, `brand_asset`, `end_card`, `overlay.add`, `overlay.remove`, `overlay.replace`, `overlay.resize`, `overlay.position`, `overlay.entry_motion`, `overlay.swap_pixels`, `clip.insert`, `clip.replace` |
| `fusion.mattes` | fusion | `FusionComp.GetToolList()` | partially_readable | none established | `mask.create`, `mask.delete`, `mask.edit`, `mask.track`, `compound.create`, `compound.flatten`, `hold.repair_window`, `visual_effect`, `subject_effect`, `motion_graphic`, `brand_asset`, `end_card`, `overlay.add`, `overlay.remove`, `overlay.replace`, `overlay.resize`, `overlay.position`, `overlay.entry_motion`, `overlay.swap_pixels`, `clip.insert`, `clip.replace` |
| `audio.volume` | audio | `TimelineItem.GetProperty()` | readable | `0.0` | `audio.gain`, `audio.pan`, `audio.automation`, `audio.fade`, `voice_isolation`, `audio_mix`, `music_selection`, `sound_effect`, `clip.insert`, `clip.replace` |
| `audio.pan` | audio | `TimelineItem.GetProperty()` | readable | `0.0` | `audio.gain`, `audio.pan`, `audio.automation`, `audio.fade`, `voice_isolation`, `audio_mix`, `music_selection`, `sound_effect`, `clip.insert`, `clip.replace` |
| `audio.effects` | audio | `no getter` | unreadable | none established | `audio.gain`, `audio.pan`, `audio.automation`, `audio.fade`, `voice_isolation`, `audio_mix`, `music_selection`, `sound_effect`, `clip.insert`, `clip.replace` |
| `audio.automation` | audio | `no getter` | unreadable | none established | `audio.gain`, `audio.pan`, `audio.automation`, `audio.fade`, `voice_isolation`, `audio_mix`, `music_selection`, `sound_effect`, `clip.insert`, `clip.replace` |
| `timeline.start_frame` | timeline | `Timeline.GetStartFrame()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |
| `timeline.end_frame` | timeline | `Timeline.GetEndFrame()` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |
| `timeline.frame_rate` | timeline | `Timeline.GetSetting('timelineFrameRate')` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |
| `timeline.width` | timeline | `Timeline.GetSetting('timelineResolutionWidth')` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |
| `timeline.height` | timeline | `Timeline.GetSetting('timelineResolutionHeight')` | readable | none established | `clip.insert`, `clip.replace`, `clip.trim`, `clip.split`, `clip.join`, `clip.roll`, `clip.slip`, `clip.slide`, `clip.move`, `clip.ripple`, `clip.delete`, `clip.remove`, `compound.create`, `compound.flatten`, `track.add`, `track.delete`, `track.reorder`, `broll.insert`, `drop_fragment`, `reel.opening`, `plan_change`, `shot_selection`, `speech_selection`, `story_pacing`, `redraw_closer`, `caption_fix`, `caption.style` |

### The properties Resolve cannot report

- **`transitions.in`** - Resolve's scripting API can PLACE a transition and returns the transition item; it exposes no getter that reports the transition on a clip. A clip's transition state cannot be observed, so it cannot be proven preserved.
- **`transitions.out`** - 
- **`effects.list`** - Resolve's scripting API exposes no effect list on a timeline item. An item's ResolveFX and OFX effects are invisible to a read.
- **`audio.effects`** - An audio item's effects, EQ and dynamics are not exposed to a read.
- **`audio.automation`** - Fairlight automation curves are not exposed to a read.

### The properties a read only partly establishes

- **`transform.Pan`** - Scaled by the CURRENT timeline's width over this timeline's width. Refuse the read off a non-current handle (`reel_read.assert_timeline_current`).
- **`transform.Tilt`** - Scaled by the CURRENT timeline's height over this timeline's height.
- **`linked.audio_state`** - A linked audio item's own gain, fades and enablement. Readable item by item; there is no link-group getter that reports the group's routing.
- **`fades.item_in`** - The documented API is Resolve 21.1's `GetFades`. Where the method is absent the fade state is UNAVAILABLE, not zero. On an AUDIO item this is the audio fade: measured, `reel_touchup._audio_fades` reads it there.
- **`fades.item_out`** - 
- **`fades.audio_in`** - The audio fade of a VIDEO item, under its own key.
- **`fades.audio_out`** - 
- **`keyframes.Pan`** - Only Pan, Tilt, ZoomX and ZoomY have a keyframe getter.
- **`keyframes.Tilt`** - 
- **`keyframes.ZoomX`** - 
- **`keyframes.ZoomY`** - 
- **`effects.lut`** - The assigned LUT path for one node. The node's other parameters are not readable.
- **`color.node_luts`** - The assigned LUT paths. Node parameters, curves and qualifiers are not readable through this surface.
- **`fusion.media_windows`** - Where each comp's MediaIn reads from. A comp's tools and animation are not fully captured.
- **`fusion.mattes`** - Each matte comp's geometry and keyframes, read only for comps whose name marks them as mattes. A comp's other tools are not captured.
<!-- END GENERATED RESOLVE PROPERTY INVENTORY -->
