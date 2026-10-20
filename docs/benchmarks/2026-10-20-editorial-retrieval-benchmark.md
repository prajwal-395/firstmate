# Editorial retrieval benchmark, and the acquisition producer ledger (2026-10-20)

The captain's punch list asks for three things this report measures:

* **VI-002** - retrieval is benchmarked on REAL EDITORIAL searches, not on
  broad object descriptions.
* **VI-009** - a retrieval change is compared against the incumbent it would
  replace, rather than adopted because it is newer.
* **VI-010** - recall AND the false-positive consequence are tracked
  together, because a wrong window is not free.

It also produces what PR 2928 named as the live evidence it owed: a frozen
producer ledger made by the NEW acquisition policies, scored by
`library.tools.acquisition_benchmark` over the held-out tasks and compared
per lane against `fixed_dense` and `fixed_sparse`.

## What was run, and where

| | |
|---|---|
| Machine | this machine, 2026-10-20, `main` at `c52e5bded` |
| Corpus | GEO vs SEO podcast, 7 MXF camera originals, 181.58 video minutes |
| Project | a Ren-owned scratch copy (`ren-scratch-retrieval-bench`) of the captain's `lucie/geo-podcast` project - search index, clip catalog and temporal index only |
| The captain's project | NOT written to. No Resolve project, no timeline, no reel was touched; the benchmark needs none |
| Incumbent | `ren eval-search --families editorial` - the shipped lanes: `FootageIndex.search_report` for the text lane, `FrameIndex.search_ranges` for the frame lane |
| Candidate | `library.tools.retrieval.search.search_footage` - the plan-driven search PR 2907 landed |
| Comparison | `library.tools.retrieval.incumbent.compare` (VI-009) |
| Acquisition ledger | `docs/benchmarks/acquisition_producer_ledger_bench.py` -> `library.tools.acquisition_benchmark.evaluate` |

The scratch copy carries the search index as it was built (2026-10-07) and
the measured source memory that both PRs leave untouched
(`footage_query.py`, `footage_frames.py`).  The merged PRs changed the
SEARCH, not the measurements, so the same index measures both.

### Commands

```sh
P=~/Documents/content_stuff/video_projects/ren-scratch-retrieval-bench
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_set.json --families editorial \
    --out-json incumbent-merged.json
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_heldout_set.json \
    --families editorial --out-json incumbent-heldout.json
bin/vep docs/benchmarks/editorial_retrieval_candidate_bench.py \
    --project "$P" --set library/tools/editorial_retrieval_set.json \
    --out-json candidate-merged.json --incumbent incumbent-merged.json \
    --comparison-out comparison-merged.json
bin/vep docs/benchmarks/acquisition_producer_ledger_bench.py \
    --project "$P" \
    --editorial-set library/tools/editorial_retrieval_set.json \
    --heldout-set library/tools/editorial_retrieval_heldout_set.json \
    --out-ledger acquisition-ledger.json --out-report acquisition-report.json
```

**The numbers below are measured with the four defect fixes applied.**  Without
them the merged benchmark does not finish at all - see "The defects this run
found".

## The headline numbers, with their denominators

### The merged editorial set - 16 queries (12 present, 4 absent)

| Measure | Value | Denominator |
|---|---|---|
| Macro recall | **0.4630** | 12 present queries (mean of the per-query moment recall) |
| Macro precision | **0.4182** | the same 12 present queries |
| Abstention | **2 / 4** | absent queries: E12 and E13 (text lane) abstained; E14 and E15 (frame lane) returned 5 candidates each and did not |
| Requirement recall | **1.0000** | 16 / 16 queries: the decomposition names every declared modality, and adds no unambiguous extra |
| False-positive windows | **41** | 16 queries |
| False-positive seconds | **385.6 s** | 16 queries |
| Wrong seconds selected | **93.01 s** | 16 queries - the editor takes window 1, so this is the cost of one wrong choice per query |
| Missing seconds | **1155.2 s** | of 1209.74 ground-truth seconds (coverage fraction 0.0451) |
| Queries whose first window shadows the wanted moment | 8 | 16 queries |

Two lanes, two calibrations (VI-004), reported separately because they are
different spaces:

| Lane | ECE | Verdict | Samples | Abstention cutoff | Balanced accuracy |
|---|---|---|---|---|---|
| text (dense cosine) | 0.2221 | poorly calibrated: the score overstates its confidence | 39 windows (21 positive, 18 negative) | 0.6286 | 0.7897 |
| visual (frame cosine) | 0.0887 | calibrated: the score reads as a probability | 25 windows (2 positive) | 0.22195 | 0.9565 |

