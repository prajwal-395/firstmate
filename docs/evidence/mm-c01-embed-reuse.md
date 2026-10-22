# C01 - Per-frame visual embedding reuse: before/after measurement

Lane mm-c01-embed-reuse. Acceptance: "Adding one clip does not require
re-embedding every unchanged clip."

## 1. What was built

`library/tools/analysis/frame_embedding_cache.py` (new): one source's
CLIP vectors filed beside its own M2 sample
(`<digest>/clip_frame_embeddings.npz`), keyed by the C05 vocabulary -
content digest, model plus weights revision (read off the model
cache's `refs/main`, no load), preprocessing recipe (measured once off
the live processor and pinned), embedding configuration
(pooler-output + L2, batching changes nothing per image),
transformers version (read off distribution metadata, no torch
import), and this lane's version. A moved component is a MISS, never
a false hit. Each row is keyed by the frame's own identity
(timestamp, source frame index, content hash of the decoded file), so
changed bytes miss even at an unchanged timestamp.

`build_frame_index` now looks each selected frame up in its source's
filed generation and encodes only the misses, inside the heavy-work
grant (guard refusal waits and retries); a full-hit rebuild imports
no torch, takes no grant, and encodes nothing. The project matrix is
reassembled in selection order, byte-identical to a full encode when
the hits cover it. Reads go through the project read root (the scope
when the project has one, the machine store when it does not - the
fallback every other frame consumer uses), so the cache shares where
the sample is shared. Rows the current sample no longer answers for
are pruned on the way in (vanished frames and superseded bytes),
and both the project matrix and the project document land
atomically (temp file plus rename), so a killed build leaves the
previous generation, never half a file. Side fix in the same edit: legacy (unscoped)
projects previously rebuilt to all-skipped under scoping, because the
M2 reads resolved through the write root; the stored lucie index
could not be rebuilt at all before this change (measured: scoped
`read_m2` returns None for all 7 digests).

## 2. Before/after, one real project

Corpus: GEO-vs-SEO podcast, 7 MXF cameras, 1092 sampled frames, held-out
set `library/tools/editorial_retrieval_heldout_set.json` (13 visual
queries: 6 present, 2 absent, 1 refusal, 4 unlabelled). The captain's
`lucie/geo-podcast` project was READ (catalog, media stat, M2) and is
asserted unmodified below; rebuilt indexes landed in `/tmp/c01`.
"Before" is the stored index (full encode, old code); "cold" is the
new code with an empty cache; "warm" is the new code reusing it.

| | Before (stored) | Cold (new code, empty cache) | Warm (new code, reuse) |
|---|---|---|---|
| Frames embedded | 1092 (full encode) | 1092 | **0** |
| Frames reused | 0 | 0 | **1092** |
| Encode wall seconds | - (not recorded at build) | 1046.3 | **0.0** |
| Total build wall seconds | - | 1064.7 | **1.3** |
| Matrix vs cold | - | - | bitwise identical (max abs diff 0.0) |
| Matrix vs stored | - | max abs diff 0.0035, same frame order | max abs diff 0.0035, same frame order |

The 0.0035 drift against the stored matrix is cross-run CPU
nondeterminism (same model, same revision `32bd6428`, same recipe,
same order); §3 shows it moves no ranking.

