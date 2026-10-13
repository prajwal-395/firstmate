# ren-filmcraft

Ren's headless FilmCraft adapter. It invokes only `filmcraft-cli`, never the desktop UI or its
crate graph. The pin lives in `src/lib.rs` as one immutable SHA (`FILMCRAFT_REVISION`);
that constant is the sole pin source and `library/tools/upstream_engines.json` mirrors it,
enforced by `assert_pins_match_crates` (`library/tools/local_ci/upstream.py`). The CLI is located from `REN_FILMCRAFT_CLI` or
`FILMCRAFT_CLI`; otherwise it is installed on demand into a revision-and-patch-set-addressed directory under
`~/.cache/ren/filmcraft/` (`<revision>-p<patch-set-key>`): the pinned revision is cloned, Ren's fork-patch
series (`library/tools/upstream_patches/filmcraft/*.patch`, embedded at build time by `build.rs`) is replayed
in order with `git apply`, and the CLI package is `cargo install --path`'d - the same replay the intake gate
performs, so the runtime binary is exactly what the gate qualified. The key (`FILMCRAFT_PATCH_SET_KEY`)
moves with the patch set, so an unpatched pre-patch binary is never reused; the Python resolvers
(`library/tools/project_model_cli.py`, `library/tools/project_model_qualification.py`) and the intake's
post-qualification install resolve the same directory through `library/tools/filmcraft_patches.py`.

The public API is a versioned typed request and receipt. `.fcproj` remains an engine projection,
and the adapter checks its explicit format identifier and schema version before accepting it.
Unknown adapter versions, project schemas, malformed command output and unrecognized command
response shapes are errors. The request vocabulary stays bounded to project create / import,
track provisioning, placement, razor, trim, move / insert, delete / ripple delete, link, retime,
transition, track gain, per-clip gain (`clip.audioGain`, mode `set`),
audio-track growth, H.264 / ProRes / WAV export, still
render and inspection. H264 exports carry an explicit bitrate and rate
control (`bitrate_kbps` / `bitrate_mode`, defaulting to Ren's 1080x1920
delivery default of 12 Mbps VBR 1-pass); bitrate on ProRes/WAV refuses,
because the engine silently ignores it there. Plus the model-content
families below: caption
tracks and overlay titles from speech/title observations, LUT import and
Lumetri input-LUT assignment, keyframed transform/crop/opacity on clip
uses, and track volume automation, with read-only caption/mixer/LUT
inspection and a 24fps sequence-rate pin for verbatim tick readback.

Run crate checks from the `native/` workspace. The fixture integration test uses the committed
short A/V clip and a real CLI built from the pinned SHA; it checks the known unlinked-audio razor
and trim-delta defects and requires an explicit loss report for either.

## Project Model adapter (`src/adapter.rs`, Unit 6)

