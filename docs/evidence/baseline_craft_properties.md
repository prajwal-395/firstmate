# Baseline craft properties

Tests: `tests/unit/picture/test_baseline_craft_properties.py`.

## Where P4's verdict lives, and where it must not

Two verdicts exist, and they answer different questions.

`build_verification.derive_verification_verdict` (step 6.01) asks whether the
TIMELINE was built as the manifest asked, off `timeline_qa` station reports.
Under the captain's ruling its outcome is advisory: `results["success"]` is
deliberately not gated on it.

`render_qa` (step 6.02) asks whether the EXPORTED FILE is deliverable, and its
outcome gates - `distribution_ready` False, exit code 1.

P4 (loudness and true peak) belongs to the second, and is the only opinion
anywhere in the pipeline about delivered loudness or peak:
`timeline_qa.verify_audio` says in its own docstring that it does not check
levels, because nothing sets one. So the two do not decide the same thing twice.

The hazard is that they COULD be coupled by accident: a `RenderQAResult` also
carries `.passed`, so routing render QA into the station list would typecheck,
run, and silently demote a clipping master from a failed build to an advisory
note. (The test class that held this note had no test bodies left; the
boundary is exercised by `TestTheGatesActuallyDecide`.)

## The P3 music offset is a required argument

`measure_speech_above_bed(..., music_offset_seconds)` has no default, and
`run_full_render_qa` declines to measure P3 when the offset is unknown. Two
tests pinned this by signature introspection and source text; they were
removed in the 2026-10 suite halving. The defect itself stays executable in
`TestP3SpeechAboveBed::test_fitting_from_zero_would_have_reported_a_bed_that_is_not_there`.
