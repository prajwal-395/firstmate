# Bakeoff: unified five-anchor EG2 index vs today's default

**Measured 2026-10-09. Verdict: keep MiniLM hybrid as the default; the
unified backend ships OPT-IN (`--unified-anchors` / `--unified`). It
does not win clearly on this set - and the set caught four real
defects, two fixed before scoring, two recorded below.**

Query set: `2026-10-09-unified-multimodal-queries.json` (same directory),
100 creator-style queries committed BEFORE any scoring run. 71 over
project 001 (17 clips, 807 s, all media present; speech + OCR on all
clips, vision rows on clips 001/002/005, no expression or sound lanes),
29 over geo-podcast (7 clips, ~3 h, 2,854 utterances / 29,154 words via
source memory, no vision rows). Categories: spoken phrases (37),
objects (14), actions (7), shot types (6), on-screen text (7), people
(5), expressions (4), absent things (20). Gold spans were read off the
corpora's own analysis artifacts (temporal speech rows, vision
action/scene/camera/object rows, step 1.07 OCR strings, source-memory
transcripts; 12 GEO queries reused from the standing
`retrieval_eval_set.json` with asset-to-clip mapping). Four key visual
windows (blue sedan, Mazda wheel, driving view, hand with bracelet)
were additionally verified against ffmpeg-extracted stills inspected by
the worker; the rest are artifact-verified, stated here so the next
reader knows which is which.

No Resolve process, timeline, cursor, broker, project index, project
output, or source footage was written. All indexes were built into
`/tmp/vep-unified-bakeoff/indexes` from read-only project inputs; all
project files stayed read-only.

## What was compared

