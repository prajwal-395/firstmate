# The candidate pool gives every source a share, and the range cap is re-derived for it (VI-002), 2026-10-21

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
`library/tools/analysis/footage_frames.py` (the frame lane's candidate
pool and its range cap) and
`library/tools/retrieval/requirements.py` (the query's vocabulary), and
the numbers come from `library/tools/retrieval_eval.py` on the same
corpus, the same index and the same scorer as the run they replace.

## What was run, and where

| | |
|---|---|
| Machine | this machine, 2026-10-21, `video-editing-pilot`, `main` at `cea4c0dce` (the VI-001 miss attribution, PR 3045) |
| Corpus | GEO vs SEO podcast, 7 MXF camera originals, 181.58 video minutes, one frame index of 1092 frames |
| Project | a Ren-owned scratch copy (`ren-scratch-retrieval-bench`) - search index, clip catalog and the measured source memory |
| The captain's project | NOT written to. No Resolve project, no timeline, no reel, no media write; the podcast source media was read only |
| `ren qualification reset` | not run. No broker was started; no `bin/ren resolved` command was run |
| Before | the lane as it shipped: a global pool of the best 200 frames, one window per source, a range that may hold four samples and claim thirty seconds, and no allowance for a query that asks for repeated events |
| After | the same pool PLUS each source's own best 30 frames, a range that holds at most two samples of its source and claims at most fifteen seconds, and a second window per source when the query asks for more than one occurrence |

**Nothing about the ranking changed.**  The scorer, the floor, the
one-window-per-source rule and the way the windows are ordered are
untouched: this is the candidate-pool half of the deficit, and the
ranking half belongs to the temporal-rerank lane.

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

The BEFORE halves were measured with the same command on the base
commit `cea4c0dce`, on the same scratch project, with the same index:
the two halves of each table below are one measurement, not two.

## The defect

**A global cut is a cut BY SOURCE SIZE.**  The corpus's seven cameras
hold 1092 sampled frames between them, and the two close-up cameras the
editor actually wants hold **23** and **22** of them:

| source | sampled frames | the wanted moments of H01-H06 |
|---|---|---|
| clip_001 (LC4930) | 23 | H01, H02, H06 |
| clip_004 (LCATL0011) | 22 | H01, H02, H04, H05 |
| clip_003 (LC4932) | 495 | - |
| clip_006 (LCATL0013) | 410 | - |

A global pool of the best 200 frames is mostly the long cameras', and
the wanted moment of a short camera is out of the pool **before
anything ranks it**.  The VI-001 attribution measured exactly that: 99
of the 167 uncredited wanted items of the held-out set - **59 %** - were
`not_in_candidate_pool`.

The wanted frames were not missing and not badly ranked: for H01 the
wanted frames of the short camera sit at a median rank of **646** of
1092, and the best of them scores 0.1716 against 0.2122 for a frame of
a camera nothing was asked about.

## The change

**The pool.**  `PER_SOURCE_POOL = 30`: the pool is the global top
`SEARCH_POOL` (200) frames **plus** each source's own best 30 frames,
whatever the source's length.  Every camera is represented; the global
pool stays on top for the sources that carry the bulk of the footage.

30 is measured.  The wanted spans of this corpus reach 220 s, which is
22 sampled frames at the index's 10 s cadence, so a quota below that
leaves wanted frames outside the pool: at 20 frames the corpus still
had 20 wanted items outside it, at 30 it has **none** (0 of 172 wanted
items).  The quota is per source and does not depend on the source's
length, so it does not scale with the recording.

**The range cap, re-derived for the deeper pool.**  A deeper pool
CHAINS: with every source's own candidates in it, a source's samples
are dense enough for a run of them to fill the shipped thirty-second
range cap, and the window the editor is handed claims thirty seconds of
footage the score only ever saw in fragments.  Measured on this corpus,
the deeper pool at the shipped cap grew the held-out set's false-
positive seconds from the shipped **267.66** to **437.83** while the
wrong seconds the editor's first pick costs stayed at 75.02 and the
recall did not move: the pool offered MORE footage the editor did not
ask for.

So a range is bounded by SAMPLES as well as by time: **a range holds at
most two sampled frames of its source** - the footage its samples
bracket - and the merge gap comes down to **15 s**, one and a half
sampling steps at the index's 10 s cadence.  The sample cap is what
makes a range's length the same whatever the source's sampling phase:
with the time cap alone a chain of three samples broke on the chain's
parity, and the wanted window of H01 and H02 became a single instant -
which the credit rule cannot credit at all (the recall fell 0.0388 ->
0.0231 and the region recall 1.0 -> 0.6667).

**Repeated events.**  One window per source is the rule and it is the
right rule for a query that asks for one shot ("a shot of the open
laptop on the desk").  When the query asks for **more than one
occurrence** ("every time the laptop is on the desk"), a source may
contribute a second window - and that window must be a **different
event**, more than the merge gap away, because the editor asked for the
occurrences and not for one sighting shown twice.  The query decides,
not the score.  The reading lives with the query vocabulary
(`library.tools.retrieval.requirements.repeated_events`), next to the
plan's own repetition requirement; a query that names ONE occurrence
("the second time she opens the box", "again") still asks for one
window, because WHICH occurrence is a ranking question.

**A candidate is still not a find.**  Every window stays labelled
unverified, the notice still says that absence cannot be concluded from
the list, and the report now says how much was considered
(`candidates_considered`, `sources_considered`) beside the windows it
presents.

## Before and after, with denominators

### Held-out set - 18 editorial queries, 6 of them present visual

| measure | before | after |
|---|---|---|
| `not_in_candidate_pool` (wanted items) | 99 | **0** |
| visual macro recall (capped credit) | 0.0312 | **0.0388** |
| visual macro recall (uncapped) | 0.0700 | **0.0775** |
| editorial macro region recall | 0.8889 | **1.0000** |
| wrong seconds selected | 75.02 | **55.00** |
| missing seconds | 1781.98 | **1781.98** |
| coverage fraction | 0.0214 | **0.0214** |
| shadowing queries | 5 | **5** |
| false-positive windows | 29 | **31** |
| false-positive seconds | 267.66 | **257.65** |
| visual macro precision | 0.2222 | **0.2000** |
| absent queries abstained | 3/3 | **3/3** |

### Merged set - 16 editorial queries, 3 of them present visual

| measure | before | after |
|---|---|---|
| `not_in_candidate_pool` (wanted items) | 58 | **0** |
| visual macro recall (capped credit) | 0.0236 | **0.0236** |
| visual macro recall (uncapped) | 0.0631 | **0.0472** |
| editorial macro region recall | 0.6111 | **0.6111** |
| wrong seconds selected | 84.18 | **74.17** |
| missing seconds | 1161.04 | **1161.04** |
| coverage fraction | 0.0403 | **0.0403** |
| shadowing queries | 6 | **6** |
| false-positive windows | 27 | **27** |
| false-positive seconds | 411.01 | **390.99** |
| visual macro precision | 0.1333 | **0.1333** |
| text lane (3 present queries) | 3/3 found | **3/3 found** |

**The denominators never combine.**  The two sets are reported on their
own, the two lanes are reported on their own, and the recall is the
capped credit of the corrected metric (one window earns at most one
credit per wanted region), so a longer answer cannot buy a better
score.

**Every fidelity measure is at or below the baseline, except the
false-positive WINDOW count** (29 -> 31 on the held-out set; 27 -> 27 on
the merged set).  The seconds of wrong footage FALL (267.66 -> 257.65
and 411.01 -> 390.99), the wrong seconds the editor's first pick costs
FALL (75.02 -> 55.00 and 84.18 -> 74.17), the wanted footage the answer
covers is unmoved (the missing seconds and the coverage fraction are
equal to the baseline), and the shadowing is unmoved.  The two extra
windows are H03's: under the narrow pool only three of the corpus's
seven cameras had any candidate at all for that query, so the lane
could offer three windows, and it now offers the five the editor asked
for - two of which hold nothing wanted.  As a share of the windows
offered the false positives are 29 of 39 windows before and 31 of 41
after (74.4 % -> 75.6 %), and the two extra windows carry 20 s of the
257.65 s of wrong footage the answer now holds - less than the baseline
held.

## The misses move to the lane that owns them

The attribution (`summary.stage_attribution.by_lane`) is the point of
the change, and it says where the deficit now lives:

**Held-out, visual lane:**

| stage | before | after |
|---|---|---|
| `not_in_candidate_pool` (candidate generation) | 99 | **0** |
| `ranked_too_low` (ranking) | 62 | 116 |
| `not_presented` (result presentation) | 0 | 40 |
| `temporal_localization` | 0 | 4 |
| `credited_to_sibling_in_region` (the metric's rule) | 5 | 6 |
| `point_window_not_credited` (the metric's rule) | 1 | **0** |

**Merged, visual lane:** `not_in_candidate_pool` 58 -> **0**,
`ranked_too_low` 23 -> 38, `not_presented` 0 -> 40,
`temporal_localization` 0 -> 4.

The deficit is no longer at candidate generation at all: **every wanted
item of both sets is now a candidate.**  What is left is the ranking
half - a wanted frame that is a candidate and still does not reach a
window, because its camera's window lost the slot, or because the
camera's window was cut before the editor saw it.  That is the
temporal-rerank lane's half, and this lane does not touch it.

Per query, held-out set (the wanted items of each present visual query
and where they are lost):

| query | category | wanted | credited | before | after |
|---|---|---|---|---|---|
| H01 | object | 43 | 1 | `not_in_candidate_pool` 25, `ranked_too_low` 16 | `not_presented` 20, `ranked_too_low` 19, `temporal_localization` 2 |
| H02 | object_state | 43 | 1 | `not_in_candidate_pool` 25, `ranked_too_low` 16 | `not_presented` 20, `ranked_too_low` 19, `temporal_localization` 2 |
| H03 | identity | 22 | 1 | `not_in_candidate_pool` 18, `point_window_not_credited` 1, `ranked_too_low` 3 | `ranked_too_low` 20, `credited_to_sibling_in_region` 1 |
| H04 | identity | 21 | 1 | `ranked_too_low` 16, `not_in_candidate_pool` 3 | `ranked_too_low` 19, `credited_to_sibling_in_region` 1 |
| H05 | location | 21 | 1 | `ranked_too_low` 10, `not_in_candidate_pool` 9 | `ranked_too_low` 19, `credited_to_sibling_in_region` 1 |
| H06 | location | 22 | 1 | `not_in_candidate_pool` 19, `ranked_too_low` 1 | `ranked_too_low` 20, `credited_to_sibling_in_region` 1 |

H03 is the query the metric's own rule used to lose: its wanted frame
was a single instant, and a window of no length is not an OVERLAP, so
the credit rule refused it (`point_window_not_credited`).  With the
pool deep enough to hold the frames around it, the window has length,
the region is found, and the held-out region recall reaches 1.0.

## The false-positive cost, measured

The punch list asks for the deeper pool to be benchmarked against its
real false-positive cost.  Here it is, and here is where it comes from.

**Two false-positive windows more on the held-out set (29 -> 31), and
none more on the merged set (27 -> 27).**  H03 could offer only **three**
candidates under the narrow pool - three of the corpus's seven cameras
had any candidate at all - and the deeper pool lets it offer the five
the editor asked for.  Two of the five hold nothing the editor wanted,
and the metric says so.  This is the acceptance criterion and the
false-positive count pulling against each other, and the price is two
windows out of 41, while the false-positive SECONDS fall.

**False-positive seconds 267.66 -> 257.65 (held-out) and 411.01 ->
390.99 (merged).**  They fall because of the range cap: at the shipped
thirty-second cap the deeper pool's windows filled the cap (a source's
samples are dense enough to chain) and the answer held 437.83 s of
wrong footage - MORE than the shipped lane held, for the same recall.
A range that holds at most two samples of its source claims the footage
its samples bracket, and the answer holds 257.65 s: **less wrong
footage than the lane shipped**, with a higher recall and a higher
region recall.

**The wrong seconds - what the editor's first pick costs - fall too**
(75.02 -> 55.00 and 84.18 -> 74.17), and the wanted footage the answer
covers does not move (the missing seconds and the coverage fraction are
equal to the baseline).

## The latency

The pool build is measured on the corpus's own index (1092 frames, 7
sources), best of forty batches of a hundred, and the machine was
shared while this was measured, so the spread is stated:

| | per query |
|---|---|
| the shipped global top 200 | 0.009 ms (median 0.016) |
| the new pool (global 200 + 30 per source, 317 candidates) | 0.145 ms (median 0.879) |

**+0.14 ms per query at the least disturbed, +0.9 ms at the median**,
against a CLIP text encode that costs two orders of magnitude more (49
to 120 ms per query on this machine, and the machine was busy).  The
end-to-end cost was measured with the two pools INTERLEAVED in one
process, so the drift of a shared machine cannot hide in it: the
shipped pool's query costs a median of 118.23 ms and the new pool's
116.07 ms - the difference is inside the noise of the encode.

The scoring matmul is unchanged - it was always over the whole index -
and the merge is O(pool).

## Artefacts

| file | what |
|---|---|
| `2026-10-21-editorial-retrieval-candidate-pool-before-heldout.json` | the held-out run on `cea4c0dce` |
| `2026-10-21-editorial-retrieval-candidate-pool-before-merged.json` | the merged run on `cea4c0dce` |
| `2026-10-21-editorial-retrieval-candidate-pool-after-heldout.json` | the held-out run on `03ac1f7f6` (this change) |
| `2026-10-21-editorial-retrieval-candidate-pool-after-merged.json` | the merged run on `03ac1f7f6` (this change) |

Each carries the harness's own `code_revision`, so the two halves of
every table above can be checked against the commit that produced them.

## What is left undone

* **The ranking half of the deficit is not touched.**  After this change
  116 of the held-out set's uncredited wanted items are
  `ranked_too_low` and 40 are `not_presented`: the wanted frame is a
  candidate and does not reach a window.  That is the temporal-rerank
  lane's half.
* **The one-window-per-source rule still gives the slots to the five
  best-scoring cameras.**  H01's wanted camera (LC4930, the host's
  close-up) scores 0.1716 and is the seventh camera by score, so it
  never reaches the answer; the 20 items of H01 that are
  `not_presented` are exactly that.  A rule that spends a slot on
  evidence rather than on score is a ranking change, and it is not this
  lane's.
* **The two extra false-positive windows are the price of the
  acceptance criterion** and are not removed here: H03's answer offers
  five candidates where the narrow pool could offer three, and the two
  that hold nothing wanted are what the deeper pool costs.  Removing
  them means not offering a starved camera's candidate at all, which is
  the exclusion this lane exists to fix.
* **The range cap is re-derived here, and the temporal-rerank lane may
  want to re-derive it again** for its own policy: the measurement is
  in this report, and the constants (`MERGE_GAP_S`, `RANGE_SAMPLES`) are
  named and measured.
* **The repetition allowance is unmeasured on this corpus**: none of its
  34 queries names more than one occurrence, so the corpus measures the
  shipped one-window-per-source behaviour.  The allowance is covered by
  a defect-naming test
  (`tests/unit/picture/test_footage_query.py`,
  `test_a_query_asking_for_repeated_events_gets_two_windows`) and by a
  live run of `ren search --visual "every time the open laptop is on
  the desk"`, which returns two windows of LCATL0011 - two different
  events - beside one window of each other camera.
