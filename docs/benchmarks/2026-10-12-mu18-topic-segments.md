# MU-18 topic segments: real-episode measurement (2026-10-12)

Plan item MU-18 specifies change points over M1 utterance embeddings
(MiniLM, the footage-search encoder), cutting where the similarity
falls below the source's own lower decile, with acceptance on a
geo-podcast transcript: predicted segments align with hand-marked
topic changes on ≥1 episode within ±1 utterance for most boundaries.

This doc is the acceptance run. Verdict first: **neither the
specified adjacent-utterance rule nor the directed k=3 block variant
reaches the bar, so no topic_segment producer ships.** The branch
implements, measures, and then removes the wiring; this doc is what
stays, with the counts.

## Method

- Encoder: `sentence-transformers/all-MiniLM-L6-v2` through
  `footage_query.load_embedder` (`sentence-transformers` backend) on
  the project's venv interpreter. One row per M1 utterance text.
- Literal rule: cosine between ADJACENT utterances, threshold
  `numpy.percentile(sims, 10)`, cut where strictly below.
- Directed rule (firstmate decision 2026-10-12 `mu18-acceptance`):
  cosine between the mean of the 3 utterances BEFORE each gap and the
  mean of the 3 AFTER it (`BLOCK_SIZE = 3`, stated not fitted),
  same decile comparison. An off-by-one mapping series positions to
  block edges was found by fixture tests and fixed before any
  held-out run; all numbers below use the fixed mapping.
- Hand marks: one rater (the worker) read each transcript in full and
  marked the utterance index where each new topic/production block
  starts, BEFORE running any prediction on that transcript. Same
  protocol everywhere: semantic topic shifts plus production shifts
  (fresh takes, direction asides, CTA blocks), since both break reel
  moments.
- Chance baseline: 2000 (confirmation: 5000) draws of the same number
  of random boundaries per episode; reports mean hits and P(random ≥
  observed).

Media read only: transcripts were read from
`~/.local/share/vep/source_memory/`; nothing was written there. No
other M1 transcripts exist on this machine: all seven are the same
geo-podcast shoot, so "a different shoot or series" was not possible
for confirmation; the two confirmation episodes below were unread
before marking.

## Per-episode counts, literal rule

| Episode (source) | Utts | Thr | Cuts | Hand marks | Hits ±1 | Rate | Chance mean | P(rand ≥ obs) |
|---|---|---|---|---|---|---|---|---|
| LCATL0013 (68 min) | 1025 | 0.0737 | 103 | 32 | 7 | 0.22 | 8.7 | 0.806 |
| LC4932 (82 min, same shoot, other camera) | 1285 | 0.0717 | 129 | 32 | 11 | 0.34 | 8.6 | 0.228 |
| LCATL0014 (14 min, clean Q&A) | 201 | 0.0627 | 20 | 22 | 10 | 0.46 | 6.0 | 0.044 |

LCATL0013 is statistically indistinguishable from random cuts
(p=0.81). LC4932 is not significantly above chance (p=0.23).
LCATL0014 is weakly above chance (p=0.044) but below the "most"
(>0.50) bar.

## Confirmation: directed k=3 on held-out episodes

k=3 scored 16/32, 15/32 and 8/22 on the three episodes above, but its
width was explored on those same marks, so the decision required
confirmation on held-out material with the bar: p<0.05 on both and
"most boundaries" on at least one.

| Episode | Utts | Thr | Cuts | Hand marks | Hits ±1 | Rate | Chance mean | P(rand ≥ obs) |
|---|---|---|---|---|---|---|---|---|
| LC4931 (unread before marking) | 106 | 0.2603 | 10 | 12 | 6 | 0.50 | 3.14 | 0.060 |
| LCATL0012 (unread before marking) | 102 | 0.2748 | 10 | 14 | 6 | 0.43 | 3.80 | 0.153 |

Neither reaches p<0.05; neither reaches most (6/12 is exactly half,
not most). The directed variant is therefore rejected with the
literal rule.

Held-out hand-mark lists (utterance indexes where a new block starts):

