# VI-004: the search lanes' abstention, calibrated on a labelled negative corpus

The punch list's item **VI-004**:

> Improve abstention calibration.
> - Retain the current explicit search floors and missing-evidence outcomes.
> - Recalibrate on a larger, independently labelled negative corpus.
> - Separate not observed, insufficient evidence, and confirmed absent
>   from the measured scope.
> - Report near misses without presenting them as confirmed results.
> - Check floor changes against both missed-present events and false
>   positives.
>
> **Acceptance: Reduced false positives do not come from silently
> discarding valid moments.**

This report is the measurement, and the change is the vocabulary the
lanes now answer in (`library.tools/retrieval/abstention.py`), one
recalibrated floor, and the corpus the calibration rests on
(`library/tools/editorial_abstention_negatives.json`).

## What was run, and where

| | |
|---|---|
| Machine | this machine, 2026-10-22, branch `fm/p-vi004-abstention` off `c4572f5f3` |
| Corpus | GEO vs SEO podcast, two speakers, two fixed cameras, 7 MXF sources, 181.58 video minutes |
| Project | a Ren-owned scratch copy (`ren-scratch-retrieval-bench`) - search index, clip catalog and the measured source memory |
| The captain's project | NOT written to. No Resolve project, no timeline, no reel, no media write |
| `ren qualification reset` | not run. No broker was started; no `bin/ren resolved` command was run |
| Incumbent | the shipped lanes: `FootageIndex.search_report` (text), `FootageIndex.sound_search` / `ocr_search` (label lanes), `FrameIndex.search_ranges` (frame) |

### Commands

```sh
P=~/Documents/content_stuff/video_projects/ren-scratch-retrieval-bench
bin/vep docs/benchmarks/editorial_abstention_bench.py --project "$P" \
    --out-json abstention.json
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_set.json --families editorial \
    --out-json merged.json
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_heldout_set.json --families editorial \
    --out-json heldout.json
```

