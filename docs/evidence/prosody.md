# Prosody that measured nothing - the history behind its tests

Moved out of `tests/unit/audio/test_prosody_failure_is_loud.py` and
`tests/unit/audio/test_prosody_view.py` on 2026-10-02, when those files kept only
their invariants. The tests and `library/tools/prosody_profile.py` are the
contract; this is the story of how it was found.

## The seventeen hollow records (project 001)

`praat-parselmouth` is step 1.05's only measuring instrument and its
manifest listed it as a precondition since the step was written - while
`requirements.txt` never carried it. On project 001 the step reported
SUCCESS in 0.1 seconds having written seventeen files that each said
`{"prosody": {"method": null, "error": "parselmouth not installed"}}`, and
4.2 KB of those identical error records were serialised into the
creative-direction prompt as if they were measurements. One arm of the A/B
on that context said, unprompted, "There is no actual prosody data to
evaluate... I had to completely ignore this section."

Two halves of the fix, and the second is the worse defect:

1. the dependency is declared in `requirements.txt`;
2. a step that cannot do its job reports `available: false`, which
   `run_pipeline.check_output_is_real` reads as a failed step - and it
   writes no profile file, because a profile file IS the cache, so an
   error record on disk made the failure permanent as well as silent.

A path allow-list cannot fix the prompt half: `context_fields` selects by
NAME and these records have the right names. `view:prosody` selects by
`profile_defect` - the same predicate step 1.05 refuses to write a hollow
profile with - and reports the absence in one line instead of hiding it.

## The stale record that blocked re-analysis

The stale-record test once hand-wrote `pipeline_output/prosody` while the
step resolves `Area.PROSODY` to `pipeline_output/steps/1_05_prosody_analysis`,
so the step reported `0 cached, 1 to analyze` and never opened the record -
the assertion passed on the missing dependency, not on the stale record.

It also asserted `available is False`, which is a statement about the
ENVIRONMENT: with parselmouth installed (2026-08-28) the clip is
re-analysed and correctly reports `available: true`, so the assertion broke
although nothing about the stale record had changed.

The record was rejected at collection - correctly - and then its mere
existence counted as "already analysed", so it permanently prevented the
measurement that would have replaced it. Measured 2026-08-28 on a working
parselmouth: `1 cached, 0 to analyze`, `available: false`. The cache check
now runs the same `profile_defect` the collection half does.

## The .MOV container

Praat raises `PraatError: Not an audio file` on a .MOV container, and this
was the inner failure hidden behind the missing parselmouth dependency for
weeks. The step reads the temporal index's audio cache WAV instead.
