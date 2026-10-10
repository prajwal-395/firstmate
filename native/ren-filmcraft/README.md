# ren-filmcraft

Ren's headless FilmCraft adapter. It invokes only `filmcraft-cli`, never the desktop UI or its
crate graph. The pin currently lives in `src/lib.rs` as one immutable SHA; replace that source with
the intake lane's manifest constant when it lands. The CLI is located from `REN_FILMCRAFT_CLI` or
`FILMCRAFT_CLI`; otherwise it is installed on demand into a revision-addressed directory under
`~/.cache/ren/filmcraft/` using the exact Git revision and Cargo's lockfile.

The public API is a versioned typed request and receipt. `.fcproj` remains an engine projection,
and the adapter checks its explicit format identifier and schema version before accepting it.
Unknown adapter versions, project schemas, malformed command output and unrecognized command
response shapes are errors. The request vocabulary stays bounded to project create / import,
placement, razor, trim, move / insert, transition, audio gain, H.264 / ProRes / WAV export, still
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
operations; legacy strings such as `clip.trim` refuse as
`unsupported_edit_operation` instead of being inferred. Model effects, masks,
and object tracks become explicit loss entries, never silent drops.
`reconcile_clip_ids` maps stable Ren clip-use ids back onto engine-minted
clip ids (linked-audio placements mint one id per track; edits address the
first). `build_frame_map` emits the exact timeline-to-render map, with
timeline gaps as explicit `Gap` segments. `validate_render_asset` checks the
export exists, is non-empty, and decodes to at least one stream when ffprobe
is available. Driver losses (including both measured defects) pass through
`summarize_losses` verbatim. `tests/adapter_qualification.rs` drives
placement, trim, split, transition, audio, render, and unsupported-op
behavior through the pinned CLI.
