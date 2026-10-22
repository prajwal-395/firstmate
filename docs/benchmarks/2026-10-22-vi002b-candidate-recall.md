# The candidate generation offers a source's moments, and the evidence says which (VI-002b), 2026-10-22

The punch list's item **VI-002**:

> Improve candidate generation without replacing the existing indexes.
> - Preserve MiniLM/BM25 and CLIP as inexpensive first-stage retrieval
>   tools.
> - Retain candidate diversity across cameras and sources.
> - Allow multiple windows from a source when the query genuinely
>   requires repeated events.
> - Distinguish a candidate window from a verified moment.
> - Benchmark deeper candidate pools against their real latency and
>   false-positive cost.
>
> **Acceptance: Relevant evidence is not excluded prematurely by an
> artificially narrow retrieval pool.**

This is the measurement.  The mechanism is
`library/tools/retrieval/temporal_rerank.py` (the pass that judges the
candidate moments) and `library/tools/analysis/footage_frames.py` (the
lane that calls it), and the numbers come from
`library/tools/retrieval_eval.py` on the editorial retrieval corpus.

## Where the deficit was, measured

PR 3052 (VI-002) already gave every source a share of the FRAME pool,
and the attribution confirms it: **`not_in_candidate_pool` is 0 on both
sets** - every wanted frame is in the candidate pool, before and after
this change.  The frame pool is no longer where the deficit lives.

The deficit is one stage further on, and the attribution says which
(held-out set, visual lane, BEFORE):

| stage | count | what it means |
|---|---|---|
| `not_in_candidate_pool` | 0 | every wanted frame is a candidate |
| `not_presented` | 40 | the source's window was cut before the editor saw it |
| `ranked_too_low` | 113 | a better-scoring window on the same source took the slot |

Both of the last two are the SAME defect: the editor's five slots went
to the five best-scoring cameras, one window each, and a source whose
evidence holds the thing ACROSS ITS OWN RECORDING never got a second
look - so the moments it held were never candidates the editor could
choose between.

## The change

**A source that the evidence establishes at more than one moment may
take more than one of the editor's slots, and the cameras are served by
the share of their own recording that their established moments span.**

Three parts, all in the candidate generation:

1. **The judged candidates are DISTINCT MOMENTS of one source.**  The
   lane's ranges are slices of a passage, and two slices that close are
   one sighting - the merge gap says so.  So the pass judges the
   camera's best moment and then the moments that show the query AGAIN
   (`candidate_intervals`), instead of three adjacent slices of one
   passage.
2. **The pass judges the second moment of the cameras that hold a
   slot** (`rerank`).  That is what makes the extra slot EVIDENCE
   rather than a guess: the model is asked about the second moment, and
   the slot is taken only if it establishes the query there too.
3. **The cameras are served by the share of their own recording that
   their established moments span** (`select`).  A camera that shows
   the thing across most of its take holds the passage the editor wants
   to scrub; a camera that shows it once in an hour of take does not.
   The windows are still presented best-first, so the editor's first
   pick is unchanged.

The indexes are untouched: the frame pool, the CLIP embeddings and the
MiniLM/BM25 text index are all read as they were.

## Before and after, with denominators

**The held-out set** - 18 editorial queries, 6 of them present visual,
172 wanted items; the frame lane's own denominator.  Same corpus, same
index, same scorer, same machine.

| measure | before | after |
|---|---|---|
| **visual macro recall (region-capped)** | **0.0539** | **0.0696** |
| visual macro recall (uncapped) | 0.1003 | 0.1317 |
| visual macro precision | 0.3250 | 0.4667 |
| editorial macro region recall | 1.0000 | 1.0000 |
| absent queries abstained | 2 / 2 | 2 / 2 |
| **false-positive windows** | **20** | **16** |
| **false-positive seconds** | **160.16** | **130.13** |
| wrong seconds selected | 10.01 | 10.01 |
| missing seconds | 1773.01 | 1773.01 |
| coverage fraction | 0.0264 | 0.0264 |
| shadowing queries | 2 | 2 |

**No fidelity measure regresses.**  The recall rises by 29 %, the
precision by 44 %, and the false-positive windows and seconds FALL: the
windows the answer offers are more of the thing the editor asked for
and less of what nobody did.

Per query, the visual lane's wanted moments credited (the wanted items
and the region recall beside them):

| query | category | wanted | before | after |
|---|---|---|---|---|
| H01 | object | 43 | 1 | **2** |
| H02 | object_state | 43 | 1 | **2** |
| H03 | identity | 22 | 2 | 2 |
| H04 | identity | 21 | 1 | **2** |
| H05 | location | 21 | 1 | 1 |
| H06 | location | 22 | 2 | 2 |

**No query loses a wanted moment.**  H01, H02 and H04 gain the second
moment of the source that holds the thing across its recording; the rest
are unmoved.

The stage attribution moves with it: `not_presented` 40 -> 36 and
`ranked_too_low` 113 -> 107, and the wanted items the answer now
presents are the ones the evidence established.

