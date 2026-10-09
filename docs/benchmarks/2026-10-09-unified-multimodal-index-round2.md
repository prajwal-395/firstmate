# Bakeoff round 2: evidence-routed unified EG2 index vs today's default

**Measured 2026-10-09. Verdict: keep MiniLM hybrid as the default; the
unified backend stays OPT-IN. It does not win clearly on this set -
closest on recall, worse on precision, ~1,000x the build cost.**

Round 1 (`2026-10-09-unified-multimodal-index.md`) recorded two defects
with fixes (non-finite video rows, max-floor retention) and two priced
follow-ups (concatenated window docs dilute distinctive phrases;
single-character OCR strings flood visual queries). This round builds
the four committed follow-ups in `e776150b` and re-scores:

1. **Per-row text anchors** - each overlapping analysis row embedded on
   its own; a window scores the max over its rows.
2. **OCR match specificity** - exact words match at any length,
   substrings only at 4+ chars over stopword-filtered tokens, in the
   one shared `ocr_text_matches` rule.
3. **Fuse by evidence** - intent routing (`spoken` to transcript,
   `shot`/`text` to video/still/description, else all lanes) with
   reciprocal-rank fusion across lanes; dense mode ranks routed dense
   lanes only.
4. **Per-anchor abstention floors** - absent probes grown to the 12
   hand-verified absent queries (X01-X12); each anchor judged by its
   own floor.

Query set: the same `2026-10-09-unified-multimodal-queries.json`, same
100 creator-style queries, same 11 configs, same scoring (recall needs
every gold span, tolerance 1 s; precision micro over present queries;
abstention over absent). Present = gold is a span list (56 @001, 21
@geo); absent = gold is `"absent"` (15 @001 including E02-E04, 8 @geo).

## Corpus change since round 1 (read before comparing absolutes)

Round 1 built 001 from a stale `/tmp` copy (265 segments). That copy
is gone (Mac restart); this round builds from the original project at
`post a day keeps the apple away/001`, which now yields **517 rows**
(110 speech, 109 chunks, 86 action, 21 scene, 18 camera, 173 object -
the same 517 the standing search-bakeoff report measured). Geo-podcast
is unchanged (4,219 speech/chunk rows). **Absolute round-1 vs round-2
numbers are not comparable on 001** (more rows, more distractors, plus
the OCR rule change moves the shared lane). The verdict below rests on
the round-2 head-to-head, where every config ran on identical inputs.
Geo numbers reproduce round 1 exactly on the unchanged paths
(minilm-hybrid 0.714, lexical 0.667), which validates the harness.

No Resolve process, timeline, cursor, broker, project index, project
output, or source footage was written. All indexes were built into
scratch (`/tmp/fm-vep-unified-r2/indexes`) from read-only project
inputs.

## Build cost (one-run observations, this 24 GB Mac)

| Index | Wall | Peak RSS | Rows |
|---|---|---|---|
| 001 minilm (517 segs) | 5.2 s | 695 MiB | 517 x 384-d |
| geo minilm (4,219 segs) | 8.7 s | 853 MiB | 4,219 x 384-d |
| 001 EG2 text | 31.8 s | 1,272 MiB | 517 x 256-d |
| geo EG2 text | 29.0 s | 1,796 MiB | 4,219 x 256-d |
| 001 EG2 full (text + chunks + unified) | ~85 min* | 2,696 MiB | 108 chunks + 109/109/109/1020/278 anchor rows |

\* 60 min initial run to window 60/109 (killed by the harness's own
timeout, not a failure), then 26 min resume from the durable
checkpoint - resume verified row-aligned (every matrix matches its row
tags, all finite). One 2-frame video tail emitted NaN and is named
unembedded, as in round 1; 13 description and 3 transcript windows
have no overlapping rows and are named, never zero-filled.

Calibrated floors on 001: video 0.753, still 0.737, audio 0.790,
**description 0.884**, transcript 0.824 (round 1: 0.729 / 0.740 /
0.785 / 0.715 / 0.792). The description floor spike is the
present-word probes ("cup", "baby", "dog" occur in 001 footage while
their queried SUBJECT is absent) landing on concentrated per-row
anchors - a single-word row matches its probe far above the old
concatenated-doc level. The floor is doing what the spec asks; the
cost is below.

## Results (top-5; tolerance 1 s)

