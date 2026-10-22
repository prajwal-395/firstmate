# C02 - incremental text indexing (footage search)

Lane: `mm-c02-incremental-text`. Code: `library/tools/analysis/footage_query.py`.
Tests: `tests/unit/picture/test_incremental_text_index.py` (8 tests).

## What changed

The text lane of `build_index` is incremental. Every stored row now
carries `stable_id` (`clip_id#kind#start-end`, the span a transcript
correction preserves rather than the `#NNN` position an insertion
renumbers), `text_hash` (sha256 of the stored words), and
`text_cache_key` (the C05 `observation_key` signature: clip content
digest, embedder model, words, lane-plus-cutter version). A rebuild
matches the fresh cut against the stored rows - same id with same
words first, then same words under any id (renumbered but untouched)
- and re-embeds only what matched nothing. Stored rows whose stable
id is gone from the new cut are dropped, never carried forward. The
BM25 half needs no persisted statistics: `FootageIndex.bm25` is
rebuilt from the stored segment texts on every query, so it always
reflects the rows on disk; the payload records `lexical_docs`
beside them so the two can be seen to agree.

`build_index(..., clip_ids=[...])` (CLI: `build --clip`, repeatable)
scopes the refresh to named clips: their rows are re-cut while every
other clip's stored rows are kept verbatim. Without it the whole cut
refreshes but vectors are still reused row by row.

A build that cannot reuse says why (`text_index.reason` / stats
`incremental_reason`): no previous index, pre-incremental rows with
no per-row hashes (full rebuild, once), embedder or backend changed,
matrix missing or misshapen, or lexical-only (no embedder).

## Measurement (2026-10-22, `/tmp/mm-c02-measure/measure.py`)

Synthetic 12-clip project with real ingest shapes (1.02 catalog,
1.03 profiles, 1.04 regions): 300 speech + 120 description/chunk
rows = 420 segments. Stub embedder sleeps 3 ms/text as the MiniLM
batch-cost stand-in (no model loaded); identical sleeps on both
paths. One transcript correction on one clip (utterance 7, clip_005).

| path | rows embedded | wall |
|---|---|---|
| A: full rebuild from scratch | 420 | 1.915 s |
| B: incremental from v1 | 2 (the utterance + its chunk window) | 0.378 s |

418 rows reused, 0 removed. Embedding matrices byte-identical;
`search_report` top-5 identical on 5 queries (dense + lexical
scores). The residual 0.378 s is cut + payload write, not encoding:
encoder calls fall 210x.

## Acceptance

"A transcript correction updates only affected records and their
necessary index metadata": the correction re-embedded exactly the 2
rows whose words changed (utterance + chunk window joining it) and
rewrote the payload/matrix metadata; all other vectors are the
stored ones, bit for bit.

## Left undone

- Video-chunk (`chunk_embeddings`) and unified-anchor lanes are still
  full-rebuild; the lane that burns MiniLM time at query scale is
  the text lane this punchlist item names.
- `ren search-index` does not yet forward `--clip` (module CLI does);
  the ren verb passes no extra flags today.
- No migration writer for pre-incremental indexes: the first rebuild
  after this lands is a full one, reported as
  "pre-incremental build", and every build after it is incremental.
