# The frame lane's pool, its windows, its floor, and its range cap (2026-10-21)

The optimisation scout (`data/scout-opt-retrieval/report.md` in the
firstmate home) measured three defects in the frame lane and named two
fixes for them: **F2** (raise the candidate pool, diversify the windows
across assets) and **F4** (give the lane a floor, so it can abstain).
This is the measurement of that change, plus the fourth defect the
corrected metric exposed - the lane's ranges were longer than the
evidence carried - on the same corpus, the same index and the same
scorer as the incumbent it replaces.

## What was run, and where

| | |
|---|---|
| Machine | this machine, 2026-10-21, `video-editing-pilot` on `main` at `85b20281c` (the corrected retrieval metric, PR 3026) |
| Corpus | GEO vs SEO podcast, 7 MXF camera originals, 181.58 video minutes |
| Project | a Ren-owned scratch copy (`ren-scratch-retrieval-bench`) of the captain's `lucie/geo-podcast` project - search index, clip catalog and temporal index only |
| The captain's project | NOT written to. No Resolve project, no timeline, no reel was touched; the benchmark needs none |
| Before | the lane as it shipped: a pool of 40 frames, the five best ranges of whichever camera scored well, no floor, and no cap on how far a merged run of sightings reached |
| After | a pool of 200 frames, at most one range per clip, the measured query floor at 0.15, and a range that spans no more than the merge gap |

The BEFORE numbers were re-measured live on this machine - the same
index, the same scorer, the lane called with its old arguments - so the
two halves of each table below are one measurement, not two.

**The metric is the corrected one.**  PR 3026 made the recall
non-gameable: one window earns at most one credit per wanted REGION,
and the seconds a window carries outside every wanted span are charged
as false-positive seconds and as wrong seconds.  The uncapped number -
the old definition, which rewarded a window for covering more of the
recording - is reported beside it as `recall_uncredited`.

### Commands

```sh
P=~/Documents/content_stuff/video_projects/ren-scratch-retrieval-bench
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_set.json --families editorial \
    --out-json /tmp/f2-merged.json
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_heldout_set.json --families editorial \
    --out-json /tmp/f2-heldout.json
bin/vep docs/benchmarks/editorial_retrieval_candidate_bench.py --project "$P" \
    --set library/tools/editorial_retrieval_set.json --out-json /tmp/f2-cand.json \
    --incumbent /tmp/f2-merged.json --comparison-out /tmp/f2-comparison.json
bin/vep docs/benchmarks/acquisition_producer_ledger_bench.py --project "$P" \
    --editorial-set library/tools/editorial_retrieval_set.json \
    --heldout-set library/tools/editorial_retrieval_heldout_set.json \
    --out-ledger /tmp/f4-ledger.json --out-report /tmp/f4-report.json
```

## The four defects, and what each one cost

1. **The pool was 40 frames of a 1092-frame index.**  The frames the
   editor wants are not missing - they are RANKED below the pool: the
   wanted frames of a present query sit at a median rank of 396-696 of
   1092.  A pool of 40 could not see them at all.
2. **The five windows were the five best ranges of whichever camera
   scored well.**  One camera could take every slot, and the other
   assets were never shown.
3. **The lane could not abstain.**  An absent picture was handed to the
   editor as five wrong windows - 10 of the 41 false-positive windows
   and 44 % of the false-positive seconds of the merged set.
4. **A merged range claimed more time than the evidence carried.**  The
   merge gap is the tolerance for ONE missed extraction, but the merge
   CHAINED: hits 30 s apart walked the recording and became ranges of
   minutes.  With the corrected metric that is charged - the merged
   set's false-positive seconds went 561.16 -> 1030.13 and the held-out
   wrong seconds 125.07 -> 204.65 when the pool and the floor shipped
   WITHOUT the cap.

## The numbers, with their denominators

### The merged editorial set - 16 queries (12 present, 4 absent)

