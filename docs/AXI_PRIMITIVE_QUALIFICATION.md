# AXI-010..023: the per-item qualification statement

The captain's punch list, section "1. Resolve AXI: Make Existing Editing
Capabilities Production-Usable", asks for fourteen editing primitives to
carry an explicit, generalizable qualification boundary.  This file
states, for each item, what is ALREADY qualified, what this lane adds,
and what cannot be qualified and why.

The scenarios live in `tests/live/axi_primitive_qualification.py`.  The
live lane (`vep-axc-live2`) runs them on the private scratch timeline
(`Ren Qualification` / `fm-scratch qualify`) and imports the evidence
through the existing registry tool:

```sh
REN_LIVE_RESOLVE=1 bin/vep tests/live/axi_primitive_qualification.py
python3 -m library.tools.axi_qualification \
    --import-result tests/live/reports/registry/<operation>__<backend>.json
```

The run writes one `ren-axi-qualification-result/v2` document per
(operation, backend) pair the passing scenarios earned.  A pair that
already has a registry row is NEVER overwritten: the new evidence is
written beside it and the report names both, so a promotion stays a
deliberate act on a file the operator chose.

The ONE exception is a committed report in the LEGACY schema.  The
importer refuses that schema outright - it carries no environment, no
semantic boundary and no coverage, so it can never qualify anything -
and a row promoted from one has an evidence document nobody can read
back.  The fresh evidence REPLACES it at the canonical path, and the
report names the replacement (`replaced_legacy_report`).

## The fixtures the lane needs

The live lane runs against `Ren Qualification`, on the timeline
`fm-scratch qualify`.  A qualification RESET empties that project, so
the setup-created fixtures - the qualification media in the media pool,
the `Q Spine` source and the EMPTY scratch timeline the lane seeds - go
with it, while the protected proof timelines (`Q Promotion Proof R5`
and the rest) have to be restored from `.drt` backups by hand.

`ren qualification ensure` is the non-destructive half.  It creates
ONLY what is missing and never deletes, renames, replaces or empties
anything that exists:

```sh
ren qualification ensure
```

It renders the synthetic clips that are absent on disk, imports the
qualification media the pool does not hold, restores the declared
`Q Spine` source onto an EMPTY `Q Spine`, creates the scratch
timeline EMPTY when it is absent, saves the project and reads the
fixtures back.  A populated timeline - the scratch timeline the lane
has already seeded, the proof fixtures - is left exactly as it is and
reported, so an absent fixture is named rather than assumed present.

WHICH `Ren Qualification` it works in is the one the live lane opens.
Resolve resolves a project NAME inside the CURRENT Project Manager
folder, and the lane opens the name from the folder it borrowed the
captain's project from - so the ensure opens the name from the same
folder.  Two projects can answer to one name (a stray rebuild filed
at the Project Manager root, the live one filed beside the captain's
project), and an ensure that opened the other one would read a pool the
lane never reads: the fixtures would read as PRESENT while the lane
still failed on the missing `qual_a.mp4`, and nothing would be
imported.  So the current folder wins, and a name that several folders
hold - none of them the current one - is REFUSED with the folders
named rather than guessed at.

Presence is the LIVE media pool of the project being ensured, read
fresh and read back after the import.  A rendered clip the pool does
not hold is missing, however present it is on disk.

## What a row means

A qualification row is keyed on ONE operation and ONE backend - the
executor path that ran.  A row is only written for a pair that HAS an
executor, because promoting a pair without one would route production
work to a path that cannot run it.  Two operations therefore carry two
rows for two reasons: the same operation reached through two different
production entries, or the same operation earned by two different
scenarios (the merged checks live in one row).

Four scenarios have no row to write, and the harness says so by name in
the report's `promotion_gaps` rather than dropping them:

| Scenario | Why there is no row |
| --- | --- |
| `insert_to_absent_track_refused` | `clip.insert` has no executor on the AXI verb path (`edit place` is a command, not an algebra executor). The pair that carries an insertion is `clip.insert::edit_patch`, exercised by `insert_at_frame_on_track`. This scenario proves the verb's own refusal, and a refusal is not a qualification |
| `move_keeps_unrelated_state` | `clip.move` has no executor on the AXI verb path: the move re-places the clip, so the item's identity changes and it cannot be one patch operation. The pair that can carry it is `clip.move::reel_touchup` |
| `transition_at_cut` | `transition` has no executor on the AXI verb path. The pair that can carry it is `transition::reel_touchup` |
| `audio_automation_refused` | `audio.automation` has no executor at all: only the `volume` parameter has a measured spelling, and the OTIO mix path writes it at build time |

## The items