| Config | Index | Query path |
|---|---|---|
| minilm-hybrid | MiniLM 384-d + BM25 + sound/OCR/object/person lanes | `search_report hybrid` (today's default) |
| minilm-lexical | same index | `search_report lexical` (keyword ablation) |
| eg2text-hybrid | EG2 256-d text + BM25 + lanes (single anchor, fused) | `search_report hybrid` |
| eg2text-dense | same index | `search_report dense` (single anchor, unfused) |
| eg2chunks | EG2 256-d 8 s video windows | `search_chunks` |
| unified-hybrid | five linked anchors/window (video, still, audio, description, transcript) + BM25 + OCR | `search_unified hybrid` |
| unified-dense | same anchors | `search_unified dense` (anchors only) |

CLIP frames were NOT scored: the 001 copy holds no M2 frame samples,
so `build_frame_index` produced 0 frames, and sampling them requires an
analysis write the read-only rule forbids here. The prior bakeoff
(`data/vep-search-bakeoff/report.md` in firstmate's home) already
established the CLIP lane has no abstention floor; nothing here
re-measures it.

## Build cost (one-run observations, this 24 GB Mac)

| Index | Wall | Peak RSS | Rows |
|---|---|---|---|
| 001 minilm (265 segs) | 8.0 s | 490 MiB | 265 x 384-d |
| geo minilm (4,219 segs) | 5.9 s | 490 MiB | 4,219 x 384-d |
| 001 EG2 text | 22.6 s | 1,227 MiB | 265 x 256-d |
| geo EG2 text | 29.5 s | 1,433 MiB | 4,219 x 256-d |
| 001 EG2 full (text + 109 video chunks + 109 unified windows x 5 anchors) | 1,190.8 s | 1,629 MiB | 109 video + 109/109/109/5/109 anchor rows |

Peak RSS is process high-water including model weights, one run each,
not p50/p95. The unified build embeds 5 x 109 windows with bounded
batches of 4; the video tower dominates the 20 minutes.

## Results (top-5; tolerance 1 s; recall needs every gold span of the query)

| Config | Recall@5 | Precision | Mean loc | Abstention | p50 |
|---|---|---|---|---|---|
| minilm-hybrid@001 | 0.768 | 0.156 | 0.768 | 0.000 | 9.8 ms |
| minilm-lexical@001 | 0.804 | 0.167 | 0.804 | 0.000 | 1.9 ms |
| eg2text-hybrid@001 | 0.786 | 0.161 | 0.777 | 0.000 | 44.5 ms |
| eg2text-dense@001 | 0.768 | 0.154 | 0.755 | 0.000 | 47.4 ms |
| eg2chunks@001 | 0.250 | 0.197 | 0.175 | 0.933 | 41.5 ms |
| unified-hybrid@001 | 0.661 | 0.133 | 0.574 | 0.000 | 40.7 ms |
| unified-dense@001 | 0.714 | 0.144 | 0.617 | 0.000 | 53.7 ms |
| minilm-hybrid@geo | 0.714 | 0.357 | 0.569 | 1.000 | 11.1 ms |
| minilm-lexical@geo | 0.667 | 0.337 | 0.633 | 0.875 | 0.9 ms |
| eg2text-hybrid@geo | 0.667 | 0.327 | 0.633 | 0.875 | 37.9 ms |
| eg2text-dense@geo | 0.762 | 0.337 | 0.635 | 0.875 | 39.4 ms |

Latency is steady-state per query (encoders warmed; unified loads
lazily on its first query - an outlier the p50 does not feel).
Coverage (queries with any retained evidence): 1.000 on 001 for every
text/unified config (0.324 chunks); on geo 0.724-0.759 with absent
queries correctly empty.

Recall@5 by category, 001 present queries:

| Category | minilm-h | minilm-l | eg2-h | eg2-d | chunks | uni-h | uni-d |
|---|---|---|---|---|---|---|---|
| spoken_phrase (16) | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.94 | 1.00 |
| object (14) | 1.00 | 1.00 | 1.00 | 1.00 | 0.29 | 0.79 | 0.86 |
| action (7) | 0.71 | 0.86 | 0.71 | 0.86 | 0.57 | 0.57 | 0.71 |
| shot_type (6) | 0.00 | 0.00 | 0.00 | 0.00 | 0.67 | 0.00 | 0.00 |
| on_screen_text (7) | 0.71 | 0.71 | 0.71 | 0.57 | 0.14 | 0.57 | 0.57 |
| person (5) | 0.60 | 0.60 | 0.60 | 0.60 | 0.20 | 0.60 | 0.60 |
| expression E01 | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 |

## What the set caught

1. **Non-finite video vectors on short tails (fixed before scoring).**
   The real EG2 video tower intermittently emits NaN for 2-frame tail
   windows (measured: two 1.5-1.9 s tails on 001; re-encoding the same
   stacks later returned finite vectors, so it is instability, not
   determinism). Both the unified anchors and the shared chunk lane now
   drop non-finite rows and name the windows unembedded; calibration
   skips non-finite rows; search refuses when no anchor measured a
   floor. Without the guard the video floor calibrated to NaN and the
   lane went blind.
2. **Max-over-anchors retention silences visual hits (fixed before
   scoring).** The first unified build judged every anchor by the
   strictest floor (transcript 0.79); video/still hits at 0.73-0.75
   died. Each anchor is now judged by its own calibrated floor
   (video 0.729, still 0.740, audio 0.785, description 0.715,
   transcript 0.792 on 001). Pinned in
   `test_retention_is_per_anchor_not_max_floor`.
3. **Concatenated window docs dilute distinctive phrases (recorded,
   not fixed).** A window's description anchor concatenates every
   overlapping vision row (913 chars on clip_001#u#000); the exact
   phrase "neutral expression" it contains scores 0.689 - below its
   own 0.715 floor - while unrelated transcript windows score
   0.76-0.82. Follow-up: embed overlapping rows as separate anchors
   instead of one concatenated doc.
4. **Single-character OCR strings flood visual queries (recorded, not
   fixed).** "find the shaky handheld footage" returns five OCR rows
   reading "a (", "N", "D", "D", "ITHE" ahead of the true camera
   window, on the text path and the unified path alike - the
   both-directions substring match fires on any shared letter. Owner:
   the OCR lane in `library/tools/analysis/footage_query.py`. That is
   why every text backend scores 0.00 on shot types while the video
   chunks (no OCR prepend) reach 0.67.

Other measured facts. Fusion with BM25 HURTS on 001
(unified-dense 0.714 > unified-hybrid 0.661; lexical-only is the best
001 text config at 0.804): word overlap ("birthday" in clip_011,
"cup"/"dog"/"baby" neighbours) promotes wrong windows above dense-best
ones. Abstention 0.000 on 001 for every hybrid backend is that same
mechanism - the dense floors do reject (cake's best transcript cosine
0.73 < 0.79 floor) but `lexical > 0 | ocr > 0` retains; on geo, where
absent words truly never occur, abstention is 0.875-1.000. Timestamp
localization favours exact segments (0.78) over 8 s windows (0.62):
a 19 s gold span can never fully fit an 8 s window. Expressions have
no lane: E02-E04 (laughing/crying/smiling) return neighbours, never
evidence. OCR and expression per-hour extraction costs remain
unmeasured (no timing ledger in the 001 artifacts; measuring needs an
isolated copy run).

## Verdict

Keep `minilm` hybrid as the default. The unified backend ships opt-in
and stays opt-in: lower recall (0.714 dense / 0.661 hybrid vs 0.768),
coarser localization, ~150x build cost, ~3x memory, no better
abstention where words overlap - with the two recorded defects as the
priced follow-ups (per-row text anchors; OCR match specificity).
Revisit when those land and the set re-scores. On speech-only geo,
EG2-text-dense (0.762) edges MiniLM-hybrid (0.714) inside noise -
not a switch either.

Raw rows: `2026-10-09-unified-multimodal-results.json` (613
query-runs). Build ledger: `2026-10-09-unified-multimodal-builds.jsonl`.
