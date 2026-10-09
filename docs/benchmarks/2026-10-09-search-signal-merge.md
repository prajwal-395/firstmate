# MiniLM search signal merge bakeoff

**Measured 2026-10-09. Verdict: keep `--signal-merge` opt-in.** The candidate
matches recall in the 001 control replay but loses precision, and the shot-type
category does not improve. It does not meet the launch brief's default-switch
gate.

## Candidate

The opt-in MiniLM hybrid path adds direct matching against measured camera-row
facets and returns precise OCR appearance spans when a query matches tracked
text. OCR token matching uses the shared round-2 specificity rule in
`footage_query.py`. The default path remains unchanged, and neither path needs
EmbeddingGemma.

Enable the experiment with `ren search <project> "<query>" --signal-merge`.
The Python `FootageIndex.search` and `search_report` methods take
`signal_merge=True` for the same opt-in.

## Method

Used the committed 100-query set, `2026-10-09-unified-multimodal-queries.json`,
and the round-2 MiniLM index files in `/tmp/fm-vep-unified-r2/indexes`. Both
control and candidate ran through `FootageIndex.search_report`, MiniLM hybrid,
top 5, with the round-2 1-second overlap tolerance. Recall counts a query only
when every gold span has a hit. Precision counts returned rows that hit a gold
span among present queries. Query embedding used the index's declared
Sentence-Transformers MiniLM backend. These runs read the scratch indexes and
did not write to either project corpus.

The round-2 raw rows and report are the historical comparison. The checkout
contains the query set and raw result JSON, but no executable round-2 scoring
harness, so the scoring contract was reconstructed from the report. All 11
round-2 configurations were re-run against the available frozen indexes. The
001 control replay did not reproduce several recorded scores; both sets are
shown so the discrepancy is visible. The GEO control reproduced the recorded
recall and precision.

## Results

| 001, 56 present queries | Round-2 recorded MiniLM hybrid | Current control replay | Signal-merge opt-in |
|---|---:|---:|---:|
| Recall@5 | 0.821 (46/56) | 0.857 (48/56) | 0.857 (48/56) |
| Precision | 0.422 (100/237) | 0.447 (106/237) | 0.384 (91/237) |
| Shot-type recall | 0.83 (5/6) | 0.83 (5/6) | 0.83 (5/6) |
| On-screen-text recall | 0.71 (5/7) | 0.86 (6/7) | 0.86 (6/7) |
| Absent-query abstention | 0.667 (10/15) | 0.667 (10/15) | 0.667 (10/15) |

On geo-podcast, control and opt-in both scored 0.714 recall (15/21), 0.398
precision (39/98), and 1.000 abstention (8/8). No camera or OCR signal was
available there, as expected.

The full 11-configuration replay is below. Metrics are recall@5 / precision;
abstention is shown for the current replay.

| Configuration | Round 2 recorded | Current replay | Replay abstention |
|---|---:|---:|---:|
| minilm-hybrid @001 | 0.821 / 0.422 | 0.857 / 0.447 | 0.667 |
| minilm-lexical @001 | 0.696 / 0.380 | 0.732 / 0.395 | 0.467 |
| eg2text-hybrid @001 | 0.839 / 0.360 | 0.875 / 0.375 | 0.533 |
| eg2text-dense @001 | 0.750 / 0.276 | 0.786 / 0.290 | 0.533 |
| eg2chunks @001 | 0.161 / 0.342 | 0.161 / 0.342 | 1.000 |
| unified-hybrid @001 | 0.804 / 0.260 | 0.804 / 0.265 | 0.667 |
| unified-dense @001 | 0.607 / 0.442 | 0.625 / 0.452 | 0.933 |
| minilm-hybrid @geo | 0.714 / 0.398 | 0.714 / 0.398 | 1.000 |
| minilm-lexical @geo | 0.667 / 0.404 | 0.667 / 0.404 | 0.875 |
| eg2text-hybrid @geo | 0.667 / 0.376 | 0.667 / 0.386 | 0.875 |
| eg2text-dense @geo | 0.762 / 0.386 | 0.762 / 0.386 | 0.875 |

The candidate's recall matches the current control, but precision falls from
0.447 to 0.384 and below the required 0.422 floor. Shot-type recall stays at
5/6. The recorded round-2 comparison also has no shot-type improvement. The
OCR category is 6/7 in both current runs, so the candidate adds precise spans
without increasing category recall on this available index.

## Disposition

Keep the feature behind `--signal-merge`. It fails the precision gate against
both the current control replay and the round-2 recorded floor, and fails the
required shot-type improvement. The source of the 001 control discrepancy
should be resolved before using later scores to change the default.

The round-2 baseline reference is
[`2026-10-09-unified-multimodal-index-round2.md`](2026-10-09-unified-multimodal-index-round2.md),
with raw rows in
[`2026-10-09-unified-multimodal-round2-results.json`](2026-10-09-unified-multimodal-round2-results.json).