Adding one clip (redirected memory root holding copies of two
digests' M2 samples, second project, no shared-memory writes):

| | One clip (clip_004) | Add clip_005 |
|---|---|---|
| Frames embedded | 22 | **29 (clip_005's only)** |
| Frames reused | 0 | **22 (clip_004 untouched)** |

## 3. Recall on the held-out set, before vs after

Coarse-tier replay (`search_ranges(temporal=False)`, shipped pool 200,
per-source 30, shipped query floor), scored with the sanctioned
`score_editorial` over the 13 visual queries. The temporal pass is a
separate lane's (R06/VI-014): reuse changes only the matrix, and the
replay compares the tier the matrix decides. Per-query rows are
identical across all three indexes.

| | Before (stored) | Cold | Warm |
|---|---|---|---|
| Macro recall (6 present) | 0.0388 | 0.0388 | 0.0388 |
| Mean region recall (6 present) | 1.0 | 1.0 | 1.0 |
| Absent abstained (2 absent) | 2 | 2 | 2 |

Per query (recall, region recall): H01 (0.0233, 1.0), H02 (0.0233,
1.0), H03 (0.0455, 1.0), H04 (0.0476, 1.0), H05 (0.0476, 1.0), H06
(0.0455, 1.0) - identical on all three. The numbers reproduce the
shipped reading in `2026-10-21-editorial-retrieval-candidate-pool.md`
(visual recall 0.0388, region recall 1.0), so the replay measures the
same thing. Recall did not drop: nothing was reverted.

## 4. Untouched, verified

* The captain's project: no file under it was created, modified, or
  deleted except append-only source-memory read telemetry in
  `pipeline_output/logs/event_log.jsonl` (the engine's read path
  emits a cache-hit metric on any M2 read, searches included; same
  class of line any `ren search` leaves). No media file was written.
* New shared files, by design: one `clip_frame_embeddings.npz` per
  digest beside its M2 sample (67 KB-1.4 MB each); additive, ignored
  by every existing reader, invalidated by key on any future model or
  recipe change.
* Other lanes' projects: never opened for write.

## 5. Tests and lint

18 defect-naming tests in
`tests/unit/picture/test_frame_embedding_cache.py` (second build
encodes nothing; added clip encodes only its frames; changed bytes
re-encode one frame; stale key re-encodes and names the component;
corrupt cache misses; full hit builds with no model; grant taken on
miss, not on hit; pin accepts/drift refuses; key shape; identity
shape and slot roundtrip; prune drops vanished and superseded rows;
sampling change encodes only the delta; source replacement encodes
the new bytes and leaves the old generation byte-identical; lane-
version bump re-encodes everything as `analysis_version`; shrunk
sample prunes on the next store; failed store keeps the old
generation and litters no temp file) - all pass, no model, no real
project. Existing
`tests/unit/picture/test_footage_query.py` (133 tests, incl. the
frame-lane builds whose M2 stubs now carry real frame bytes, since
identities hash them) and `test_cache_keys.py` pass; the isolation
guard now covers the new module. `ruff check` with
`ruff-ci-gate.toml` is clean on all four touched files.

## 6. Reproduce

Driver (scratch, uncommitted): `/tmp/c01_measure.py` is lane-local;
the commands, in order: `python3 /tmp/c01_measure.py cold` (17 min
CLIP encode under the heavy-work grant), `warm`, `add`, `recall`.
Equivalence and recall comparisons are pure numpy plus the CLIP text
encoder and re-run in minutes.

## 7. Folded in from PERF-01, and what remains

The execution plan's asks, one by one: remove stale frame rows -
done (prune on store, §1, tested at unit and build level); atomic
updates - done (temp-plus-rename for per-source generations and for
both project files); recovery from an interrupted build - done (a
torn file reads as a miss, a failed store keeps the old generation,
both tested); source replacement - done and tested (new digest
encodes fully, old generation untouched); frame-sampling changes -
done and tested (cadence change encodes only the delta);
model upgrades - done and tested (version bump misses everywhere
with `differing: [analysis_version]`); incremental-vs-full
equivalence - done (§2 cold-vs-warm bitwise, §3 identical recall).

Remains: temporal-tier (`temporal=True`) replay - deliberately not
run, the reuse changes only the matrix and the coarse reports are
identical to the row, so a VLM re-verdict would price model
nondeterminism as lane signal; ANN / vector-database rewrites -
none, per the architectural rules (lookup is a dict get per frame
plus one batched encode); per-scope deduplication - a scoped
project holding its own M2 copy files its own generation, sharing
follows the sample as designed. The `ren search-index` pipeline
path inherits all of this unchanged (same `build_frame_index` entry
point); it was not re-measured separately.
