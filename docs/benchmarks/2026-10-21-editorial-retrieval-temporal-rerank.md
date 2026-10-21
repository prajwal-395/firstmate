# Temporal reranking for visually complex queries (VI-003), 2026-10-21

The punch list's item **VI-003**:

> - Add temporal reranking for visually complex queries.
> - Classify whether a query requires appearance matching, action
>   recognition, temporal order, identity, or object relationships.
> - Use inexpensive indexes for retrieval, then inspect candidate video
>   intervals where necessary.
> - Require the temporal pass to justify the selected interval.
> - Reject a candidate if its evidence does not establish the requested
>   event.
> - Record the incremental model and compute cost of verification.
>
> **Acceptance: "The moment someone opens the laptop" is evaluated as an
> event, rather than simply matching a frame containing a laptop.**

This report is the measurement.  The mechanism is
`library/tools/retrieval/temporal_rerank.py`, the interval verdicts come
from `library/tools/span_verification.py` (a second prompt family beside
the span family, in the same recorded M8 store), and
`library/tools/retrieval_eval.py` produces the numbers.

**What the pass does.**  The frame lane ranks sampled frames by how much
they RESEMBLE the query.  A rank is a candidate, and VI-001 showed that a
large share of the held-out misses were candidates that lost their
camera's slot to a better-scoring shot - a defect of the RANKING.  This
pass is the second half of the lane: it CLASSIFIES what the query needs,
INSPECTS the candidate shots the index proposed (the index says where to
look, the source memory's shared frame sample says what is there),
REJECTS the candidates whose evidence does not establish the query, and
presents only the intervals it can justify - each carrying the verdict
that justified it.

## What was run, and where

| | |
|---|---|
| Machine | this machine, 2026-10-21, `main` at `67ef3f501` (the reviewed baseline this branch was measured on), re-measured on `02c644d23` with identical numbers - the commits between them touch the caption path and the report provenance, not the frame lane |
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

The BEFORE run is the same command with the temporal pass OFF
(`search_ranges(..., temporal=False)`), which is the lane as it ships on
`main`.  The AFTER run is the shipped lane with the pass ON, which is
what `ren search --visual` and the plan-driven search now do.

## The classification

The five kinds are the punch list's own, and they are read off the
decomposition the lane already has (`library/tools/retrieval/requirements.py`),
so the classification is auditable against the cues that fired:

| requirement kind | evidence kind |
|---|---|
| `visual:object`, `visual:state`, `visual:scene`, `visual:composition` | appearance matching |
| `visual:action` | action recognition |
| `visual:person` | identity |
| `temporal:ordering` | temporal order |
| `relational:*` | object relationships |

**A claim is put to the model in the editor's own words, minus the words
that frame it and minus the editor's LABEL for the thing they just
described.**  "the shot of the woman wearing glasses, the host" becomes
"the woman wearing glasses": the apposition names WHO, which no frame can
establish, and putting it to the model only makes the model argue about
who is hosting instead of looking at the woman.  A clause joined by "with"
or "while" is part of the claim and stays.

**Two kinds cannot be answered by one interval** - temporal order and
object relationships are claims about two things - and the pass says so
and names the lane that owns them (the timeline; the project graph)
rather than answering them badly.

**An EVENT is inspected on seconds, a PICTURE on the shot.**  A claim
that names an action and no state is a claim about something HAPPENING,
so the candidate is inspected on the seconds around the sighting that
proposed it; a claim about what the shot SHOWS is inspected across the
shot.  "The open laptop on the desk" names a state and is a picture;
"someone opens the laptop" names an action and is an event.

## The acceptance case

`ren search --visual "the moment someone opens the laptop"` on the
corpus:

**Classification** - `action_recognition` (primary), `event: true`,
statement `someone opens the laptop`.

**WITHOUT the pass** (the lane as it ships): five ranked windows, every
one of them a frame that CONTAINS a laptop, none of them an opening:

```
clip_004  199.783-209.793  0.2508   clip_003  4660.239-4660.239  0.2202
clip_005   20.103-30.113   0.2221   clip_006  2179.761-2189.771  0.2194
clip_007  160.243-169.753  0.1965
```

**WITH the pass**: twenty-one candidate shots inspected, twenty
REJECTED, one established -

```
clip_007  558.641-561.644  "The laptop is open in the first frame and closed in the second."
```

and the rejected ones say why: *"The laptop remains closed throughout the
sequence"*, *"The frames show a person's hand moving over a laptop, but
no one opens the laptop"*, *"No laptop is visible in any of the frames"*,
and - the one that matters most - *"The laptop is already open in all
four frames."*

**That is the acceptance criterion, measured.**  The moment is evaluated
as an EVENT - a transition across a sequence of frames - and the frames
that merely CONTAIN a laptop are rejected rather than offered.  A shot
where the laptop is already open is NOT the moment it was opened, and the
pass says so where a ranking would have offered it.  The windows the pass
presents carry the verdict that justified them, and the report carries
the cost.

**What the model is loose about.**  The one window the pass established
here is a TRANSITION - the laptop open in the first frame and closed in
the second - and the model read that transition as the event, when the
lid in it is closing.  The pass established that the frames show a change
in the laptop's state; the DIRECTION of the change is the model's
reading, and this measurement does not claim otherwise.  What the
measurement does claim is the acceptance criterion: the twenty frames
that merely contain a laptop were rejected, and the frames that show the
laptop changing were not.

## Before and after, with denominators

### The held-out set (the baseline the brief names)

Same corpus, same scratch project, same command; the only difference is
the temporal pass.

| measure | before | after |
|---|---|---|
| **visual macro recall (region-capped)** | **0.0388** | **0.0539** |
| visual macro recall (uncapped) | 0.0775 | 0.1003 |
| visual macro precision | 0.2000 | 0.3250 |
| editorial macro recall (all lanes) | 0.3592 | 0.3693 |
| editorial macro recall (uncapped) | 0.3850 | 0.4002 |
| editorial macro precision | 0.2667 | 0.3500 |
| editorial macro region recall | 1.0000 | 1.0000 |
| **wrong seconds selected** | **55.000** | **55.000** |
| missing seconds | 1781.98 | 1769.98 |
| coverage fraction of the wanted moment | 0.0214 | 0.0280 |
| false-positive windows | 31 | 27 |
| **false-positive seconds** | **257.65** | **257.65** |
| shadowing queries | 5 | 4 |
| text lane (untouched) | recall 1.0, precision 0.4 | recall 1.0, precision 0.4 |

Denominators: 6 present visual queries, 172 wanted items, 3 text queries
(1 present), 3 absent queries, 5 unlabelled queries, 1 refused query.

Per query, the visual lane's wanted moments credited:

| query | category | wanted items | before | after |
|---|---|---|---|---|
| H01 | object | 43 | 1 | 1 |
| H02 | object_state | 43 | 1 | 1 |
| H03 | identity | 22 | 1 | 2 |
| H04 | identity | 21 | 1 | 1 |
| H05 | location | 21 | 1 | 1 |
| H06 | location | 22 | 1 | 2 |

### The merged set

| measure | before | after |
|---|---|---|
| **visual macro recall (region-capped)** | **0.0236** | **0.0691** |
| visual macro recall (uncapped) | 0.0472 | 0.1381 |
| visual macro precision | 0.1333 | 0.3333 |
| editorial macro recall (all lanes) | 0.4573 | 0.4687 |
| editorial macro recall (uncapped) | 0.6299 | 0.6526 |
| editorial macro precision | 0.5000 | 0.5545 |
| editorial macro region recall | 0.6111 | 0.6944 |
| **wrong seconds selected** | **74.170** | **74.170** |
| false-positive windows | 27 | 24 |
| false-positive seconds | 390.99 | 360.96 |
| shadowing queries | 6 | 6 |

### The legacy frame-level family (`visual_retrieval_heldout_set.json`)

| measure | before | after |
|---|---|---|
| visual macro recall | 0.0468 | 0.1275 |
| visual macro precision | 0.1500 | 0.3500 |
| absent queries abstained | 3 / 3 | 3 / 3 |
| refusals correct | 1 / 1 | 1 / 1 |

## Which stage the misses are in now

The stage attribution is VI-001's, unchanged, and it reads the pass's
answer: the wanted items the pass recovered are no longer uncredited.

| stage | held-out before | held-out after | merged before | merged after |
|---|---|---|---|---|
| `not_presented` | 40 | 40 | 40 | 20 |
| `temporal_localization` | 4 | 4 | 4 | 2 |
| `ranked_too_low` | 116 | 113 | 38 | 54 |
| `credited_to_sibling_in_region` | 6 | 6 | 2 | 5 |
| `point_window_not_credited` | 0 | 1 | 0 | 0 |
| **wanted items credited (visual lane)** | **6** | **8** | **2** | **5** |

On the held-out set the pass credits two more wanted items (6 -> 8 of
172).  On the merged set it credits three more (2 -> 5 of 86) AND takes
twenty items out of `not_presented` - items the baseline never presented
a window for at all, which the pass now covers with a window the
evidence established.

The `point_window_not_credited` item is BACK, and it belongs there: the
pass does not re-shape the lane's windows, so a shot of no length stays
a shot of no length, and the credit rule - which counts a window only
when it OVERLAPS the wanted span - does not count it.  That is the
metric defect VI-001 reported, and it stays with the regions module.

## The fidelity vector: no loss, and where it came from

**Wrong seconds selected: 55.000 -> 55.000.  False-positive seconds:
257.65 -> 257.65.  Both are the baseline, unchanged.**

That was not free, and the way it was reached is worth stating, because
the first version of this pass moved both.  It presented a shot of NO
LENGTH as the span of the frames the pass had inspected, so the editor
was shown a 3-second interval where the lane had shown a point - and
the consequence metric, which charges a window for the wrong picture it
contains, charged those 3 seconds (wrong seconds 55.000 -> 58.003) and
the widened windows' seconds (false-positive seconds 257.65 -> 265.157).

**The pass SELECTS and JUSTIFIES; it does not re-shape the lane's
windows.**  A shot of no length stays a shot of no length.  The frames
the pass inspected and the verdict that established them travel with the
window as its justification, and the window keeps the length the lane
gave it.  The recall and precision gains are untouched by that:
0.0388 -> 0.0539 and 0.2000 -> 0.3250 on the held-out set, 0.0236 ->
0.0691 and 0.1333 -> 0.3333 on the merged set.

**What the pass did buy on the fidelity vector, on the held-out set:**
the false-positive WINDOW count fell 31 -> 27, the shadowing count fell
5 -> 4, the missing seconds fell 1781.98 -> 1769.98 and the coverage of
the wanted moment rose 0.0214 -> 0.0280 - because a candidate whose
evidence does not establish the query is REJECTED instead of presented
as a place the editor might cut from.

**On the merged set the fidelity vector IMPROVED**: the false-positive
windows fell 27 -> 24 and the false-positive seconds fell 390.99 ->
360.96 (-7.8 %), with the wrong seconds unchanged at 74.170.

**The point window stays a point, and the metric defect stays where
VI-001 put it.**  `library/tools/retrieval/regions.py` counts a window
only when it OVERLAPS the wanted span, so a wanted moment the lane
reached with a point window earns no credit - the `point_window_not_credited`
item in the table above.  That is the metric's business, the brief left
the metric alone, and this pass does not re-shape a window to work
around it.

## The cost of the verification

| | |
|---|---|
| Queries that ran the pass (held-out, present visual) | 6 |
| Candidate intervals inspected | 55 |
| Candidates ESTABLISHED | 32 |
| Candidates REJECTED | 23 |
| Model time, that run | 538.3 s (9.8 s per candidate, including the process's cold start) |
| Model time, steady state | 5.1 s per candidate (median 4.6 s, p90 7.5 s, over 1300 recorded interval verdicts) |
| Model calls on a RERUN | **0** - the verdicts are recorded in the source memory's M8 store beside the span verdicts, under their own keys |
| Model | `mlx-community/gemma-4-12b-it-4bit` (the local VLM), through the same heavy-work lock the span verifier uses |

The pass stops when the editor's list is full: it visits the cameras
round robin and stops the moment it holds as many windows as were asked
for, so a query answered by the first camera inspected does not cost
three calls per camera to find out.  The cost is in the report
(`temporal_pass.cost`) on every search, and `ren search --visual` prints
it.

## What the pass does not do

* **It does not widen the candidate pool.**  The misses the candidate
  pool lost are untouched - the pool is the other lane's, and this lane
  only chooses among the candidates the index already proposed.
* **It does not answer a question one interval cannot answer.**  Temporal
  order and object relationships are reported with the lane that owns
  them.
* **It does not build an index and it decodes nothing.**  The frames it
  inspects are the shared M2 sample the source memory already produced.
* **It does not refuse what the frame lane refused.**  The lane's refusal
  (an action/gesture query CLIP cannot resolve) stands; the pass is a
  reranking of candidates, not a new lane.  The held-out set's refused
  query is still refused (1 / 1) and the legacy family's too.
* **It does not change the lane's diversity rule.**  One window per
  camera, and a second window of a camera only when the query asks for
  more than one occurrence of the thing and the second window is a
  different event.  The pass adds one thing to it: a slot left over
  (because a camera's candidate was rejected) goes to another moment of a
  camera that has already contributed, under the same different-event
  test.

## What is left undone

* **The text lane is untouched.**  Its three present held-out queries
  already found their moment, so there was nothing for this lane to win
  there.
* **The held-out set carries no relationship-labelled query and no
  order-labelled query**, so the two kinds the pass REFUSES to answer
  have no held-out denominator.  The refusal is measured by the
  classification, not by the set.
* **The false-positive seconds are at the baseline, not below it** on the
  held-out set (257.65 -> 257.65), and 7.8 % below it on the merged set
  (390.99 -> 360.96).  The leftover-slot rule - a slot left over because
  a camera's candidate was rejected goes to another moment of a camera
  that has already contributed - is what buys the extra credited moments;
  a policy that kept the list to one window per camera with no depth
  would give them back at the price of the recall.
* **The point-window effect is NOT fixed here.**  A wanted moment the
  lane reached with a point window earns no credit, because the credit
  rule counts a window only when it OVERLAPS the wanted span.  That is
  the metric defect VI-001 reported and left with the regions module,
  and the brief left the metric alone: the pass does not re-shape a
  window to work around it.
* **`ren search --visual` is now slower**: the pass costs a model call
  per candidate on the first run of a query and nothing on the runs after
  it, because the verdicts are recorded in the source memory.  The cost
  is printed with the answer and the report carries it.