| Item | What it asks | Operations | State before this lane | This lane adds | What cannot be qualified, and why |
| --- | --- | --- | --- | --- | --- |
| AXI-010 | Insertion at exact frame positions and designated tracks | `clip.insert` | implemented, unqualified on every backend | `insert_at_frame_on_track` (the EditPatch placement, `clip.insert::edit_patch`), `insert_to_absent_track_refused` (the AXI verb refuses a row the timeline does not have) | none |
| AXI-011 | Removal with explicit ripple and non-ripple semantics | `clip.remove`, `clip.delete` | `clip.remove::resolve_axi` and `clip.delete::edit_patch` are ALREADY production-enabled | `remove_ripple_closes_gap` (the gap closes), `remove_leaves_gap` (the gap stays) - the two semantics side by side, which the existing evidence did not show; and, on a REALISTIC timeline (the captain's AXI-005), `delete_linked_av_moderate`, `delete_retimed_moderate`, `delete_still_card` and `delete_fusion_bearing` | none |
| AXI-012 | Trimming, in/out preservation and source-range validation | `clip.trim` | implemented on `resolve_axi`, unqualified | `trim_narrows_source_range` (the in/out points narrow, the duration and the shifted record start are read back), `trim_past_handles_refused` (a trim past the handles is refused and the timeline does not move) | none |
| AXI-013 | Moving without loss of unrelated timeline state | `clip.move` | implemented on `reel_touchup` only, unqualified | `move_keeps_unrelated_state` (the production verb `edit move`, with the other row and the other clip read back unchanged) | the QUALIFICATION ROW cannot be written for the AXI verb: `clip.move` has no executor on that path, because the move re-places the clip and the item's identity changes, so it cannot be one patch operation. The pair that can carry it is `clip.move::reel_touchup` (the built-reel touch-up) |
| AXI-014 | Source replacement preserving authorized clip-level adjustments | `clip.replace` | implemented on `edit_patch`, unqualified | `replace_source_keeps_transform` (the source is re-pointed and the Pan the request had already authorized is read back) | none for the operation. The LIMITATION is recorded: the write re-points the MEDIA POOL ITEM, so every clip drawing from that item changes with it |
| AXI-015 | Exact clip property changes: position, scale, crop, opacity, rotation | `clip.set_property` | `clip.set_property::edit_patch` is production-enabled, with evidence for ONE property (Pan) | `set_property_position`, `set_property_scale`, `set_property_crop`, `set_property_opacity`, `set_property_rotation` - the five families, each read back through `GetProperty()` | none. The measured property set is the transform and its modes; a key outside it is refused, never rounded |
| AXI-016 | Adding and removing video/audio tracks | `track.add`, `track.delete` | `track.add::edit_patch` is production-enabled; `track.delete` was implemented with no guard | `add_video_and_audio_tracks`, `remove_empty_track`, `remove_occupied_track_refused` | none. This lane ADDS the guard `track.delete` needed to be qualifyable at all: Resolve's `DeleteTrack` destroys every clip on the row, so a row that still carries clips is now refused before the write |
| AXI-017 | Track-scoped ripple operations | `clip.remove` | ALREADY production-enabled (`clip.remove::resolve_axi`), with the evidence naming the track-scoped ripple | `ripple_is_track_scoped` - the other track read back UNCHANGED, which the existing evidence did not show; and `ripple_remove_multi_track` and `ripple_remove_light`, which MEASURE the ripple's scope on a multi-track and on a light timeline | none |
| AXI-018 | B-roll insertion and replacement over dialogue | `broll.insert`, `clip.replace` | `broll.insert::resolve_axi` is ALREADY production-enabled | `broll_insert_keeps_dialogue` (the cover and the dialogue's linked audio read back), `broll_replace_keeps_dialogue` (the cover's source is re-pointed and the dialogue is untouched) | none |
| AXI-019 | Multicam angle replacement on synchronized source ranges | `multicam.select_angle` | implemented on `resolve_axi`, unqualified; the safety class still refuses it under VC-11 | `multicam_angle_switch` (the production verb `edit switch-camera`, with the assigned camera's source and the record span read back) | the router still refuses the operation under `SAFETY_UNREPRESENTABLE`, so the QUALIFICATION ROW cannot promote a route the router will not take. The verb itself is exercised and its read-back recorded |
| AXI-020 | Caption text, timing and style as SEPARATE semantic operations | `caption.style`, `caption_fix` | `caption.style::resolve_axi` is ALREADY production-enabled, with the text and the timing preserved | `caption_text_refused` (a caption TEXT change is refused by the router) | TEXT: `caption_fix` refuses before mutation, because a rendered caption card's text does not read back off the timeline inventory, and there is no independent read-back to judge it by. TIMING: it is not an AXI operation at all - it is the build's declaration path (`library/tools/caption_timing.py`). STYLE is qualified |
| AXI-021 | Lower-third and overlay insertion, replacement, positioning and removal | `clip.insert`, `clip.replace`, `clip.set_property`, `clip.delete` | the four clip primitives behind the overlay work are implemented; `overlay.add`/`overlay.replace`/`overlay.position`/`overlay.remove` are the built-reel touch-up wrappers and are unqualified | `overlay_insert`, `overlay_replace`, `overlay_position`, `overlay_remove` - the four operations, each on a cover row | the `overlay.*` wrappers are only reachable through a BUILT reel (the touch-up stages a duplicate and promotes by rename), so they are qualified by the live lane's built-reel rows, not on the scratch timeline. The primitives an agent actually needs are the clip ones, and those are qualified here |
| AXI-022 | Audio gain, mute, fades and supported automation | `clip.set_enabled`, `audio.gain`, `audio.fade`, `audio.automation` | `audio.gain` was IMPLEMENTED on `reel_touchup` and REFUSED there (no independent rendered-audio evidence); `audio.fade` is implemented on `reel_touchup`; `audio.automation` has no executor | `audio_mute` (the clip's contribution is switched off and read back through `GetClipEnabled()`), `audio_automation_refused`, and - NEW - the GAIN now executes on `resolve_axi` through `resolve-axi edit audio-gain`, which renders the timeline before and after the write and measures the decoded samples | GAIN: the route is NATIVE - Resolve 21.1 writes the level on the item itself (`SetProperty("AudioVolume", -6)`), and the spelling that answers False is `Volume`, not `AudioVolume`. MEASURED at REQUEST TIME on 21.1.0.14 (2026-10-21, `tests/live/reports/axi012b-audio-gain.json`, through the serving broker on a Ren-owned scratch timeline): the write reads back -6.0 and the RENDER measures EXACTLY -6.00 dB on the clip's window (-22.11 -> -28.11 dBFS), the other clip holds at -21.09 dBFS in both renders, the linked A/V group is the same before and after, and the level was restored afterwards. A write whose rendered audio does not verify is ROLLED BACK to the previous level. The OTIO build remains the route for a KEYFRAMED curve (a rebuild of the timeline), not for a single level. FADES: `TimelineItem.SetFades` is a native IN-PLACE write, and the only production entry that reaches it is the built-reel touch-up. AUTOMATION: no executor, and only the `volume` parameter has a measured spelling. PAN: the native route is now MEASURED too (`SetProperty("AudioPan", -100)` moves the render's left channel 12.0 dB up, `0` leaves the two channels identical, and the scale is -100..100), but no executor writes a pan, so `audio.pan` refuses BY NAME and names the measured route. The routes, the exact spellings, the read-backs, the audible verification and the refusals live in ONE enumeration, `library/tools/audio_operations.py`, and the operation map publishes that row per operation |
| AXI-023 | Retiming, transitions, Fusion compositions and graded clips | `retime.segments`, `transition`, `mask.create`, `grade.node_set` | `retime.segments` (EditPatch), `mask.create` and `grade.node_set` (EditPatch) are implemented and unqualified; `transition` is implemented on `reel_touchup` | `retime_speed_change` (the speed reads back), `fusion_matte_placed` (the matte comp reads back by name), `graded_clip_node_set` (the CDL reads back on the node) | the QUALIFICATION ROW for `transition` cannot be written for the AXI verb, because `transition` has no executor on that path - the pair that can carry it is `transition::reel_touchup`. `grade` and `clip_lut` have no executor at all: the Graph API has no AddNode and no LUT call, so only an EXISTING node can be graded and a LUT is not a timeline write |

## The delete and the ripple on a realistic timeline (the captain's AXI-005)

The scenarios above earned the delete and the ripple on a fixture with
one or two clips on ONE row: light density, one populated row, no
linked audio, no effects.  A real edit is none of those things, and
the AXI-11 suite's own fixture - a linked A/V A-roll, a cover on a
second row, a caption card on a third - refused four of its ten
ordinary editorial requests in the delete preflight, naming the FIXTURE
rather than the operation:

```
clip.delete: runtime state does not satisfy recorded qualification
predicates: timeline_density is 'moderate', not 'light';
linked_audio_configuration is 'linked_audio_present', not
'no_linked_audio'; clip_composition is 'linked_video_clip', not
'single_video_clip'; track_occupancy is 'all_populated', not 'mixed'

clip.remove: runtime state does not satisfy recorded qualification
predicates: timeline_density is 'moderate', not 'light';
existing_effects is 'retiming', not 'none'
```

The scenarios below measure the states a real edit happens in, and
they read the ADJACENT state back as carefully as the target: an edit
that moves the picture but loses the source range, the link, the effect
or the sync is a different edit, and the evidence has to say which of
them it saw.

| Scenario | What it measures | The state it was measured in |
| --- | --- | --- |
| `delete_linked_av_moderate` | a LINKED A/V target on a dense, multi-track timeline; the audio twin is read back unchanged, so the row is still in sync | moderate density, every row populated, `linked_video_clip`, linked audio present, no effects |
| `delete_retimed_moderate` | a RETIMED target on the same shape | moderate density, every row populated, `single_video_clip`, a retime |
| `delete_still_card` | a CAPTION CARD: a still held over forty-eight frames of its own span | light density, mixed occupancy, `still_clip`, the offset a still always carries |
| `delete_fusion_bearing` | a target that carries a FUSION COMPOSITION; the composition goes with the clip | light density, a retime and a Fusion composition |
| `ripple_remove_multi_track` | a ripple on the multi-track timeline, and the SCOPE of the ripple measured | moderate density, every row populated, a linked A/V target and a linked successor |
| `ripple_remove_light` | the same ripple on a light timeline | light density, mixed occupancy |

### What a ripple actually affects

The punch list asks for the ripple's scope to be MEASURED, not
assumed, so the scenario reads every row before and after and names
three things: the rows that LOST a clip, the rows where a clip MOVED,
and the rows that did not move at all.  Measured on the multi-track
fixture:

* the target and its LINKED AUDIO TWIN leave the timeline - the
  ripple removes the target's whole link group;
* the successor on the ripple's own track closes the gap, and its
  linked audio twin moves with it, so the two stay on the same frame;
* the rows the ripple did not name do not move: their record spans,
  their source ranges, their transforms and their effects read back
  exactly as they were.

A shifted clip is deleted and re-placed, so it carries a NEW identity:
the clip that moved is the same PICTURE at a new frame, not the same
item.  The scope is matched by name and kind for that reason.

### The defect the measurement found

The scope reading also found a defect in the ripple itself, and the
lane's own check now names it: the re-place of a shifted clip kept the
RAW source end, and Resolve reports that end in two forms - the
inclusive end on an existing clip, the exclusive end on a fresh
placement - while the re-place adds one to it.  A successor whose
source range was shorter than its media therefore came back ONE FRAME
LONGER than it was (measured 2026-10-21: a 48-frame successor came
back as 49 frames), and the ripple's own verification could not see
it, because it compared the arithmetic `source_in + duration` with
itself rather than with the source range the re-placed clip carried.

`library/tools/resolve_axi_core_commands.py` now speaks the ONE
reading (`_inclusive_source_end`, the helper the move and trim paths
already use), refuses a span that tells neither form rather than
re-placing it unfaithfully, and verifies the shifted clip against the
source range the inventory READ.

### The rows these scenarios widen

A run that measures LESS never narrows a row it did not re-measure:
the coverage a pair holds is the UNION of what the run measured and
what the shipped row already held.  So `clip.delete::edit_patch` and
`clip.remove::resolve_axi` now admit the states above and keep every
state they admitted before, and a state nobody demonstrated - a dense
timeline, a portrait project, a fractional frame rate - is still
refused.

## The two additions to the machinery

Both are the smallest change that makes a primitive qualifyable, and
both are refusals rather than new behaviour:

* `track.delete` refuses a row that carries clips
  (`library/tools/edit_algebra.py`).  Resolve's `DeleteTrack` destroys
  every clip on the row with no undo of its own; a row removal that
  took editorial work with it would destroy work the request never
  named.
* `resolve-axi edit place` refuses a track index the timeline does not
  have (`library/tools/resolve_axi_core_commands.py`).  Resolve answers
  an out-of-range track index by placing the clip on a row the request
  never named, which is exactly the silent substitution the punch list
  forbids.

## The harness's own rules

* One broker borrow, one private scratch timeline, one scenario at a
  time, each cleaned up before the next starts.
* The media a scenario RE-POINTS is copied out of the shared fixture
  pool into `/tmp` first, because a replacement writes to the pool item
  it re-points.  The shared fixture pool is never re-pointed.
* A scenario that did not commit on its first attempt is a failed row.
  There are no retries.
* A check that could not be read is `False`.  A qualification result
  whose predicates could not be measured is written as `failed`, which
  the registry records without enabling anything.
* A refusal is a recorded outcome, never a qualification: a scenario
  that EXPECTS a refusal produces no registry row.