The deterministic bridge between Ren's canonical Project Model
(`ren-protocol`) and this driver. `plan_projection` turns a model's clip
uses into ordered imports plus `timeline.place` operations with no I/O, so
the same model always plans identical bytes. Roles map onto fixed tracks:
`a_roll` on V1, `b_roll` on V2, `subtitle_overlay` on V3,
`motion_graphic` on V4, `frame_overlay` on V5, and full-frame `end_card`s
beside the footage on V1. Anything else refuses as `unsupported_role`.
Source-segmented person masks (`sam2_rle_track` and `region_rle`) are
projected only when their source clock and entity link match a placed
picture clip and a seed covers its source start. The raster seed becomes
FilmCraft polygon masks, whose measured raster IoU must be at least 0.92;
each polygon is then tracked on the placed clip. Face boxes stay evidence,
not person mattes, and remain a named projection loss.
`plan_edit` translates only explicit `filmcraft.*` model edits into driver
operations. `plan_ren_edit` translates Ren's own editorial vocabulary -
split, trim (both edges, including the E2E fixture's `timeline_end` /
`duration` shape), move/slide, link, delete (lift, or ripple delete with
`after.ripple`), standalone ripple (one compensated move per affected
placement, ordered so destinations stay free), constant retime
(`retime` / `span_retime` via `clip.speedDuration` with ripple, addressing
every reconciled engine clip of the use in one call, `reverse` riding the
engine's flag),
transition and audio gain - resolved against
the model and projection, with `timeline.patch` expanding into its inner
algebra operations. A stepped `retime.segments` ramp refuses both paths with
`retime_segments_not_supported` and expands instead through
`expand_retime_ramp` into razor points, link repairs, and one constant
`retime` per piece: a razor severs the A/V link on later pieces (measured
distinct link ids), a retime over severed ids ripples once per id, and
`clip.link` is a toggle, so each severed piece is re-linked first
(clear, then link - receipts read false-then-true) while the intact first
piece needs no repair. A reversed piece rides its retime's flag; only
whole-ramp reversal refuses, with `reverse_not_supported`. Inexact rates,
fractional-frame retime spans, overlapping ripple destinations and
plan-level records refuse
with named codes (`retime_rate_inexact`,
`retime_inexact_duration`, `ripple_would_overwrite`,
`plan_level_not_executable`); gain without an
audio stream refuses as `no_audio_for_gain`. The engine's remaining
retime gap - variable speed has no engine parameter at all - is tracked in
`library/tools/upstream_patches/filmcraft/REQUESTS.md`, not fork-patched. A split over linked audio
plans two razors (video plus audio) so the measured V1-only razor defect
does not recur; delete, ripple delete and retime rely on the engine
following linked audio itself (measured); a Ren move compensates the measured one-frame-early
`timeline.move` defect to land frame-exact. Overlap refuses per track
pair: the same track never stacks, and base picture (`a_roll`, `b_roll`,
`end_card`) stays sequential even across tracks, while a compositing
overlay may overlap the base and other tracks' overlays - captions over
dialogue are the normal case, not an error. Only base picture carries
audio; overlay and card files are never wired to the mix. A move that trims an occupant
reports `move_overwrote` instead of trimming silently. `apply_ren_edit_to_model`
mutates clip uses for the equivalence proof, so qualification compares the
engine's inspected timeline against Ren's expected clip uses frame-exact
and the render's frame map against the mutated model's map. Constant
retimes log into `model.edits` so the map carries their exact rational
rate as `Retimed` segments; a slow-motion span longer than its source
re-projects exactly when its logged retime explains the ratio, and still
refuses as `source_too_short` otherwise. A model with overlays but no
`a_roll`/`b_roll`/`end_card` spine refuses as `no_base_spine`, and the
frame map packs the spine alone - overlays draw onto the base frames and
add no render time. Model effects,
masks, and object tracks become explicit loss entries, never silent drops.
Compositing overlays additionally carry an `overlay_unpositioned` loss:
the projection has no placement parameters to send, so an overlay with
no model `transform.position` effect draws at the engine's default
position. Static `transform.position` (sequence pixels) and uniform
positive multiplicative `transform.scale` factors (`1.0` is unchanged)
execute over the target clip's full span through the measured
`effects.setParam` path, converted to FilmCraft's percent scale;
keyframed
transforms keep their timed writes. `verify_media`
gates overlay and card codecs through ffprobe before anything runs:
ProRes with alpha composites (measured), while QuickTime Animation and
PNG decode to nothing and refuse as `overlay_codec_not_supported` -
transcode those to ProRes 4444 first (H264 is accepted for opaque end
cards). Overlays needing V4/V5 plan one `sequence.addTracks` operation
(`add_tracks_operation`, driven once after create) because fresh projects
carry three video tracks. The Project Model CLI's optional
`--sequence-size WIDTHxHEIGHT` sets and reads back the sequence frame
before import and placement; position effects are sequence pixels.
`reconcile_clip_ids` maps stable Ren clip-use ids back onto engine-minted
clip ids (linked-audio placements mint one id per track; edits address the
whole array for retime and link, the first id otherwise - per-clip gain
rides the first id because the engine follows clip links onto the audio
item, measured). `build_frame_map` emits the exact timeline-to-render map, with
timeline gaps as explicit `Gap` segments and reversed retimes carrying a
negative rate numerator (-1/1: the sign is the direction).
`validate_render_asset` checks the
export exists, is non-empty, and decodes to at least one stream when ffprobe
is available. Driver losses (including both measured defects) pass through
`summarize_losses` verbatim. `tests/adapter_qualification.rs` drives
placement, trim, split, link repair, delete, ripple delete, ripple, retime, ramps, reverse (including a render-level direction proof), transition, audio, render, and unsupported-op
behavior through the pinned CLI.

 Model content FilmCraft executes natively (adapter contract version 5):

 - captions/titles: `speech`/`caption` observations plan into a caption
   track (`plan_captions` to `captions.newTrack`/`add`/`setText`/`setTimes`,
   read back with `captions.list`); `title` observations plan into overlay
   titles bound to the overlapping clip (`plan_titles` to
   `graphics.newText`, read back by item name/span). Each has an
   `apply_*_to_model` counterpart anchoring the plan to the model.
 - LUTs: `color.lut` effects (`{"lut": "builtin:…"|"lib:…"|path}`) plan
   into `lut.import` (file paths, two-phase via the receipt `ref`) plus
   `lumetri.setInputLut`, read back through the clip's Lumetri
   `input_lut` value.
 - keyframed picture: `transform.position/scale/rotation` and
   `opacity.opacity` keyframes plus static `transform.crop` rects plan
   into `effects.apply` (crop only) + `effects.toggleAnimation` + timed
   `effects.setParam` writes, read back tick-exact from the project file
   (`read_effect_keyframes`; inspect reports counts only).
 - volume automation: `audio.volume` dB keyframes plan into
   `mixer.writeAutomation` on the placement's audio strip, read back with
   `mixer.inspect` lanes.
 - out-of-span guard: a key outside its clip's timeline span refuses with
   `keyframe_out_of_span` at planning time (the engine answers such
   writes with success while placing nothing - measured), so the whole
   projection refuses fail-closed instead of executing.
 - EffectCraft boundary: `move.*`, `blur.gaussian`, `color.saturation`,
   masks and track mattes stay with EffectCraft
   (`effect_owned_by_effectcraft` / existing mask/track losses); anything
   else unmapped keeps `effect_not_projected`. Pin the sequence to the
   model's timeline rate (`SequenceSettings`, 24.0 in qualification) so
   tick readback is verbatim; off-rate sequences inherit the engine's
   frame floor.

## Native audio mix (`music`/`sfx` stems, per-clip gain, bed ducking)

`plan_projection` maps `music` and `sfx` clip-use roles onto audio strips
from A2 upward: the bed allocates first (a declared crossfade is a real
overlap, so its pieces take two strips), then SFX starts above whatever
the bed used - the order `library/tools/otio_mix.py` and `music_bed.py`
render in on the Resolve path. A1 stays linked dialogue at unity. Picture
overlaps still refuse; stem overlaps allocate. Plans addressing strips
past the fresh sequence's own A1-A3 grow the sequence first
(`AddAudioTracks`, audio only - no stray video track). Per-clip
`audio.gain` edits plan into `ClipAudioGain`; the model mutation logs the
span-neutral edit so qualification compares the inspected `gainDb`
against the log and re-plans each logged edit identically. Bed ducking
(`audio.ducking` effects on `music` uses) derives a ramped dip under the
effect's own interval into `mixer.writeAutomation` on the bed's strip;
the full level, dip depth and ramp all arrive declared on the effect, and
`adapter_qualification` proves the rendered dip per-channel RMS against
the declared depth (adapter contract version 5).

Explicitly unwired, never silent: keyframed ren edits (`audio.fade`,
`audio.automation`) still refuse as
`audio_keyframes_require_volume_automation` - edits carry no declared
depth or ramp of their own, so the ducking route stays the declared
`audio.ducking` effect, not a second keyframe path off edit parameters.
`audio.pan` refuses as `audio_pan_not_projected`: Ren's mix has no
measured pan mapping (the OTIO surface is volume only), so a pan would
render differently per engine. Engine-generated ducking
(`essentialSound.generateDucking`) is deliberately not driven: it would
substitute the engine's own ducking amounts for the mix the plan
declared, and Ren's ducking curve ships as derived volume automation
from the declared effect instead.

No pipeline producer mints `music`/`sfx` uses or `audio.ducking` effects
yet: the editorial bridge (`library/tools/project_model_editorial.py`)
covers A-roll/B-roll nodes and carries mix nodes as operations only.
Minting manifest bed segments and SFX buckets into stem uses (plus static
`audio.gain` edits from `mix_targets`, plus `audio.ducking` effects from
the manifest's `music_ducking_plan` depth/ramp over its dialogue spans)
attaches there, the way toolkit moves attach at `post_bridge.py` -
stated here so the projector's lack of a live caller reads as a gap,
not a guess.