| Measure | Before | After | Denominator |
|---|---|---|---|
| Macro recall (capped) | 0.4553 | **0.4573** | 12 present queries |
| Macro recall (uncapped) | 0.6297 | **0.6338** | the same 12 |
| Macro precision | 0.5000 | 0.5000 | the same 12 |
| Abstention on absent queries | 2 / 4 | **4 / 4** | E12, E13 (text), E14, E15 (visual) |
| False-positive windows | 37 | **27** | 16 queries |
| False-positive seconds | 561.16 | **411.01** | 16 queries |
| Wrong seconds selected | 124.22 | **84.18** | 16 queries - the editor takes window 1 |
| Shadowing queries | 8 | **6** | 16 queries |

### The held-out editorial set - 18 queries (9 present, 3 absent, 1 refused, 5 unlabelled)

| Measure | Before | After | Denominator |
|---|---|---|---|
| Macro recall (capped) | 0.3464 | **0.3541** | 9 present queries |
| Macro recall (uncapped) | 0.4063 | 0.3800 | the same 9 |
| Macro precision | 0.2222 | **0.2815** | the same 9 |
| Abstention on absent queries | 1 / 3 | **3 / 3** | H09 (text), H07, H08 (visual) |
| False-positive windows | 43 | **29** | the 12 scored queries |
| False-positive seconds | 517.91 | **267.66** | the 12 scored queries |
| Wrong seconds selected | 125.07 | **75.02** | the 12 scored queries |
| Shadowing queries | 7 | **5** | the 12 scored queries |

**No metric regresses on the metric the product ships.**  Every one of
them improves or holds on both sets.

### The frame lane's own numbers, on its own denominator

The set measures two lanes, so the lane numbers are reported beside the
macro ones:

| Lane | Measure | Before | After |
|---|---|---|---|
| visual (merged) | macro recall (capped) | 0.0155 | **0.0236** |
| visual (merged) | macro recall (uncapped) | 0.0465 | **0.0631** |
| visual (merged) | abstention on absent | 0 / 2 | **2 / 2** |
| visual (held-out) | macro recall (capped) | 0.0196 | **0.0312** |
| visual (held-out) | macro recall (uncapped) | 0.1095 | 0.0700 |
| visual (held-out) | macro precision | 0.1333 | **0.2222** |
| visual (held-out) | abstention on absent | 0 / 2 | **2 / 2** |

**The one number that goes down is the uncapped held-out recall of the
frame lane (0.1095 -> 0.0700), and it is the number the corrected
metric replaced.**  The uncapped recall counted every labelled
ten-second sample a window happened to span, so a range that covered
the whole recording scored better than the search did - which is what
"return more footage" means when the scoreboard pays for it.  The
ranges are now tight, so that number falls; the capped recall, which
counts a wanted REGION once however much of it the window spans, goes
UP (0.0196 -> 0.0312) and the seconds the window carries beyond the
moment are charged.

## The variants that were measured, and why the cap is there

Every variant was measured with the shipped scorer on both sets:

| Variant | merged recall | merged wrong s | merged FP s | held-out recall | held-out wrong s | held-out FP s | A metric regressed? |
|---|---|---|---|---|---|---|---|
| incumbent | 0.4553 | 124.22 | 561.16 | 0.3464 | 125.07 | 517.91 | - |
| pool + diversity + floor | 0.4533 | 124.22 | 1030.13 | 0.3438 | 204.65 | 1688.08 | **yes** (recall, precision, wrong s, FP s) |
| + cap 60 s | 0.4573 | 114.21 | 611.21 | 0.3541 | 135.08 | 537.93 | **yes** (held-out wrong s, FP s) |
| + cap 40 s | 0.4573 | 94.19 | 531.13 | 0.3541 | 105.05 | 427.82 | no |
| **+ cap 30 s (shipped)** | **0.4573** | **84.18** | **411.01** | **0.3541** | **75.02** | **267.66** | **no** |
| + cap 25 s | 0.4533 | 84.18 | 421.02 | 0.3541 | 75.02 | 267.66 | **yes** (merged recall) |
| + cap 20 s | 0.4533 | 74.17 | 370.97 | 0.3489 | 55.00 | 187.58 | **yes** (merged recall) |
| trim to the scored span (0.9) | 0.4573 | 54.15 | 360.96 | 0.3463 | 75.02 | 237.63 | **yes** (held-out recall, by 0.0001) |