| Config | Recall@5 | Precision | Mean loc | Abstention | p50 |
|---|---|---|---|---|---|
| minilm-hybrid@001 | 0.821 | 0.422 | 0.782 | 0.667 | 38.8 ms |
| minilm-lexical@001 | 0.696 | 0.380 | 0.666 | 0.467 | 9.2 ms |
| eg2text-hybrid@001 | 0.839 | 0.360 | 0.781 | 0.533 | 41.4 ms |
| eg2text-dense@001 | 0.750 | 0.276 | 0.682 | 0.533 | 65.3 ms |
| eg2chunks@001 | 0.161 | 0.342 | 0.148 | 1.000 | 36.9 ms |
| unified-hybrid@001 | 0.804 | 0.260 | 0.725 | 0.667 | 44.3 ms |
| unified-dense@001 | 0.607 | 0.442 | 0.561 | 0.933 | 41.0 ms |
| minilm-hybrid@geo | 0.714 | 0.398 | 0.536 | 1.000 | 22.8 ms |
| minilm-lexical@geo | 0.667 | 0.404 | 0.515 | 0.875 | 1.3 ms |
| eg2text-hybrid@geo | 0.667 | 0.376 | 0.546 | 0.875 | 39.7 ms |
| eg2text-dense@geo | 0.762 | 0.386 | 0.573 | 0.875 | 40.9 ms |

Recall@5 by category, 001 present queries:

| Category | minilm-h | minilm-l | eg2-h | eg2-d | chunks | uni-h | uni-d |
|---|---|---|---|---|---|---|---|
| spoken_phrase (16) | 0.94 | 1.00 | 0.94 | 1.00 | 0.00 | 0.94 | 0.94 |
| object (14) | 0.79 | 0.71 | 0.86 | 0.71 | 0.14 | 0.86 | 0.64 |
| action (7) | 0.71 | 0.29 | 0.71 | 0.71 | 0.43 | 0.57 | 0.43 |
| shot_type (6) | 0.83 | 0.33 | 0.83 | 0.33 | 0.50 | 0.50 | 0.50 |
| on_screen_text (7) | 0.71 | 0.71 | 0.71 | 0.57 | 0.00 | 1.00 | 0.14 |
| person (5) | 1.00 | 0.80 | 1.00 | 1.00 | 0.20 | 0.80 | 0.60 |
| expression E01 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

## What the set caught

1. **Shot-type queries are fixed on the text paths (OCR specificity
   works).** Round 1 scored 0.00 on every text backend because
   single-character OCR strings ("a", "N", "D") outranked the true
   camera window. Now minilm-hybrid and eg2text-hybrid reach 0.83
   (5/6); the shared `ocr_text_matches` rule holds. Unified-hybrid
   reaches 0.50 - routing helps, BM25 neighbours still win half.
2. **On-screen text is perfect on unified-hybrid (7/7).** Exact-word
   OCR retention carries these: the text is really pictured, so the
   window retains on that evidence alone. Unified-dense manages 0.14 -
   dense vectors alone cannot read text, and the 0.884 description
   floor leaves no room.
3. **The per-row fix did not save E01.** "Neutral expression" scores
   0.00 on every config including unified: per-row concentration
   raised the phrase, but the 12-probe calibration raised its floor
   further (0.884). There is still no expression lane; this stays an
   observation, not a finding for any backend.
4. **Fusion with BM25 still hurts spoken phrases** (0.94 hybrid vs
   1.00 lexical/dense on 001) - word overlap promotes wrong windows,
   the same mechanism round 1 named. Not fixed, not regressed.
5. **The chunk lane over-abstains.** eg2chunks abstains on all 15
   absent AND all 56 present queries it misses (recall 0.161,
   abstention 1.000): the 12-probe video floor (0.753) is fail-closed
   to the point of silence. Owner: the chunk lane's floor; the
   unified visual anchors on the same footage do not go this quiet
   (unified-hybrid 0.804).
6. **Unified-dense trades recall for precision.** 0.607 recall (round
   1: 0.714) under the 0.884 description floor, but the best 001
   precision (0.442) and near-perfect abstention (0.933, 14/15). The
   floor calibration is a precision/abstention dial turned past recall.

## Verdict

Keep `minilm` hybrid as the default. The unified backend stays
opt-in: unified-hybrid recall (0.804) trails the default (0.821) by
one query, precision is much worse (0.260 vs 0.422), localization is
coarser (0.725 vs 0.782), and the build costs ~85 min and 2.7 GiB
against 5 s and 0.7 GiB. The best 001 recall, eg2text-hybrid (0.839),
beats the default by one query with worse precision - not a switch.
On speech-only geo, eg2text-dense (0.762) edges minilm-hybrid (0.714)
by one query of 21 - inside noise, as in round 1. Abstention is
materially better than round 1 everywhere on 001 (0.467-1.000 vs
0.000), shared across backends through the OCR fix, not a unified
exclusive.

Priced follow-ups: description-floor fragility to present-word probes
(finding 6 - the floor needs a probe set that separates present words
from present subjects, or a per-row discount); the chunk lane's silent
floor (finding 5); the expression lane gap (finding 3, carried over).

Raw rows: `2026-10-09-unified-multimodal-round2-results.json` (613
query-runs: 71 x 7 configs @001 + 29 x 4 @geo). Build ledger:
`2026-10-09-unified-multimodal-round2-builds.jsonl`. Query file reused
unchanged from round 1.
