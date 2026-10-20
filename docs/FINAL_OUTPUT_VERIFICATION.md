# Final-output verification: what already answered the eight criteria

The captain's capability punch list, section 6 ("Final-Output
Verification and Quality Control"), names eight acceptance criteria and
its definition of done is: *"Ren can differentiate a structurally valid
timeline from a technically compliant, visually coherent, and
editorially acceptable final output. A verified export includes evidence
for every required acceptance criterion."*

Most of the MEASUREMENTS behind those criteria already existed. What
did not exist was the ONE thing that turns them into a verdict: an
enumeration of the eight, a runner that asks each question in turn, and
an answer that says which criterion could not be determined and why.

That is `library/tools/export_verification.py`, and the model reaches it
through the `verify_export` skill
(`library/skills/verify_export/skill.py`,
`.agents/skills/verify_export/SKILL.md`).

This document is the per-item account the lane owes: what was already
done, what this lane added, and what still needs live evidence.

## The shape

Three records are read, each from where the engine wrote it:

- **the plan** - the accepted edit plan, the compiled
  `assembly_manifest.json`.
- **the build** - the built timeline's OWN record: the committed
  timeline snapshot under `pipeline_output/review/`. On disk, so the
  export can be verified against the build with no Resolve and no
  render.
- **the export** - the delivered file, and the identity record the
  delivery wrote beside it (the ledger and the `.deliver.json`
  sidecar).

A criterion whose evidence is missing is UNKNOWN, never a pass. An
export nobody could check is an unchecked export, and the verdict says
so.

---

## QA-001 - clip and track structure against the accepted edit plan

**Already done.** `manifest_validator.validate_manifest` checks the
plan's own structure (track and clip overlap, timecode sanity, CDL
bounds, transition validity) and its semantic half (no zero-duration
clips, distinct cut points, SFX distribution, B-roll correspondence, no
repeated source audio, no flash captions, no effect family on
everything, the subject surviving the conform).
`reel_conformance_verifier.check_plan_describes_timeline` compares a
reel's plan with its built timeline, frame-exact.
`timeline_conformance.verify_timeline` reads the live timeline against
the SOP (no empty rows, no default-named rows, one role per row).

**Added.** The plan's tracks matched against the BUILD's rows, by
TIMELINE RANGE rather than by name, on the committed snapshot: a
planned clip the build dropped, a clip the build added, and a picture
row the plan forgot are each named. A row holding media the plan never
declared is an overlay row, and is reported rather than failed.

**Live evidence still needed.** The live row structure
(`timeline_conformance`) and the reel-level frame-exact plan check
(`reel_conformance_verifier`) read Resolve; this lane's comparison must
be shown to agree with them on a real reel.

## QA-002 - exact timing, source ranges, media identity, linked audio

**Already done.** `library/tools/qa/timeline_sync_qa.py` compares
planned positions with actual ones (live). `reel_read.clip_detail`
reads a placed clip's record and source ranges (live).
`footage_identity.fingerprint` is the media's own identity. `reel_hearing`
hears the render against what it was planned to say.

**Added.** The per-clip comparison of the plan against the build's
committed record: the timeline range to a frame, the source range
against the source's MEASURED frame rate, the media identity (the
paths, then the fingerprint when the paths differ), and the linked
audio - the speech clip's source range against the picture beside it,
and the sync offset it carries.

**Live evidence still needed.** The live read of
`GetSourceStartFrame`/`GetSourceEndFrame`/`AudioSyncOffset` from
Resolve. The snapshot is the committed copy of that read; the two must
be shown to agree.

## QA-003 - missing media, black frames, frozen frames, gaps, overlaps

**Already done.** `render_qa.detect_black_frames` (with the spine's
declared beats), `render_qa.detect_freeze_frames`,
`render_qa.measure_silence_under_picture`, `manifest_validator`'s
source-file existence, `compile_manifest.assert_overlay_segments_on_disk`,
`reel_conformance_verifier.check_picture_holes`/`check_audio_holes`
(live), and the validate step's own V1 tiling check.

**Added.** One criterion that runs the file checks AND the build-side
gap/overlap walk, and the missing-media check over the files the BUILD
points at rather than the files the plan names.

The gap rule was measured, not guessed: a V1 gap that a B-roll cutaway
covers is NOT a hole, and a V1-only tiling check refused exactly that
on the round3 snapshot (four V1 gaps, each one exactly a V2 clip). So
the holes are read over the whole PICTURE, V1 and V2 merged, while the
overlaps are read WITHIN a row - V2 over V1 is how B-roll works.

**Live evidence still needed.** The live hole read
(`reel_conformance_verifier.check_picture_holes`) on a real reel.

## QA-004 - output format, frame rate, dimensions, duration, encoding

**Already done.** `render_qa.verify_resolution`, `verify_framerate`,
`verify_duration` and `verify_audio_streams`. `reel_deliver`'s
`verify_deliverable` wired them for the delivery, with its own codec
check.

**Added.** `render_qa.verify_encoding` - the codec, the pixel format and
the container. The container is measured from the file's OWN BYTES,
because ffprobe cannot tell an `.mp4` from a `.mov`: both report
`mov,mp4,m4a,3gp,3g2,mj2`, because both are ISO base media files, and
a QuickTime file named `.mp4` is exactly what a platform refuses. And
one criterion that grades all four legs against the declared frame, rate,
length and encoding, and names the leg it could not grade.