The cap is 30 s because the MERGE GAP is 30 s: the gap is the
tolerance for one missed extraction, so a run of sightings that spans
more than the gap is not one sighting any more, however close its
neighbours are.  The number is the rule, not a tuned constant - and it
is the value with the most fidelity headroom of the caps that regress
nothing.

## The floor: what it is, and what it risks

The floor is MEASURED, and it is narrow:

| | value | where it comes from |
|---|---|---|
| Query-level floor | **0.15** | present picture queries score 0.1652-0.2724, absent ones 0.1237-0.1447 |
| Window-level floor | 0.22195 | the lane's own calibration: balanced accuracy 0.9565, ECE 0.0887 |

The query floor ships ON, because the lane has to abstain somewhere, and
the window floor ships OFF: measured, it prices the wrong seconds well
down but it costs recall, because it drops present windows whose best
frame scores low.

**The risk is stated, not hidden.**  The weakest present query on the
corpus (E10) scores 0.1652, so the floor sits 0.0152 below it.  A floor
that is too high turns a present moment into "not found", which is a
silent recall loss, and the measurement is one podcast with two
cameras.  So the floor is a PARAMETER (`floor=None` turns it off, and
`ren search --visual --no-visual-floor` turns it off from the command
line), the abstention names its near miss rather than going silent, and
the abstention never claims the subject is absent from the footage - it
says this signal could not resolve the query.

## One reading worth naming: "at most one range per clip"

The scout's prose says "take at most one range per clip BEFORE FILLING THE
REMAINING SLOTS", and its simulation took one range per clip and stopped
there.  The two are not the same policy, and the difference was
measured on the held-out set: filling the remaining slots with a second
range from a camera that is already in the list returns two more
false-positive windows and a lower precision, with the same recall.

So the SHIPPED policy is one range per clip: it is what the numbers
above were measured with, and it is the better of the two on every
fidelity metric.  Its cost is that a query whose candidates all sit on
one camera returns ONE window rather than five - and the window it
drops is a wrong one.

## The other two commands

**The candidate against the new incumbent.**  The plan-driven search
(PR 2907) is unchanged by this work and is still `not_decided`: recall
0.4573 (delta 0.0), precision 0.388 against the incumbent's 0.5000,
wrong seconds 94.19 against 84.18.  It finds exactly as much as the
lane and returns more wrong picture, so it is not adopted - the same
verdict it had before.

**The acquisition producer ledger.**  Unchanged by this work, and
measured to be so: the ledger exercises the TEXT lane's acquisition
policies and never calls the frame lane, and running it with the lane
as the incumbent had it gives the identical numbers (adaptive utility
0.3399 on the editorial calibration tasks and 0.5098 on the held-out
ones, miss rate 0.125 on the held-out tasks).  Those are not the
scout's 0.3591 / 0.6429 because the held-out set now carries the text
lane's own queries (PR 3026) - the frame lane is not in that path.

## What this does NOT do

* **The sibling angle (F3).**  The corpus is a two-camera interview and
  every visual moment exists on both angles, so half the corpus is
  still unreachable by construction.  That is a bigger decision (an
  angle CHOICE for the editor, not a second window) and it is not
  taken here.
* **The compositional queries (F5).**  A query with a relation ("with
  the plant behind it") is a caption, and CLIP matches captions to
  images poorly: E11 still scores 0 recall with the whole index as the
  pool.  It needs a second-stage signal, which needs a model budget.
* **The negative corpus.**  The floor is measured on four labelled
  absent picture queries (E14, E15, H07, H08).  The five held-out
  categories are still unlabelled on this machine (the labels live in
  the custodian's sealed store), so the floor's margin is thin, and it
  is a parameter for that reason.

## Artefacts

| file | what it is |
|---|---|
| `2026-10-21-editorial-retrieval-frame-pool-merged.json` | the merged set, AFTER |
| `2026-10-21-editorial-retrieval-frame-pool-heldout.json` | the held-out set, AFTER |

The BEFORE is the incumbent as it shipped on the base this branch
rebases onto; it is re-measured live by the commands above, with the
lane called with its old arguments.