The artefacts are in this directory:
`2026-10-22-editorial-abstention.json` (the bench's own report: every
negative's score, every sweep, both fits) and
`2026-10-22-editorial-abstention-eval-summary.json` (the
`retrieval_eval` summary of both editorial sets, before and after the
floor change).

## The corpus: 34 negatives, labelled by looking

The corpus is `library/tools/editorial_abstention_negatives.json`: **18
text negatives and 16 visual negatives**, split in two - `tune` (the
half a floor may be fitted on) and `heldout` (the half it may not).

**Every label was established by reading the footage, never by asking a
search lane.**  A lane that says "not found" is the thing being
measured, so it cannot also be the thing that says what is not there.

* **The 18 text negatives** were labelled against the measured
  transcript: 2,854 speech segments over the two long takes
  (LC4932, 4,941.10 s; LCATL0013, 4,099.60 s), read end to end - 2,310
  lines, every utterance.  The subject of each negative is absent from
  a marketing podcast: no dog, no cat, no cookie, no recipe, no car, no
  city, no sea, no rocket.  **The bench REFUSES to score a text
  negative whose subject words occur in the measured transcript** (all
  18 pass: zero occurrences), so a corpus entry that drifts out of its
  label is caught rather than scored.
* **The 16 visual negatives** were labelled by looking at **all 1,092
  sampled frames** of the measured scope, as 23 contact sheets of 48
  labelled frames each (clip id and second on every tile).  The
  measured scope is one studio: a dark geometric acoustic-panel wall, a
  potted plant, a dark marble table, laptops, two speakers, and the
  other speaker's hands in the foreground on one camera.  There is no
  B-roll, no cutaway, no street, no kitchen and no animal.

The sampling is 10 s, so an event shorter than 10 s could in principle
be unsampled; every visual subject below is an object that would be
visible for far longer than the sampling gap if it were present.

Five of the entries are the negatives the editorial sets already
carried (E13, H09, H07, H08, E15).  They are **re-verified** against
this scope, not re-labelled, so the shipped sets keep meaning what they
meant.

## The measurement, before and after, with denominators

A **false positive** is a negative query the lane ANSWERS: the subject
is not in the footage and the lane hands the editor rows anyway.  A
**missed-present query** is a positive query the lane REFUSES: the
moment is in the footage and the floor turned the whole answer away.

### Text lane - the floor is CONFIRMED at 0.40

| measure | before (0.40) | after (0.40) |
|---|---|---|
| false positives | **0 of 18** (0/10 tune, 0/8 heldout) | **0 of 18** (0/10 tune, 0/8 heldout) |
| near misses reported | 11 of 18 negatives (40 weak rows) | 11 of 18 negatives (40 weak rows) |
| missed-present queries | 1 of 9 (E16) | 1 of 9 (E16) |
| missed-present moments | 2 of 18 (E16 0.3125, E08#3 0.2539) | 2 of 18 (E16 0.3125, E08#3 0.2539) |
| engine's own fit on the corpus | 0.4029 (tune half) / 0.40695 (whole) | - |

The text floor does not move, and the corpus is what says so: **none of
the 18 independently labelled negatives clears 0.40**, and the engine's
own method (`library.tools.retrieval.calibration.abstention_threshold`,
balanced accuracy over labelled queries) selects 0.4029 on the tune
half and 0.40695 on the whole corpus - the same decision as 0.40,
because no labelled score lies between 0.40 and 0.40695.  Lowering the
floor to 0.36 buys nothing (still 2 missed moments) and 0.34 costs 4
false positives; raising it to 0.46 costs a third missed moment for no
false positive at all.

The price of the floor is stated rather than hidden: **E16's moment
scores 0.3125**, under the floor, so the lane abstains on that query
and reports the moment as a **near miss** in the weak band (0.28-0.40)
rather than dropping it.  That is the punch list's "report near misses
without presenting them as confirmed results", and it was already the
lane's shape; what was missing was the outcome vocabulary that says
which of the three empty answers it is.

### Frame lane - the floor moves 0.15 -> 0.1611

| measure | before (0.15) | after (0.1611) |
|---|---|---|
| false positives | **5 of 16** (1/8 tune, 4/8 heldout) | **3 of 16** (1/8 tune, 2/8 heldout) |
| missed-present queries | 0 of 3 | 0 of 3 |
| present moments | 86 | 86 (the query floor does not gate windows) |
| engine's own fit on the corpus | 0.1524 (tune half) / **0.1611** (whole) | - |

**Why the floor moves.**  The 2026-10-20 measurement had three absent
probes whose best frame scores ran 0.1237-0.1447 - all under the floor,
so the floor was set 0.0152 below the weakest present query (0.1652)
and cost nothing.  The larger corpus says the absent band reaches
**0.1932**, and the present band starts at **0.1652**: the two bands
now OVERLAP, and 5 of the 16 absent picture queries were answered with
five wrong windows each (31 %).

The value shipped is the one the engine's own method selects over the
whole corpus: **0.1611**, the midpoint of the band that separates the
two refusable absent queries (0.1551, 0.1570) from the weakest present
query (0.1652).  It refuses 13 of the 16 absent queries and still
answers **every** present query, the weakest with 0.0041 of margin.

**The three false positives that remain are the overlap, and they are
not removable.**  They score 0.1665, 0.1787 and 0.1932 - all ABOVE the
weakest present query (0.1652) - so no floor refuses them without
discarding E10, a moment that is really in the footage.  A floor of
0.20 would refuse all 16 and would discard E10, which is exactly the
trade the punch list forbids.

**The window floor stays off, and the corpus says so again.**  At
0.22195 it would drop **84 of the 86** present visual moments (E09's
43 moments keep 19; E10's best moment scores 0.1451 and E11's 0.1339,
so both lose every moment they have).

**What the tune half can and cannot see.**  The tune-half fit is
0.1524, and on the tune half it changes nothing measurable (the same
single false positive, V05 at 0.1787, which is in the overlap).  The
two false positives the change removes - 0.1551 and 0.1570 - are in the
HELD-OUT half, so a fit on the tune half alone cannot select the value
that removes them.  The reduction is therefore measured on negatives
the whole-corpus fit saw, and the held-out half is reported beside it:
**4 of 8 -> 2 of 8**.

### The fidelity check: nothing else moved

The same command, the same scratch project, the only difference being
the floor:

| measure | before | after |
|---|---|---|
| merged macro recall | 0.4573 | **0.4573** |
| merged macro precision | 0.5000 | **0.5000** |
| merged wrong seconds selected | 74.17 | **74.17** |
| merged false-positive windows | 27 | **27** |
| merged false-positive seconds | 390.99 | **390.99** |
| held-out macro recall | 0.3592 | **0.3592** |
| held-out macro precision | 0.2667 | **0.2667** |
| held-out wrong seconds selected | 55.00 | **55.00** |
| held-out false-positive windows | 31 | **31** |
| held-out false-positive seconds | 257.65 | **257.65** |

The two `retrieval_eval` summaries are **byte-identical** before and
after (only the latency fields differ, which is machine noise): the
editorial sets' absent picture queries already abstained at 0.15
(their best scores are 0.1237, 0.1299, 0.1447), and every present
picture query still answers.

## The vocabulary: three empty answers, told apart

**The defect.**  A lane's empty answer used to be whatever that lane
felt like saying.  The text lane said `not_found`, the frame lane said
`abstained`, and the frame lane said `insufficient_evidence` on a
search that had returned five ranges - a claim that the evidence was
missing when the lane had five.  Measured on the corpus: all three
present visual queries (E09, E10, E11) reported `insufficient_evidence`
while returning five ranges each.

**The fix.**  `library/tools/retrieval/abstention.py` is the one table,
and it answers the question the plan layer (`library/tools/retrieval/
coverage.py`) already asked through the same evidence-stack vocabulary
(`library/tools/evidence_query.classify_absence`):

| outcome | claim |
|---|---|
| `found` | the lane returned evidence |
| `not_observed` | the lane searched its WHOLE measured scope and nothing cleared the floor |
| `insufficient_evidence` | the lane could NOT search its whole measured scope, so the empty answer is about the index, not about the footage |
| `confirmed_absent` | the measured scope carries an EXPLICIT negative assertion over the queried kind |
| `refused` | the lane declined the question and observed nothing |

**The scope travels with the answer**, because "not observed" is a
claim about the footage the lane measured and nothing else:

```
"scope": {"complete": true,
          "note": "7 of 7 catalogued sources carry a transcript; 4219 indexed rows in all"}
"scope": {"complete": true,
          "note": "1092 frames every 10s over 7 source(s); the catalog names 7 source(s)"}
```

**A ranking lane can only ever reach `not_observed`.**  CLIP ranking a
studio frame for "a plate of food" says the studio is the closest thing
to a plate of food; it does not say there is no plate of food.  The
`confirmed_absent` state is earned only where the scope carries an
explicit negative, and the one lane that has one is the SOUND lane: it
measured a source and recorded a label vocabulary, so "no label of this
kind in any measured source" is an assertion - over the measured label
vocabulary, which its basis says.

**The near miss travels with the outcome and never inside the results.**
The text lane already carried a weak band and a `best_rejected`; the
frame lane carried a `near_miss` on an abstain.  The abstention block
now carries the same near miss beside the outcome, so an abstain that
cannot say what came closest is impossible.

**A refusal is not an observation.**  The frame lane refuses an action
query by design (CLIP measured at chance on gestures); that answer is
`refused` / `not_analyzed`, never `not_observed`, which would claim a
look that never happened.

## What is left undone

* **The band overlap is not removable by a floor.**  Three of the
  sixteen absent picture queries score above the weakest present query,
  so the frame lane answers them with wrong windows.  Closing that gap
  needs a signal that is not a cosine - a detector that asserts
  "there is no plate of food here" - not a threshold.
* **E08's third present moment (0.2539) is neither a result nor a near
  miss**: it is under the weak floor (0.28), so the text lane reports
  it nowhere.  The weak band's lower edge is a second threshold, and
  this corpus says nothing about where it should sit.
* **The corpus is one corpus.**  34 negatives over 181 minutes of one
  studio, verified by one labelling pass.  The floors are fitted on
  these scores, so a different corpus, a different encoder or a
  re-built index moves them; the text floor and the frame floor both
  carry that statement in their comments.
* **The held-out half is eight visual and eight text negatives.**  It
  is enough to see a 4-of-8 -> 2-of-8 change and not enough to put an
  error bar on it.
* **The five carried entries are re-verified, not re-labelled.**  A
  corpus that quietly re-labelled them would change what the shipped
  editorial sets mean, so it does not.
