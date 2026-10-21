# Which stage of retrieval lost the wanted moment (VI-001), 2026-10-21

The punch list's item **VI-001**:

> Diagnose the visual retrieval recall deficit at each stage.
> - Separate extraction coverage, indexing, candidate generation, ranking,
>   temporal localization, and result presentation.
> - Determine whether a missed target was never sampled, never indexed,
>   ranked too low, or removed by the retrieval floor.
> - Break results down by objects, actions, identities, locations,
>   relationships, and object states.
> - Keep region-capped recall and wrong-seconds metrics so longer answers
>   cannot artificially improve scores.
>
> **Acceptance: every missed evaluation query has an attributable failure
> category.**

This report is the measurement.  The mechanism is
`library/tools/retrieval/miss_attribution.py`, and `library/tools/retrieval_eval.py`
produces it for every editorial query and prints the table below into the
report's `summary.stage_attribution`.

**Nothing about the search changed.**  No ranking, no sampling, no floor, no
pool size, no merge rule.  The attribution reads what the shipped lane did
and says where the wanted thing stopped.  Every headline number below is
IDENTICAL before and after the change, with the same denominators - the
region-capped recall, the uncapped recall, the precision, the excess seconds
and the whole false-positive consequence vector are byte-identical, which was
checked by running the same command on the same scratch project before and
after.

## What was run, and where

| | |
|---|---|
| Machine | this machine, 2026-10-21, `main` at `97b57197` (reviewed baseline) |
| Corpus | GEO vs SEO podcast, 7 MXF camera originals, 181.58 video minutes, one frame index of 1092 frames |
| Project | a Ren-owned scratch copy (`ren-scratch-retrieval-bench`) - search index, clip catalog and the measured source memory |
| The captain's project | NOT written to. No Resolve project, no timeline, no reel, no media write |
| `ren qualification reset` | not run. No broker was started; no `bin/ren resolved` command was run |
| Incumbent | the shipped lanes: `FrameIndex.search_ranges` (frame lane), `FootageIndex.search_report` (text lane) |

### Commands

```sh
P=~/Documents/content_stuff/video_projects/ren-scratch-retrieval-bench
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_heldout_set.json --families editorial \
    --out-json heldout.json
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_set.json --families editorial \
    --out-json merged.json
```

The attribution is inside the report: `summary.stage_attribution` carries the
table, `results[].attribution` carries the per-wanted-item detail.

## The vocabulary

One enumeration, `library.tools.retrieval.miss_attribution.STAGES`, and one
place that decides which stage a miss belongs to.  The stages are the
punchlist's own, plus the floor and the credit rule:

| stage | pipeline stage | what it means |
|---|---|---|
| `never_sampled` | extraction coverage | no extracted frame covers the wanted span - it was never decoded |
| `extraction_evidence_unavailable` | extraction coverage | the extracted sample cannot be read, so whether the frame was decoded cannot be said |
| `never_transcribed` | extraction coverage | no transcript segment covers the wanted span (text lane) |
| `never_indexed` | indexing | an extracted frame covers it and the index does not |
| `not_in_candidate_pool` | candidate generation | the index holds it and the CLIP ranking put it below the pool's cut |
| `removed_by_query_floor` | retrieval floor | the lane abstained: nothing in the pool cleared the floor |
| `removed_by_window_floor` | retrieval floor | every candidate in the span scored under the window floor, so the window was dropped |
| `ranked_too_low` | ranking | a better-scoring window on the same clip took the slot |
| `temporal_localization` | temporal localization | the window that covers it is on the sibling angle, not on this asset |
| `not_presented` | result presentation | the clip's window was cut before the editor saw it |
| `touched_within_tolerance` | credit accounting | a window touches it only inside the overlap tolerance - the metric's rule, not the search's |
| `point_window_not_credited` | credit accounting | a window of no length lies inside the wanted span and the credit rule counts only an OVERLAP |
| `credited_to_sibling_in_region` | credit accounting | a window covers it and the region's one credit went to another moment in the same region |
| `credited` | found | the capped credit rule credited it |

The last three are NOT retrieval failures, and the report says so:
`failure_kind` is `retrieval` or `credit_accounting`, and only the first
counts as a defect in the search.

## The category table - held-out set (the baseline the brief names)

**Visual-only held-out region-capped recall: 0.0312 (3.12 %), unchanged by
this work.**  6 present visual queries, 172 wanted items, 167 of them
uncredited.  Every one of the six missed queries carries a stage:

| query | category | recall | wanted items | stages of the uncredited items |
|---|---|---|---|---|
| H01 | object | 0.0233 | 43 | `not_in_candidate_pool` 25, `ranked_too_low` 16, `credited_to_sibling_in_region` 1 |
| H02 | object_state | 0.0233 | 43 | `not_in_candidate_pool` 25, `ranked_too_low` 16, `credited_to_sibling_in_region` 1 |
| H03 | identity | 0.0000 | 22 | `not_in_candidate_pool` 18, `ranked_too_low` 3, `point_window_not_credited` 1 |
| H04 | identity | 0.0476 | 21 | `ranked_too_low` 16, `not_in_candidate_pool` 3, `credited_to_sibling_in_region` 1 |
| H05 | location | 0.0476 | 21 | `ranked_too_low` 10, `not_in_candidate_pool` 9, `credited_to_sibling_in_region` 1 |
| H06 | location | 0.0455 | 22 | `not_in_candidate_pool` 19, `ranked_too_low` 1, `credited_to_sibling_in_region` 1 |

**Held-out, by category (uncredited wanted items):**

| category | queries | `not_in_candidate_pool` | `ranked_too_low` | credit accounting |
|---|---|---|---|---|
| object | 1 | 25 | 16 | 1 |
| object_state | 1 | 25 | 16 | 1 |
| identity | 2 | 21 | 19 | 2 |
| location | 2 | 28 | 11 | 2 |
| action | 0 | - | - | - |
| absent_event | 2 (absent) | - | - | - |
| **total** | **6** | **99** | **62** | **6** |

Every category the set DECLARES appears in the table, with zeroes where
nothing was attributed: `action` and `absent_event` are empty because their
queries are unlabelled, refused or absent, and an axis with no denominator
must be visible as empty rather than inferred from its absence.  The set
declares no `relationship` category, so that axis has no held-out
denominator either; the breakdown by requirement kind below is where
`relational` lives when a query names one.

**Held-out, by lane:** visual 99 / 62 / 6 (candidate generation / ranking /
credit accounting).  The text lane's three present queries (H16, H17, H18)
all found their moment, so they carry no stage.

**What the table says.**  The deficit is NOT at extraction (0
`never_sampled`), NOT at indexing (0 `never_indexed`), NOT at the floor (0
`removed_by_*`), NOT at temporal localization (0 `temporal_localization`)
and NOT at presentation (0 `not_presented`).  It is entirely in the two
places where a SCORE decides:

* **99 of 167 uncredited items (59 %) were never in the candidate pool** -
  the index holds the frame and the CLIP ranking put it below the pool's
  200-frame cut;
* **62 of 167 (37 %) were in the pool and lost their clip's single window
  slot** to a better-scoring range on the same camera;
* **6 of 167 (4 %) were found** and not counted by the credit rule.

## The category table - merged set

8 of the 16 queries missed, all 8 attributed, 89 uncredited items:

| query | lane | recall | wanted items | stages of the uncredited items |
|---|---|---|---|---|
| E01 | text | 0.0000 | 1 | `touched_within_tolerance` 1 |
| E02 | text | 0.6667 | 3 | `ranked_too_low` 1 |
| E05 | text | 0.0000 | 1 | `touched_within_tolerance` 1 |
| E08 | text | 0.7500 | 4 | `ranked_too_low` 1 |
| E09 | visual | 0.0233 | 43 | `not_in_candidate_pool` 24, `ranked_too_low` 17, `credited_to_sibling_in_region` 1 |
| E10 | visual | 0.0476 | 21 | `not_in_candidate_pool` 12, `ranked_too_low` 6, `credited_to_sibling_in_region` 2 |
| E11 | visual | 0.0000 | 22 | `not_in_candidate_pool` 22 |
| E16 | text | 0.0000 | 1 | `removed_by_query_floor` 1 |

By lane: visual 58 candidate generation / 23 ranking / 3 credit accounting;
text 2 ranking, 1 floor, 2 credit accounting.

**By requirement kind** (the decomposition's own vocabulary, which is where
`relationships` live - `relational:same_subject`, `relational:shot_relation`,
`relational:cross_clip`): the merged set's visual misses decompose into
`visual:composition` 58/23, `visual:action` 24/17, `visual:object` 46/17,
`visual:state` 24/17, `visual:person` 12/6, `visual:scene` 12/6
(candidate generation / ranking).  A wanted item is counted once for EVERY
kind its query declares, so these columns do not sum to the lane total.  The held-out set's queries decompose into
`visual:object`, `visual:state`, `visual:person` and `visual:composition`;
no held-out query names a `relational` requirement, so the relationship axis
has no held-out denominator yet - the held-out set's categories are object,
object_state, identity, location, absent_event and action.

