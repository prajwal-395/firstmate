# ren-filmcraft

Ren's headless FilmCraft adapter. It invokes only `filmcraft-cli`, never the desktop UI or its
crate graph. The pin lives in `src/lib.rs` as one immutable SHA (`FILMCRAFT_REVISION`);
that constant is the sole pin source and `library/tools/upstream_engines.json` mirrors it,
enforced by `assert_pins_match_crates` (`library/tools/local_ci/upstream.py`). The CLI is located from `REN_FILMCRAFT_CLI` or
`FILMCRAFT_CLI`; otherwise it is installed on demand into a revision-addressed directory under
`~/.cache/ren/filmcraft/` using the exact Git revision and Cargo's lockfile.

The public API is a versioned typed request and receipt. `.fcproj` remains an engine projection,
and the adapter checks its explicit format identifier and schema version before accepting it.
Unknown adapter versions, project schemas, malformed command output and unrecognized command
response shapes are errors. The request vocabulary stays bounded to project create / import,
placement, razor, trim, move / insert, delete / ripple delete, link, retime,
transition, track gain, per-clip gain (`clip.audioGain`, mode `set`),
audio-track growth, H.264 / ProRes / WAV export, still
render and inspection.

Run crate checks from the `native/` workspace. The fixture integration test uses the committed
short A/V clip and a real CLI built from the pinned SHA; it checks the known unlinked-audio razor
and trim-delta defects and requires an explicit loss report for either.

## Project Model adapter (`src/adapter.rs`, Unit 6)

The deterministic bridge between Ren's canonical Project Model
(`ren-protocol`) and this driver. `plan_projection` turns a model's clip
uses into ordered imports plus `timeline.place` operations (A-roll on V1,
B-roll on V2) with no I/O, so the same model always plans identical bytes.
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
`timeline.move` defect to land frame-exact. A move that trims an occupant
reports `move_overwrote` instead of trimming silently. `apply_ren_edit_to_model`
mutates clip uses for the equivalence proof, so qualification compares the
engine's inspected timeline against Ren's expected clip uses frame-exact
and the render's frame map against the mutated model's map. Constant
retimes log into `model.edits` so the map carries their exact rational
rate as `Retimed` segments; a slow-motion span longer than its source
re-projects exactly when its logged retime explains the ratio, and still
refuses as `source_too_short` otherwise. Model effects,
masks, and object tracks become explicit loss entries, never silent drops.
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

## Native audio mix (`music`/`sfx` stems, per-clip gain)

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
against the log and re-plans each logged edit identically.

Explicitly unwired, never silent: keyframed audio (`audio.fade`,
`audio.automation`) refuses as `audio_keyframes_require_volume_automation`
until the volume-automation op lands - this adapter plans static clip
gain only, not a second keyframe path. `audio.pan` refuses as
`audio_pan_not_projected`: Ren's mix has no measured pan mapping (the OTIO
surface is volume only), so a pan would render differently per engine.
Engine-generated ducking (`essentialSound.generateDucking`) is deliberately
not driven: it would substitute the engine's own ducking amounts for the
mix the plan declared, and Ren's ducking curve ships as keyframed volume
automation from the plan instead. Keyframed ducking itself arrives with
the volume-automation op; static gains are the whole mix until then.

No pipeline producer mints `music`/`sfx` uses yet: the editorial bridge
(`library/tools/project_model_editorial.py`) covers A-roll/B-roll nodes
and carries mix nodes as operations only. Minting manifest bed segments
and SFX buckets into stem uses (plus static `audio.gain` edits from
`mix_targets`) attaches there, the way toolkit moves attach at
`post_bridge.py` - stated here so the projector's lack of a live caller
reads as a gap, not a guess.