### The merged set - 16 editorial queries, 3 of them present visual,
86 wanted items; the frame lane's own denominator

Same corpus, same index, same scorer; the before half is the base
commit on the rebuilt project, the after half this change.  The
before half reproduces the committed baseline's visual-lane recall
exactly (0.0691), which is what makes the two halves one measurement.
The aggregate false-positive absolutes are lower than the committed
report's on BOTH halves, because the rebuild has no text index (the M1
transcripts live in the corpus directory the privacy block closed) -
the text lane's rows are empty and the consequence totals cover only
what the frame lane returned.  The numbers are comparable
before-vs-after, on the identical setup; they are not comparable with
the committed report's absolutes.

| measure | before | after |
|---|---|---|
| **visual macro recall (region-capped)** | **0.0691** | **0.0927** |
| visual macro recall (uncapped) | 0.1381 | 0.1854 |
| visual macro precision | 0.3333 | 0.4667 |
| editorial macro region recall | 1.0000 | 1.0000 |
| absent queries abstained | 2 / 2 | 2 / 2 |
| **false-positive windows** | **10** | **8** |
| **false-positive seconds** | **70.07** | **70.07** |
| wrong seconds selected | 20.02 | 20.02 |
| missing seconds | 1197.74 | 1197.74 |
| coverage fraction | 0.0099 | 0.0099 |
| shadowing queries | 2 | 2 |

**No fidelity measure regresses.**  The recall rises by 34 %, the
precision by 40 %, and the false-positive windows fall 10 -> 8 with
the false-positive seconds and the wrong seconds unchanged.

Per query, the visual lane's wanted moments credited:

| query | category | wanted | before | after |
|---|---|---|---|---|
| E09 | object | 43 | 1 | **2** |
| E10 | object_state | 21 | 1 | **2** |
| E11 | identity | 22 | 3 | 3 |

**No query loses a wanted moment.**  The stage attribution moves with
it: `not_presented` 20 -> 18 and `ranked_too_low` 54 -> 50.
`temporal_localization` moves 2 -> 4: two more wanted items are now
covered on the sibling angle rather than their own asset, which is the
attribution's bookkeeping for an answer that spans more cameras - the
region recall is 1.0 on every query and the fidelity vector above is
unmoved.

### The corpus this was measured on

The captain's corpus directory became unreadable part-way through the
measurement (a macOS privacy block on `~/Documents`), so the scratch
project that carried the frame index was rebuilt from what IS readable:
the per-source memory (the M2 frame samples) and the corpus manifest
the eval set carries.  The rebuilt index reproduces the committed
baseline's VISUAL-lane recall exactly - 0.0539 on the held-out set,
0.0691 on the merged set, both on the base commit - which is what
makes each before/after pair one measurement.  The aggregate
false-positive and wrong-second absolutes are NOT comparable with the
committed reports (the rebuild has no text index, so the text lane's
rows are empty); they are comparable before-vs-after, on the identical
setup, which is the comparison this lane needs.

**The text lane could not be rebuilt** (the M1 transcripts live in the
blocked project's private memory root), so the text lane's rows are
empty in both halves of this measurement and the editorial macro
numbers over both lanes are not comparable with the committed reports.
The numbers above are the FRAME lane's, on its own denominator, which
is what this change touches.

## The cost

The pass judges one more moment of the cameras that hold a slot, and
that is the whole of the extra cost: the held-out run inspected 58
candidates against the baseline's 55, the merged run 31 against 31
(fewer cameras hold a slot there), and every verdict the runs needed
was recorded or measured live - the merged one through a healthy model
server.  The candidate pool itself is unchanged: the same frames, the
same embeddings, the same matmul, and the merge is O(pool) as it was.

## What this does NOT do

* **It does not widen the frame pool.**  The pool already admits every
  wanted frame (0 of 172 wanted items outside it); a deeper pool would
  be a cost with nothing to buy on this corpus.
* **It does not replace the indexes.**  CLIP, MiniLM and BM25 are read
  exactly as they were.
* **It does not claim the wanted camera.**  The corpus labels are
  camera-specific and asset-granular: H05's wanted moment is labelled
  on one angle, and the other angles show the same thing (the model's
  verdicts agree).  A tighter label is the corpus's next step, not a
  metric change (`docs/benchmarks/2026-10-21-editorial-retrieval-non-gameable-recall.md`).
* **What is left undone: nothing in this lane.**  Both halves of both
sets are measured above.  Rebuilding the text lane is a corpus-access
problem (the M1 transcripts live behind the privacy block), not a
retrieval problem, and it is out of this lane's scope.

## Artefacts

| file | what |
|---|---|
| `docs/benchmarks/2026-10-22-vi002b-candidate-recall.md` | this report |
| `docs/evidence/vi002b-candidate-recall.md` | the evidence, in one file |

## Reproducing

```sh
P=<the rebuilt scratch project>
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_heldout_set.json \
    --families editorial --out-json /tmp/heldout.json
```
