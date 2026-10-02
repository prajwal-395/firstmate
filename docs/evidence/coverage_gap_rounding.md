# coverage gap rounding

Test: `tests/test_coverage_gap_rounding.py`.

A sub-frame abutment is not a hole in the timeline.

`_video_coverage_gaps` exists to catch the 6.4s of black that once
shipped, and it counts in frames rather than seconds because float noise
between abutting clips would otherwise read as findings. But the frame
number of each clip boundary is rounded INDEPENDENTLY, so two clips that
genuinely meet can land on different frames: on project 001, clips
meeting at 43.646s and 43.650s became frames 1309 and 1310 and failed
compile_manifest with "1 uncovered range totalling 0.004s".

Four milliseconds is an eighth of a frame at 30fps. It cannot render as
black. It stopped a render one step from the end.

So a hole now has to be a frame wide in BOTH clocks. These tests hold
that line from both sides: the rounding artefact must pass, and the real
hole the assertion was written for must still fail.