- LC4931:
  `8,9,15,17,30,33,57,75,81,87,89,96`
- LCATL0012:
  `7,8,15,17,24,31,34,54,60,71,77,82,84,91`

Earlier hand-mark lists (used episodes):

- LCATL0013:
  `24,58,96,137,186,211,259,284,338,380,405,434,493,509,528,583,607,632,653,700,719,748,786,806,835,859,897,910,938,970,989,1011`
- LC4932:
  `25,55,96,139,192,219,266,294,356,394,424,454,526,542,564,623,647,675,683,742,762,792,835,861,887,910,948,959,986,1021,1041,1061`
- LCATL0014:
  `6,15,20,27,33,48,56,63,69,79,91,102,115,120,129,134,137,143,153,156,165,176`

## What the cuts land on

Spot-checking predicted boundaries against the transcript shows they
land on filler↔content transitions ("okay" → take, take → "i keep
messing this up") far more often than on topic shifts: the rule
segments the session's production flow (takes, asides, retakes), not
its topics. Misses are typically 2–8 utterances from the hand mark -
a topic opens with discourse markers ("so...", "okay") and shared
vocabulary, so the cosine only collapses after the transition, if at
all. Four hand-marked gaps on LCATL0013 have similarity ABOVE the
median (percentiles 92–97): single-adjacent similarity genuinely
cannot see those shifts.

Explored variants (same encoder, same decile idea - all rejected with
the rules they vary, since every variant changes the specified
mechanism or was explored post-hoc on the used episodes):

| Variant | LCATL0013 | LC4932 | LCATL0014 | LC4931 (held-out) | LCATL0012 (held-out) |
|---|---|---|---|---|---|
| k=1 literal | 7/32 (p=0.81) | 11/32 (p=0.23) | 10/22 (p=0.044) | - | - |
| k=1 + run-collapse (one cut per below-floor run) | 7/32 | 11/32 | 9/22 | - | - |
| k=3 block means (directed, then confirmed) | 16/32 (p=0.004) | 15/32 (p=0.009) | 8/22 (p=0.23) | 6/12 (p=0.060) | 6/14 (p=0.15) |
| k=4 block means | 17/32 (p=0.001) | 14/32 (p=0.032) | 7/22 (p=0.39) | - | - |
| diarization-turn grouping | 5/32 | - | - | - | - |

Block smoothing (TextTiling-style) beats chance strongly on the two
long rehearsal-heavy episodes but is inconsistent on the short clean
one, and its window width was explored post-hoc on these same marks,
so it cannot be adopted without an independent confirmation set.

## Method notes for re-runs

- Strict `<` against the decile: with `n <= 11` similarities the 10th
  percentile sits at/between the two lowest values, so a second tied
  drop can never cut. Short transcripts may cut once or not at all;
  this is the specified comparison, kept literally.
- Encode cost: ~3 s per 1000 utterances (MiniLM, CPU) plus one model
  load; the whole measurement is seconds, not a heavy pass.
- Re-run: `score_all.py` logic is described above; hand marks are the
  table in this doc. Scripts were scratch (outside the worktree) and
  are not committed; the hermetic suite carries a synthetic
  equivalent (`tests/unit/picture/test_topic_segments.py`).

## Options (for firstmate/captain)

Decision 2026-10-12 (`mu18-acceptance`): fix k=3 (stated, not fitted)
and confirm on held-out material; ship k=3 only if p<0.05 on both
held-out episodes and "most boundaries" on at least one, else drop
the producer and keep this doc.

Outcome: the held-out bar was missed (p=0.060 and p=0.153, rates
0.50 and 0.43), so the branch drops all topic_segment wiring - the
`topic_segments` module, its Project Model migration and lane key,
the search-index scope and the reel-proposal reader, with their
tests - and keeps this measurement doc. No test pins either rule.

If topic segmentation is revisited, the evidence here says: single
utterance adjacency tracks production flow on session transcripts;
block smoothing helps long rehearsal-heavy episodes but not short
clean ones; any new mechanism needs an independent confirmation
episode before it ships as `topic_segment`.
