# The candidate generation's evidence, in one file (VI-002b), 2026-10-22

The rule, the measurement and the artefacts live in
`docs/benchmarks/2026-10-22-vi002b-candidate-recall.md`.  This file is
the evidence itself: what the code does, what it was measured against,
and where each number came from.

## The rule

**A source that the evidence establishes at more than one moment may
take more than one of the editor's slots, and the cameras are served by
the share of their own recording that their established moments span.**

| part | where | what it does |
|---|---|---|
| the judged candidates are DISTINCT MOMENTS | `library/tools/retrieval/temporal_rerank.py` - `candidate_intervals` | the camera's best moment, then the moments that show the query AGAIN, instead of three adjacent slices of one passage |
| the pass judges the second moment of the cameras that hold a slot | `library/tools/retrieval/temporal_rerank.py` - `rerank` | the extra slot is EVIDENCE: the model is asked about the second moment, and the slot is taken only if it establishes the query there |
| the cameras are served by the share of their recording the moments span | `library/tools/retrieval/temporal_rerank.py` - `select`, `_span_share` | a camera that shows the thing across most of its take holds the passage the editor wants to scrub |
| the windows are still presented best-first | `library/tools/retrieval/temporal_rerank.py` - `_by_score` | the editor's first pick is the best-scoring window, exactly as before |

The lane's own duration reading lives in
`library/tools/analysis/footage_frames.py` - `_source_durations`.

## What was measured, and where the numbers came from

| number | value | where it came from |
|---|---|---|
| held-out visual macro recall, before | 0.0539 | the base commit, on the rebuilt scratch project, the same harness command |
| held-out visual macro recall, after | 0.0696 | this change, the same command, the same corpus |
| held-out visual macro precision | 0.3250 -> 0.4667 | the same two runs |
| held-out false-positive windows | 20 -> 16 | the same two runs |
| held-out false-positive seconds | 160.16 -> 130.13 | the same two runs |
| wrong seconds / missing seconds / coverage / shadowing | unchanged, both sets | the same runs |
| merged visual macro recall | 0.0691 -> 0.0927 | the base commit vs this change, on the rebuilt scratch project |
| merged visual macro precision | 0.3333 -> 0.4667 | the same two runs |
| merged false-positive windows | 10 -> 8 | the same two runs, false-positive seconds 70.07 unchanged |
| `not_in_candidate_pool` | 0, before and after, both sets | the VI-001 stage attribution, all four runs |
| `not_presented` / `ranked_too_low`, held-out | 40 / 113 -> 36 / 107 | the same attribution, both runs |
| `not_presented` / `ranked_too_low`, merged | 20 / 54 -> 18 / 50 | the same attribution, both runs |

Denominators, never combined: the held-out set's 18 editorial
queries, 6 of them present visual, 172 wanted items; the merged set's
16 editorial queries, 3 of them present visual, 86 wanted items; the
frame lane's own denominator in both.  The text lane is reported apart
and could not be rebuilt (the M1 transcripts live in the corpus
directory a macOS privacy block closed), so the editorial macro
numbers over BOTH lanes are not comparable with the committed reports
- the numbers above are the frame lane's.

## The corpus

The captain's corpus directory became unreadable during the
measurement, so the scratch project was rebuilt from the per-source
memory (the M2 frame samples) and the corpus manifest the eval set
carries.  The rebuilt index reproduces the shipped baseline EXACTLY -
0.0539 / 20 false-positive windows / 160.16 false-positive seconds on
the held-out set before the change, on both the shipped index and the
rebuilt one - which is what makes the two halves one measurement.

## The tests that name the defects

`tests/unit/picture/test_temporal_rerank.py`:

* `test_the_candidates_judged_are_distinct_moments_and_not_slices` -
  three adjacent slices of one passage are not three candidates.
* `test_a_camera_that_shows_the_query_again_holds_two_of_the_slots` -
  the second slot is evidence, not a guess.
* `test_the_pass_stops_when_the_editors_list_is_full` - the pass stops
  when the list is full AND every camera in it has been judged at its
  second moment.
