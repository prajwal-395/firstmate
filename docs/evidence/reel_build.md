# Reel build

Incident history moved verbatim out of the `tests/test_reel_build*.py` files.
The tests keep the invariant; this file keeps how each was found.

## `test_reel_build.py` - a reel must carry BOTH speakers' audio

Measured 2026-09-04: the first sixteen reels were built with two video
tracks and ONE audio track. Akshita's V1 clips carried their sound to
A1; Craig's V2 clips had nowhere to put theirs, so Resolve placed his
picture and discarded his audio while returning True. Sixteen reels
reported success and played with one speaker silent.

The helpers that first closed this (`required_tracks`, `audio_layout`,
`strays`) are gone: the track plan (`timeline_layout.plan_layout`)
owns row counts and names now, and stream enforcement reads every
placed audio item back. Their coverage lives in
`tests/test_reel_build_sop_conformance.py`, which drives the real
`build_reel_timeline` against fake Resolve and reads the timeline
back through the verifier.

## `test_reel_build.py` - test_the_reel_resolution_is_explicit_and_declared

The PROJECT default is 3840x2160 and only the existing timelines
override it, so a timeline created through the API inherits the
horizontal default - a silent wrong answer, not an error. So the
reel timeline is sized EXPLICITLY.

What it is sized to is the project's DECLARED delivery format, not
`REEL_RESOLUTION = (1080, 1920)`. A project that declares nothing
still gets vertical, which is what every reel already built was
built at.

The input that breaks this: writing the frame back as a constant.
A project declaring `horizontal_1920x1080` below would then build a
vertical timeline and composite horizontal overlays onto it - the
001 defect, which gemma-4-12b named unprompted on five of eight
sampled frames.

## `test_reel_build.py` - test_a_finely_segmented_retake_is_cut

Reel 03: three takes of one sentence, none of them long.

`MIN_TAKE_SECONDS = 1.5` skipped any pair where either side was
shorter than that, so a retake WhisperX segmented into sub-second
pieces was never even scored. Reel 03 of the captain's approved
nineteen played "search didn't change, the question changed, whoever
AI understands best gets the answer" three times inside forty
seconds, and `redundant_takes` returned an empty list for it while
the proposal's own detector reported the repeat at similarity 1.0.

Both sides here are well under the old floor and their durations
agree, which is exactly the shape the floor uniquely blocked.

## `test_reel_build.py` - test_an_overlay_import_lands_in_its_declared_bin_not_in_current

The destination is binding at import time: the import happens
with the current folder set to the overlay's declared bin, and the
previous current folder is restored afterwards. Measured
2026-09-10: a fresh build left every overlay render in Source
footage because CURRENT was there and no organise followed.

The declared bin for this builder-written overlay is `03 -
Assets`: a production asset no render step wrote is not a
per-reel render (`overlay_import_bin`, AGENTS.md 14), so the
binding the test proves is import-into-Assets, not import-into
the reel's leaf.

## `test_reel_build_lease_shape.py` - <module>

The reel build holds the placement, not the build.

`rebuild_reels_in_project` used to hold the machine-wide EXCLUSIVE
Resolve lease for its entire body - minutes of Remotion caption
renders and model-answer reads - while the cursor writes that lease
exists to protect take seconds per reel. Reel throughput was capped
at one build at a time however many lanes ran.

The shape pinned here: derivation holds NOTHING, one exclusive hold
per placed reel covers the carried self-read, the decision, the
placement and the Fusion comp pass, and handle-only gate/survey reads
run under shared holds. Cursor-moving drift and digest reads take
exclusive holds. A test that only asserts "the build takes
a lease" would pass on both shapes; these fail on the old one.

The failure mode the narrower hold must still survive is
demonstrated deliberately below: a writer that never takes the lease
(the captain editing by hand) moving the cursor mid-placement. The
per-write check re-establishes the cursor before every append, so
the placement lands where it was aimed with or without a whole-build
hold - and the control shows what the same interleaving does to
writes without the check (2026-09-04: 579 captions onto the wrong
reel while every call returned True).

## `test_reel_build_environment.py` - <module>

What a reel build needs in its interpreter is DECLARED, and checked first.

Four instances, all on 2026-09-10/11, each costing a lane a failed build:
a cv2 without Haar cascades answering None from the face probe, a cv2 5.0
refusing the punch-in aim, and a purpose-built cv2 4.12 venv missing
`jsonschema` on the very next attempt. (The Remotion half already has an
owner.) Every lane fixed it locally with its own venv, each missing
something different - nothing declared the set, so every attempt
rediscovered a different subset.

`library/tools/shared_environment.py` (the BUILD half) is the one owner;
`env.face_detector` and `env.reel_build_libraries` in
`library/tools/requirements.py` are the pre-build refusal both reel-build
lanes share. A missing detector, cascade file or library is ONE CLEAR
MESSAGE BEFORE THE BUILD STARTS, naming what is missing and what would
supply it.

Nothing here reaches Resolve, renders, or a real project. The cv2 in
each case is a fake in `sys.modules`, because the point is what the
declaration says about each machine shape - not what this machine's
own cv2 happens to be.

## `test_reel_build_pool_filing.py` - <module>

A build files where it imports; the organiser has nothing to repair.

The captain's field-test project held timelines in motion-graphics bins
and caption clips beside them, plus duplicate pool entries for the same
file - because `CreateEmptyTimeline` and `ImportMedia` land in whatever
bin is CURRENT, and because re-importing a path already pooled makes a
second item rather than returning the existing one. The build now
decides every destination up front through `resolve_bin_layout` (the
one owner of bin paths) and looks every path up before importing it, so
a timeline lands in the reels bin and a subtitle clip in the subtitles
bin because the code cannot put them anywhere else - not because a
cleanup pass moved them afterwards.
