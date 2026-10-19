# Backend limitations

Generated from `edit_algebra.OP_STATUS`.

A missing backend row means this registry does not advertise that backend for the operation. Qualification is per operation and backend.

## Cross-cutting Resolve invariants

Consult the owning implementation for these constraints:

- A Resolve project is addressed by its EXACT listed name, never a prefix. Owner: `library/tools/timeline_ingest.py`
- The unit of exclusion is the instance CURSOR; a write REFUSES outside the lease, a foreign move is DETECTED. Owner: `library/tools/resolve_lock.py`
- Timeline reads use recorded generations; writes use EditPatch. Owner: `library/tools/timeline_shadow.py, library/tools/edit_patch.py`
- Continuous edit detection watches the shadow on a schedule. Owner: `library/tools/shadow_watcher.py`
- The broker is a STANDALONE service any client starts on demand, never a worker's child. Owner: `library/tools/resolved/client.py`
- One-off scripts are broker jobs, never direct. Owner: `docs/RESOLVE_AXI_CUSTOM_SCRIPTS.md`
- Judge a Resolve call by what it RETURNS, never by `hasattr` Owner: `library/steps/step_6_01_render/probe_resolve_capabilities.py`
- A comp's MediaIn must COVER every frame its item PLAYS, or Resolve FAILS the render. Owner: `library/tools/comp_media_window.py`
- Pan/Tilt is ONE model, and a unit is not a pixel. Owner: `library/tools/resolve_transform.py`
- Do not wire FCPXML or DRP project-file surgery back in. Owner: `library/tools/transition_vocabulary.py`
- A DRAWN transition can only sit where a V1 clip ends. Owner: `library/tools/transition_carriers.py`
- Six things that must NEVER appear in a Fusion .comp. Owner: `library/tools/fusion/comp_builder.py`
- What a Fusion .comp must ALWAYS carry. Owner: `library/tools/fusion/comp_builder.py`
- One enumeration, `library/tools/fusion/played_window.py`; a comp is KEYED to it. Owner: `library/tools/composed_edit.py`
- The default transition values. Owner: `library/tools/fusion/effects.py`
- Rows are the SOP's. Owner: `docs/TIMELINE_SOP.md; library/tools/timeline_layout.py`
- The mix goes through OTIO, and it goes in at placement time. Owner: `library/tools/otio_mix.py`
- Both pages show the SAME PIXELS through DIFFERENT VIEWERS; measure a grade on an EXPORT, never on a viewer. Owner: `library/tools/resolve_surfaces.py`
- A capture that did not happen RAISES. Owner: `library/tools/marker_capture.py`
- Copy the project database before opening it; never open it in place. Owner: `library/tools/resolve_axi.py`
- FILED from measurements; nothing deleted. Smart bins are NOT scriptable. Owner: `library/tools/resolve_organization.py`
- REMOVING is a different act: `DeleteClips` on a TIMELINE's pool item DELETES THE TIMELINE. Owner: `library/tools/orphan_removal.py`
- Every build SWEEPS: empty bins and dead pool items go, superseded files to quarantine; nothing is unlinked. Owner: `library/tools/build_sweep.py`
- A staged timeline awaiting promotion is HELD. Owner: `library/tools/staging_holds.py`
- Markers are the ONLY write the master takes, placed by footage overlap. Owner: `library/tools/master_markers.py`
- Timeline speech is REBUILT from source, not rendered; the transcriber is a seam that FALLS BACK. Owner: `library/tools/timeline_transcript.py, library/tools/hybrid_transcription.py`
- What runs in parallel is a TABLE. Owner: `library/tools/concurrency_routing.py; library/tools/declaration_keys.py`