### The held-out editorial set - 15 queries (6 present, 3 absent, 1 refused, 5 unlabelled)

| Measure | Value | Denominator |
|---|---|---|
| Macro recall | **0.1095** | 6 present queries (H01-H06) |
| Macro precision | **0.1333** | the same 6 |
| Abstention | **1 / 3** | absent queries: H09 (text lane) abstained; H07 and H08 (frame lane) did not |
| Correct refusals | **1 / 1** | H10, the action query the frame lane refuses by design |
| Requirement recall | **1.0000** | 15 / 15 queries |
| False-positive windows | **36** | the 9 scored queries |
| False-positive seconds | **420.42 s** | the 9 scored queries |
| Wrong seconds selected | **80.08 s** | the 9 scored queries |
| Missing seconds | **1640.57 s** | of 1796.08 ground-truth seconds (coverage fraction 0.0866) |

**The frame lane cannot abstain, and that is the shape of the abstention
numbers.**  It has no floor: every absent picture query (E14, E15, H07, H08)
returns its top-5 candidates, so 0 of the 4 frame-lane absent queries
abstained, while 3 of the 3 text-lane absent queries did (E12, E13, H09).
An absent picture is therefore reported as "found", and the editor is handed
five wrong windows.

### Per query (incumbent; the candidate column is in the next section)

| id | kind | windows | recall | precision | wrong s |
|---|---|---|---|---|---|
| E01 | present | 5 | 0.0 | 0.0 | 11.52 |
| E02 | present | 5 | 0.6667 | 0.6 | 0 |
| E03 | present | 5 | 1.0 | 0.6 | 3.75 |
| E04 | present | 5 | 1.0 | 0.2 | 4.15 |
| E05 | present | 4 | 0.0 | 0.0 | 3.52 |
| E06 | present | 5 | 1.0 | 0.8 | 0 |
| E07 | present | 5 | 1.0 | 1.0 | 0 |
| E08 | present | 5 | 0.75 | 1.0 | 0 |
| E09 | present | 5 | 0.1395 | 0.4 | 0 |
| E10 | present | 5 | 0.0 | 0.0 | 30.03 |
| E11 | present | 5 | 0.0 | 0.0 | 0 |
| E12 | absent | 0 | - | - | 0 |
| E13 | absent | 0 | - | - | 0 |
| E14 | absent | 5 | - | - | 10.01 |
| E15 | absent | 5 | - | - | 30.03 |
| E16 | present | 0 | 0.0 | - | 0 |
| H01 | present | 5 | 0.3256 | 0.4 | 0 |
| H02 | present | 5 | 0.093 | 0.2 | 0 |
| H03 | present | 5 | 0.0 | 0.0 | 0 |
| H04 | present | 5 | 0.2381 | 0.2 | 0 |
| H05 | present | 5 | 0.0 | 0.0 | 30.03 |
| H06 | present | 5 | 0.0 | 0.0 | 0 |
| H07 | absent | 5 | - | - | 10.01 |
| H08 | absent | 5 | - | - | 40.04 |
| H09 | absent | 0 | - | - | 0 |
| H10 | refused | 0 | - | - | - |

E01 is worth one line: the label is the twenty-percent line at 3562.1-3610.9
on LC4932, and the search's best window starts at 3618.31 - **7.4 s late**.
The nearest window is not a hit, and the harness says so rather than
rounding it in.

## The candidate against the incumbent (VI-009)

`library.tools.retrieval.incumbent.compare`, on the same queries, the same
corpus, the same scoring.

**Merged set (16 queries)**

| | incumbent | candidate | delta |
|---|---|---|---|
| Macro recall | 0.4630 | 0.4630 | **0.0** |
| Macro precision | 0.4182 | 0.3083 | **-0.1099** |
| Wrong seconds selected | 93.01 | 123.04 | **+30.03** |
| Missing seconds | 1155.2 | 1155.2 | 0.0 |
| False-positive windows | 41 | 56 | +15 |
| False-positive seconds | 385.6 | 1016.23 | +630.63 |

**Verdict: `not_decided`** - "the two halves of retrieval disagree, and
weighing a wrong window against a missed moment is the edit policy's
decision".  The candidate finds exactly as much as the incumbent and returns
more wrong picture, so it is not adopted, and the harness says that instead
of inventing a total.

**Held-out set (15 queries)**: every delta is 0.0 - recall 0.1095, precision
0.1333, wrong seconds 80.08, missing seconds 1640.57.
**Verdict: `indistinguishable`** - and a tie is not a reason to change
anything, so the incumbent stays.

**Where the candidate differs, and why.**  Only three of the sixteen queries
move, and every movement is the plan naming a second lane:

* **E06 and E07** - "the comparison of a ten person agency against a company
  fifty times its size" and "the question about which CRM to use for a ten
  person law firm".  The word *person* is a person cue, so the plan carries an
  AMBIGUOUS visual requirement beside the textual one and searches both
  lanes.  The candidate returns 10 windows where the incumbent returned 5;
  the 5 extra windows are all wrong (precision 0.8 -> 0.4 and 1.0 -> 0.5).
* **E16** - "the twenty percent line while the laptop is open on the desk".
  The text lane abstained (the incumbent answered with 0 windows and a recall
  of 0), the plan named the picture lane, and the candidate answered with 5
  windows - all of them wrong, and 30.03 wrong seconds the incumbent did not
  spend.

**The plan bought nothing on this set.**  Recall is identical to four
decimals on both sets, and the only thing the merged plan changed on the
merged set was the number of wrong windows.  Its own dedup (VI-007) did
remove one duplicate window on E05 (4 windows, not 5), which is the only
place the candidate returned FEWER windows than the incumbent.

**The always-on text lane is not the cost.**  The candidate's text lane runs
on every query, whether the plan names a textual requirement or not; measured
here it abstained on every picture-only query, so it added no windows.  A
second candidate, with the text lane's rows dropped when the plan does not
name them, scored EXACTLY the same numbers (the raw comparison JSONs for both
are in the artefacts below).  That question stays open until a picture query
whose words also match something is measured.

## The categories still awaiting labels

The held-out set covers the five categories the punch list names.  Each one
has ONE entry whose labels do not exist, and the scorer refuses to score them
rather than counting them as misses:

| Category | Held-out entry | Query | State |
|---|---|---|---|
| action | H11 | "the moment she opens the box" | `unlabelled` - the annotation record the custodian owes |
| object_state | H12 | "the cup that has been emptied" | `unlabelled` |
| identity | H13 | "the shot where Akshita is speaking and Craig is off camera" | `unlabelled` |
| location | H14 | "the moment the scene changes to a different room" | `unlabelled` |
| absent_event | H15 | "the moment where nobody speaks for a full minute" | `unlabelled` |

5 of the 15 held-out entries are therefore unscored, and the denominators
above exclude them.  The corpus manifest
(`docs/benchmarks/2026-10-15-editorial-eval-corpus.json`) records
`annotation.state = not_started` and `access = not_created` for EVERY source
in both collections, so no label payload exists to score them with.

## The acquisition benchmark producer ledger (the PR 2928 follow-up)

`docs/benchmarks/acquisition_producer_ledger_bench.py` produces the frozen
ledger PR 2928 named as the live evidence it owed, and
`library.tools.acquisition_benchmark` scores it.

**The ledger**: 21 tasks and 60 runs.  A task is one hand-marked moment in
one source; the gold is the query-relevant evidence inside that moment; the
prediction is the query-relevant evidence inside the windows the policy
ACQUIRED.  The splits come from the corpus manifest, so the held-out tasks
are held out by leakage group.

| Split | Tasks | What |
|---|---|---|
| training | 12 | E01, E02, E04, E05, E06, E07, E16 on LC4932 / LCATL0013 |
| calibration | 6 | E03 and E08 on LC4931 / LCATL0012, plus the two absent-event abstention tasks E12, E13 |
| held_out | 3 | E08 on LC4930 / LCATL0011, plus the absent-event abstention task H09 |

Three policies acquire the windows over the same source: `fixed_dense` (every
10 s window), `fixed_sparse` (every 60 s window), and `adaptive_candidate`
(the NEW policy: `plan_adaptive_frames` with the measured speech boundaries
as the edit-critical events and the shipped search's candidates as the
query-importance signal).

**Calibration (4 paired tasks, the speech lane)**

| Policy | Mean utility | Mean cost (s) | Miss rate |
|---|---|---|---|
| `fixed_dense` | 0.1952 | 5.1e-05 | 0.0 |
| `fixed_sparse` | 0.1000 | 1.6e-05 | 0.75 |
| `adaptive_candidate` | **0.3591** | **2.6e-05** | 0.0 |

The adaptive policy is non-inferior in utility and cheaper than the better
baseline, so it is fitted (config `default`).

**Held out (2 paired tasks, reported once)**

| Policy | Mean utility | Mean cost (s) | Miss rate |
|---|---|---|---|
| `fixed_dense` | 0.3600 | 3.3e-05 | 0.0 |
| `fixed_sparse` | 0.2778 | 1.2e-05 | 0.0 |
| `adaptive` (fitted) | **0.6429** | **2.1e-05** | 0.0 |

