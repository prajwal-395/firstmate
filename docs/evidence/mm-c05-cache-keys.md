# C05 - Explicit cache keys: before/after measurement

Lane mm-c05-cache-keys. Acceptance: "Cache reuse is predictable and never
silently returns semantically incompatible results."

## 1. Distinct key constructions (in-scope caches)

In scope (brief): `analysis_cache` (PR 3141), the frame cache, the
detector cache (`object_grounding.analysis_key`, built on the analysis
cache), the scene-window cache (`measurement_layers.LayerCache`).

| # | Builder (before) | Components | Built through `cache_keys` before |
|---|---|---|---|
| 1 | `analysis_cache.AnalysisKey` | source, model, configuration digest, analysis version | no (owned the vocabulary) |
| 2 | `measurement_layers.layer_key` | format, layer, source digest, method digest, params | no (own `json.dumps` call) |
| 3 | `shared_decode._key` | `sha256(digest \| recipe.signature)` | no |
| 4 | `video_features.cache_key` | path, byte_size, mtime_ns, interval, model, precision, adapter version | no |
| 5 | vision frame `cache_subdir` | policy_version + sample times (adaptive) / nothing (fixed interval) | no |
| 6 | lifecycle `producer_key` strings (4 ad-hoc formats) | digest + filename, no model, no version | no |

After: 6/6 in-scope construction sites build through
`library/tools/cache_keys` (1 module, 1 component vocabulary:
source_identity, model, configuration, analysis_version; plus the
observation-vs-interpretation kind). Stored keys that were already
complete are byte-identical (analysis keys, layer keys, legacy feature
keys - verified by assertion, see §4); keys that were deficient move
exactly once, to a key that says what they measure. A moved key is a
MISS, never a false hit, so the migration direction is safe.

Out of scope, audited, left unchanged (each already versions what it
measures): `filmcraft_preconform.cache_path` (fingerprint + spec key +
policy version), `person_entity` voice/face keys (payload includes
code digest, model pack, thresholds), `native_overlay_media` key
(content sha + span + rate + schema). `source_memory` slot
`producer_key`s are file-slot identities for lifecycle registration,
not reuse keys - unchanged.

## 2. Incompatible configurations sharing a key

| # | Collision (before) | Consequence | After |
|---|---|---|---|
| 1 | shared decode: same recipe after an ffmpeg-command change | stale frames served silently (no command version anywhere) | `DECODE_PRODUCER_VERSION` in key AND manifest; `_read_manifest` refuses another version's entry |
| 2 | feature store: same path + mtime, replaced bytes | old file's tensors served for the new file | content-addressed arm (`source_digest=`): same bytes one key whatever the path; different bytes different keys |
| 3 | frame cache: fixed interval 5 s vs 10 s | one shared directory, frames overwrite/misread under the same names | interval in the namespace (`fixed_<digest>`); every plan gets its own subdir |
| 4 | frame cache: same policy + times after an extractor change | another command's pixels served | `FRAME_PRODUCER_VERSION` + extraction params in the subdir digest |
| 5 | lifecycle names: same entry after a method change (`{digest}:{layer}:{name}`, `semantic-frame:{d}:{name}`) | re-measure overwrites silently; reachability cannot tell versions apart | `cache_keys.producer_key(kind, source, model, version, extra)` at all three call sites |

Result: 5/5 in-scope collision cases closed, 0 remaining. Deferred (out
of lane scope, named so it is not mistaken for done):
`extract_video_clips` window files are named `prefix_index.mp4` without
the window length; the read path re-cuts on audio/height mismatch but
does not check `window_s`. Every caller passes the `ACTION_WINDOW_S`
constant today, so no two configurations collide in practice.

## 3. Reusable observations vs context-dependent interpretations

`LayerCache` layers classified by `measurement_layers.layer_kind`:
`windows`/`window` are INTERPRETATIONS (their params carry the
transcript slice, temporal boundaries and prompt inputs - a window
measured under one transcript is never served under another);
`objects`/`picture` are OBSERVATIONS (true of the source, reusable
wherever the source is). Enforced in the canonical module:
`observation_key` refuses a payload carrying context keys, and
`interpretation_key` refuses an empty context.

## 4. Proofs (all cheap: digests and manifests, no model, no media)

* `tests/unit/picture/test_cache_keys.py`: 12 tests, each naming one
  defect above (convergence, 4 collision families, 2 stale-refusal
  paths, observation/interpretation boundary, selective invalidation,
  detector-key movement, producer-key versioning). 12/12 pass.
* Neighbouring suites re-run against the changed builders:
  `test_analysis_cache`, `test_demand_analysis_cache`,
  `test_shared_decode`, `test_video_features`, `test_object_grounding`,
  `test_cache_lifecycle` - 65/65 pass.
* Byte-stability assertions (run, not committed): analysis-key
  signatures, configuration digests (incl. non-ASCII payloads), layer
  keys (incl. non-ASCII transcripts) and legacy feature keys are
  identical before/after; shared-decode, digest-arm feature and frame
  subdir keys move exactly as intended.
* `ruff check` clean on all touched files except pre-existing findings
  in `vision_pipeline_v3.py`, identical count at HEAD.

## 5. Selective invalidation

`cache_keys.selective_invalidate(entries, wanted)` splits a store into
the current entry and the stale ones, each stale one naming the
components that moved (`differing_components`). `analysis_cache`
delegates to it, so a stale analysis entry reports WHY it was not used
instead of reading as a miss.