| operation | backend | compilable | qualified | production enabled | limitations | evidence |
|---|---|---:|---:|---:|---|---|
| `clip.delete` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; A local delete leaves a gap; `clip.ripple` closes it. | none |
| `clip.delete` | `resolve_axi` | yes | no | no | Only operations reached by the Resolve AXI command dispatch.; A local delete leaves a gap; `clip.ripple` closes it. | none |
| `clip.insert` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; Places a pool clip at a stated span; the inverse deletes it. | none |
| `clip.insert` | `reel_append_placement` | yes | no | no | Recorded full-build placement; not an existing-timeline patch.; Places a pool clip at a stated span; the inverse deletes it. | none |
| `clip.insert` | `reel_otio_placement` | yes | no | no | Recorded full-build placement; OTIO representability refusals apply.; Places a pool clip at a stated span; the inverse deletes it. | none |
| `clip.link` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; Link clips so later edits move them together. The shadow read-back does not capture link state; the executor verifies by re-read. | none |
| `clip.move` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints.; same-row overlay items only; cross-row moves require remove plus add; An item to a different record position on the same row. | none |
| `clip.replace` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; A clip's source for another pool clip at the same span, carrying the remapped Fusion keys (`fusion_keys`) and the grade assertion (`expected_grade`) with the binding so a commit can never land new media under stale keys (the Unit 5 swap-back). | none |
| `clip.set_enabled` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `clip.set_enabled` | `feedback_patch_apply` | yes | no | no | Local restorable feedback operations only; no pool fetch or ripple. | none |
| `clip.set_enabled` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints. | none |
| `clip.set_property` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; One transform property; the inverse restores the old value. | none |
| `clip.set_property` | `feedback_patch_apply` | yes | no | no | Local restorable feedback operations only; no pool fetch or ripple.; One transform property; the inverse restores the old value. | none |
| `clip.set_property` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints.; One transform property; the inverse restores the old value. | `tests/qualification/test_golden_conversation_live.py` |
| `clip.set_property` | `composed_edit` | yes | no | no | Composition-level staging and validation rules apply.; One transform property; the inverse restores the old value. | `tests/qualification/test_golden_conversation_live.py` |
| `clip.smart_reframe` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; Resolve's SmartReframe; judged on its framing properties. | none |
| `clip.smart_reframe` | `resolve_axi` | yes | no | no | Only operations reached by the Resolve AXI command dispatch.; Resolve's SmartReframe; judged on its framing properties. | none |
| `clip.unlink` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `clip_marker.add` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `clip_marker.add` | `feedback_patch_apply` | yes | no | no | Local restorable feedback operations only; no pool fetch or ripple. | none |
| `clip_marker.delete` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `clip_marker.delete` | `feedback_patch_apply` | yes | no | no | Local restorable feedback operations only; no pool fetch or ripple. | none |
| `grade.node_add` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; GUI-only write, API-read judgement: the Color-page menu recipe presses Add Serial Node server-side, and `GetNumNodes` must grow by exactly one. The Resolve 21.1 Graph API has no AddNode (measured; `edit_ledger.py` refuses a LUT on a node outside 1..count for the same reason). | none |
| `grade.node_remove` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; GUI-only write, API-read judgement: the Color-page menu recipe steps to the target node and presses Delete Current Node; `GetNumNodes` must shrink by exactly one AND the removed index's cache-mode label must be gone (labels restored after). The Resolve 21.1 Graph API has no RemoveNode - SetNodeEnabled(False) bypasses a node but does not remove it from the graph. | none |
| `grade.node_set` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; SetCDL the four terms onto one named node; judged by GetCDL re-read. The only node-grade op the Resolve 21.1 API can execute - SetCDL with NodeIndex is measured, AddNode is not in the API. | none |
| `hold.repair_window` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints.; A freeze-hold tail's comp media windows rebound in place; spans, pixels, transforms and grades unchanged. | none |
| `marker.add` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; One marker per frame: a second add there replaces the first. | `tests/qualification/test_feedback_patch_flow_against_resolve.py` |
| `marker.add` | `feedback_patch_apply` | yes | no | no | Local restorable feedback operations only; no pool fetch or ripple.; One marker per frame: a second add there replaces the first. | `tests/qualification/test_feedback_patch_apply_against_resolve.py` |
| `marker.add` | `resolve_axi` | yes | no | no | Only operations reached by the Resolve AXI command dispatch.; One marker per frame: a second add there replaces the first. | none |
| `marker.delete` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; The inverse carries the deleted marker's color/name/note. | `tests/qualification/test_feedback_patch_flow_against_resolve.py` |
| `marker.delete` | `feedback_patch_apply` | yes | no | no | Local restorable feedback operations only; no pool fetch or ripple.; The inverse carries the deleted marker's color/name/note. | `tests/qualification/test_feedback_patch_apply_against_resolve.py` |
| `mask.create` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; Place a full-frame matte comp; the matte read verifies. The `provider` names the engine: fusion_matte places the comp; resolve_magic_mask_api starts the native mask from the op's seed (True plus a gained node claims it); resolve_magic_mask_ui names the recipe verb that clicks the seed and never runs in a patch. | none |
| `mask.delete` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `mask.edit` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `mask.track` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; Key the matte's track; the keyframes read back verifies. The `provider` names the engine: fusion_matte keys the comp; resolve_magic_mask_api re-tracks through the mask engine (empty track - keyframes are the fusion vocabulary); resolve_magic_mask_ui names the recipe verb that presses the tracker and never runs in a patch. | none |
| `overlay.add` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints.; native PNG stills use still_placement exact-duration routing; staging must re-read the requested span; A new overlay item on an overlay row. | none |
| `overlay.entry_motion` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints. | none |
| `overlay.remove` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints. | none |
| `overlay.resize` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints. | none |
| `overlay.swap_pixels` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints.; An overlay's pixels for a re-rendered file. | none |
| `picture.keyframe` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; A keyframed picture parameter, written through the transform write log. | none |
| `retime` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints.; Constant-percent speed; a ramp reaches the timeline as stepped segments (`retime.segments`), never as a curve. | none |
| `retime.segments` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; Stepped constant-speed segments; the measured form of a speed ramp. | none |
| `track.add` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; A new named row; the touch-up's `add_row` views here. | none |
| `track.add` | `reel_touchup` | yes | no | no | Only the named touch-up vocabulary and its declared constraints.; A new named row; the touch-up's `add_row` views here. | none |
| `track.delete` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `track.rename` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable. | none |
| `track.reorder` | `edit_patch` | yes | no | no | Only algebra operations with a native apply are schedulable.; Move a row to a new index; the track listing verifies. A row carrying clips is refused: Resolve has no move-track call, and DeleteTrack destroys them. | none |