**E16 is the floor, and it is the floor's own doing.**  The text lane
abstained: the labelled passage scores 0.2489 and the phrase-carrying
utterance 0.3125, both under the 0.40 `DENSE_SCORE_FLOOR` and the latter
under the 0.28 weak floor.  The attribution names it
`removed_by_query_floor` rather than a ranking miss, which is the
distinction the punchlist asked for.

## The findings

### 1. The deficit is a ranking deficit, and it is at two depths

59 % of the uncredited wanted items were never candidates, and 37 % were
candidates that lost their camera's slot.  Both are the CLIP score's doing
and both are fixable in the ranking, not in the extraction, the index, the
floor or the presentation.  The five other stages are EMPTY on this corpus -
which is itself the finding: the extraction, the index, the floor, the
localization and the presentation are not what is losing the held-out
moments.

### 2. Six items were FOUND and not counted - and that is a metric defect, not a search defect

`library/tools/retrieval/regions.py` `credit_windows` arbitrates a region's
one credit with a test that needs a POSITIVE overlap, while the harness's own
hit test (`retrieval_eval.overlaps`) pads the wanted span by the tolerance
and treats a window of no length inside the span as a hit.  The two tests
disagree, and the disagreement is the whole of E01, E05 and H03's single
`point_window_not_credited` item:

* **E01** - the wanted span on LC4932 is 3562.1-3610.9; the returned window
  is 3611.17-3622.57, i.e. **0.27 s late**.  The hit test pads by 1 s and
  calls it a hit (`hit: true`, `recall_uncredited` 1.0); the credit rule
  wants an overlap and gives nothing (`recall` 0.0).
* **E05** - the wanted span ends at 3043.8, the window starts at 3044.21:
  **0.41 s late**, the same disagreement.
* **H03** - the wanted span is 15.103-25.103 and the returned window is the
  single frame at 20.103: **no length at all**, inside the span, and the
  credit rule's positive-overlap test refuses it.

**This lane does not change the metric.**  The brief keeps the region-capped
recall and the wrong-seconds metrics as they are, and the metric belongs to
the regions module.  The fix is one line - arbitrate the credit with the same
padded test the hit test uses - and it is reported here for whoever owns it,
with the measurement above.  Until then the attribution names these items
`credit_accounting`, so they are never counted as a failure of the search.

### 3. The sibling angle is not what is losing the held-out moments

The clock's offsets put every held-out moment on two angles, and the
attribution checks the other angle for every wanted item
(`instances_localized_on_sibling` in the report).  It fires on 2 items of
H01 and H02 and on none of the others: the wanted moments are not being
localized on the sibling camera and missed on their own - they are not
reaching a window at all, on either camera.  The "one angle only" effect is
real for the EDITOR (the window the editor gets is one camera's view of a
moment labelled on both), and it is not what produces the 3.12 %.

## Before and after, with denominators

Same corpus, same scratch project, same command, the only difference being
the attribution code.

| measure | before | after |
|---|---|---|
| held-out visual macro recall (capped) | 0.0312 | **0.0312** |
| held-out visual macro recall (uncapped) | 0.0700 | **0.0700** |
| held-out visual macro precision | 0.2222 | **0.2222** |
| held-out macro region recall | 0.8889 | **0.8889** |
| held-out wrong seconds selected | 75.02 | **75.02** |
| held-out false-positive windows | 29 | **29** |
| held-out false-positive seconds | 267.66 | **267.66** |
| held-out shadowing queries | 5 | **5** |
| merged macro recall | 0.4573 | **0.4573** |
| merged macro precision | 0.5000 | **0.5000** |
| merged wrong seconds selected | 84.18 | **84.18** |
| merged false-positive windows | 27 | **27** |

The wrong-seconds and false-positive vectors are equal in every field, so
nothing about the answer changed - only the report about WHY the answer is
what it is.

## What is left undone

* The metric defect in finding 2 is REPORTED, not fixed: the credit rule and
  the hit test disagree about the same question, and the regions module owns
  the fix.
* The held-out set carries no `relationship`-labelled query, so the
  relationship axis of the breakdown has no held-out denominator; the
  breakdown by requirement kind reports it where the decomposition names one.
* The `visual` family of the benchmark (`library/tools/visual_retrieval_benchmark.py`)
  keeps its own coarser four-name vocabulary; `miss_attribution.COARSE_TO_STAGE`
  translates it, and unifying the two is not done.
* The five unlabelled held-out categories (H11-H15) are still unlabelled, so
  they are still reported and not scored.