**Note.** The delivery's declared preset is checked at the DECLARATION
(`reel_deliver.resolve_deliver_settings`); this lane checks the FILE,
which is the half that was unmeasured.

## QA-005 - subtitle/caption timing and presence

**Already done.** `subtitle_qa.verify_subtitle_timing` checks the
plan's caption timing against the spine.
`library/tools/qa/subtitle_qa.py` checks the rendered overlay's
geometry (drawn, clipped, in the safe zone). `subtitle_coverage`
measures word-level coverage. `render_qa.measure_caption_legibility`
and `measure_caption_obscuring` report on the captions' readability.

**Added.** One criterion that runs the plan-side timing AND checks that
every declared caption REACHED the timeline - on its own row, over the
declared window. The search is over every row the build carries, not
only the rows the plan names: a caption is placed on a row no planned
track describes, and a search over the planned rows alone finds no
caption anywhere and calls every one of them missing.

**Not covered, and why.** Burnt-in caption INK cannot be separated from
the picture in an exported file, so presence is established at the
BUILD boundary (the caption row of the accepted revision). The pixels
are the model-judged half (`ask_the_footage`) and the overlay-level
half (`qa/subtitle_qa`). The caption's PLACEMENT and SCALE are the
caption-quality checks' business, not QA-006's: the punch list gives
captions their own criterion (timing and presence) and gives the
graphics theirs (placement and scale), so grading a caption under both
would be two rules for one thing.

## QA-006 - graphical elements at the expected placement and scale

**Already done.** `overlay_verify.verify_values` compares a placed
overlay's stored transform against the declared or computed intent;
`verify_rendered_boxes` and `verify_pixels` read the rendered result.
`render_qa.measure_graphics_render` verifies the overlay's ink is
present in the rendered frame. `qa/subtitle_qa.check_caption_geometry`
checks the caption's own box.

**Added.** The plan's graphical elements enumerated, their artefacts
checked on disk, their presence on the timeline checked, and the pixels
measured over the SAME windows the validate step uses. The span
builders moved to `library/tools/qa_spans.py` so the two grade one
definition of "the graphical element" rather than two.

**Live evidence still needed.** The placement and scale READ-BACK
(`overlay_verify.verify_values`) needs the live timeline: the stored
Scaling mode is not in the committed snapshot. Without it the criterion
reports UNKNOWN and names that check, which is the honest answer - the
placement is not one of the things a snapshot can prove.

## QA-007 - loudness, peaks, clipping, channel configuration

**Already done.** `render_qa.measure_lufs` grades the integrated
loudness AND the true peak, both halves gating.
`deliverable_qc.run_deliverable_qc` binds duration, black frames and
loudness to ONE delivery spec. `master_loudness` masters to it.
`audio_quality` measures the source audio.

**Added.** `render_qa.detect_clipping` and
`render_qa.verify_channel_configuration`, and one criterion that runs
all four legs against the delivery spec's loudness target and the
declared channel count.

The clipping check answers a different question from the true-peak
half: that one asks "is it too loud", this one asks "is it damaged". A
clipped sample is one that reached full scale and STAYED there - a run
of identical samples at the ceiling. A signal that merely reaches the
ceiling once per cycle is loud, not a flat top.

The channel bound is MECHANICAL, not a taste call (AGENTS.md 10.5): a
delivered program mix is mono or stereo, and a file carrying a
six-channel bed is carrying stems the platform was never given a way to
play. No delivery-spec row declares a channel count today, so the
criterion applies that bound and says so; a caller that declares its
own count is matched exactly.

## QA-008 - export identity and provenance against the project revision

**Already done.** `deliver_all`'s ledger records the delivery signature
over the timeline, the settings, the spec, the plan digest and the
assignment, and `deliver_all.needs_render` already refuses to treat a
changed signature or a replaced file as up to date. The `.deliver.json`
sidecar records the frame range, the settings, the render and the
verification. `plan_provenance` records which snapshot each timeline was
built from, the plan's content hash, and the caption and footage
bindings. `provenance.py` records which step wrote which file, in which
run, from what.

**Added.** One criterion that ties the export to the delivery record
AND to the revision that produced it, reading the provenance record for
the timeline's snapshot and REFUSING to grade one that is not recorded
(`plan_provenance.built_from_snapshot` names why).

**Not moved.** The staleness comparison against a re-derived signature
stays in `deliver_all.needs_render`: it is the delivery's own gate and
it already refuses a re-render that would produce the same bytes.

---

## What the lane leaves open

1. **The live read-backs.** The placement and scale of an overlay, the
   live row structure, the live hole read and the live source ranges
   need Resolve. They are the live lane's evidence; this lane names
   them rather than pretending to have measured them.
2. **The burnt-in caption ink.** Presence is established at the build
   boundary. Whether the caption READS is the model-judged half.
3. **The reel-level plan check.** `reel_conformance_verifier` grades a
   reel against its re-derived plan; this lane grades an export against
   the accepted plan and the build's committed record. Both are needed:
   the first says the reel is the plan, the second says the file is the
   build.