**The benchmark's own verdict: `failure: fewer than three producer types
calibrated`.**  The quality/cost acceptance PASSES on the one lane that has
labels (`held_out_quality_noninferior_at_lower_cost: true`); the overall
verdict fails because only ONE of the five producer lanes (speech) has any
labelled task.  The other four - **semantics, objects, ocr, motion** - await
the annotation records above, and the benchmark reports them as
`unmeasured` rather than as zero.

**What the cost is.**  Every producer on this corpus is already migrated into
the source memory, so the cost a policy pays here is the work of the
acquisition itself: computing the schedule and reading the evidence it
covers.  It is a real, measured, policy-dependent number (the median of five
timings per run) - and it is NOT what a cold producer would cost.  Every
ledger row says so in `cost_basis`, and `budget_seconds` carries the
producer catalog's MEASURED M4 window cost (90 s per 10 s window) for the
windows acquired, so the two readings can be told apart.  On this corpus's
SHORT sources the adaptive schedule's 48-frame budget is denser than a 10 s
fixed grid, so the budget reading and the measured reading do not agree; on
the long sources (the training split) the adaptive schedule acquires 48
windows against the dense grid's 494.

## The defects this run found

Four, each with its own PR, each with the measurement that found it.

1. **The merged benchmark crashes on every frame-lane query.**
   `library/tools/retrieval_eval._editorial_rows` read the frame report's
   `results` - the individual STILLS, which carry a `t` and no `start`/`end` -
   instead of its `ranges`, the merged intervals.  The scorer then compared
   `None` with a number and died:
   `TypeError: '<' not supported between instances of 'float' and 'NoneType'`
   at `retrieval_eval.py:109`, after 10 of the 16 merged queries.  The merged
   benchmark (PR 2942) could not complete the four picture queries at all.
2. **A noun refuses a picture query.**  The frame lane's action gate matched
   a bare "point", so two of the sixteen committed editorial queries were
   refused for a word that meant something else: "to sit under the pricing
   point" (E09) and "GEO just SEO two point oh" (E08).  E09 lost every
   window it could have returned.  The gesture readings still refuse.
3. **The plan-driven search drops the frame lane's refusal.**
   `library.tools.retrieval.search` read the frame lane's report but never
   its `refused`, so a lane that REFUSED read as a lane that ran and found
   nothing - and the requirement was reported as `not_observed`, a claim
   that the lane looked and saw nothing.  It did not look at all.  This is
   VI-005's exact subject, inside the module that was written to answer it.
4. **The acquisition benchmark decides "cheaper" by rounding.**
   `_aggregate` rounds the mean cost to four decimals, so a candidate
   measured at 54 us and a baseline measured at 106 us both round to 0.0001
   and the comparison the acceptance criterion exists to make becomes
   undecidable.  On the first ledger built for this report (single-shot
   timings) no configuration fitted at all because of it.

## What this run does NOT claim

* **No new labels.**  Every ground truth is the committed editorial set's,
  carried unchanged.  The five unlabelled categories are unlabelled.
* **The frame lane cannot abstain** (0 / 4 absent picture queries), so the
  abstention numbers are a property of the lane, not of the queries.
* **The acquisition costs are measured on cached evidence.**  They are the
  acquisition's own work, not a cold producer's inference.
* **One corpus, one machine, one index build** (2026-10-07).  The numbers
  are a baseline to compare against, not a general claim.
* **Five tests in `tests/unit/picture/test_footage_query.py` fail on the base
  commit** (`test_person_filter_*`, `test_sound_search_*`,
  `test_search_json_sound_lane_shares_the_hit_shape`).  They are unrelated to
  this work and were failing before it.

## The artefacts

| File | What |
|---|---|
| `2026-10-20-editorial-retrieval-incumbent-merged.json` | the incumbent's full result, 16 queries |
| `2026-10-20-editorial-retrieval-incumbent-heldout.json` | the incumbent's full result, 15 queries |
| `2026-10-20-editorial-retrieval-candidate-merged.json` | the candidate's full result (shipped and plan-restricted), 16 queries |
| `2026-10-20-editorial-retrieval-candidate-heldout.json` | the candidate's full result, 15 queries |
| `2026-10-20-editorial-retrieval-comparison-merged.json` | the incumbent comparison, both candidates |
| `2026-10-20-editorial-retrieval-comparison-heldout.json` | the incumbent comparison, both candidates |
| `2026-10-20-acquisition-ledger.json` | the frozen producer ledger (21 tasks, 60 runs) |
| `2026-10-20-acquisition-report.json` | `library.tools.acquisition_benchmark`'s own report for it |
