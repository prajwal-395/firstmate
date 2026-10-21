# The cross-project share path, exercised end to end on Pair A

**Lane:** BL-6's opt-in sharing path (B-15), exercised for the first time.
**Measured on:** 2026-10-21, on a Ren-owned scratch pair.

## The pair

Cross-project sharing happens only per a pair the captain approved, and
nothing by default. On 2026-10-21 the captain approved **Pair A only**:

| | Project | Role |
|---|---|---|
| Source | `ren-scratch-breaktest` | holds the measured Project Model (116 059 observations) |
| Target | `ren-scratch-share-pair-a` | a Ren-owned scratch copy of the same seven source files |

The target is a copy of the source's seven recordings (APFS clones: same
bytes, same content digests, the originals never opened for writing), its
own catalog, its own fingerprints and its own Project Model holding the
seven assets and seven clocks its own footage declares. The captain's
project and the captain's media were not touched.

Two `cross_project_policy.json` files were written, one per project,
each naming the other and nothing else. **Pair B
(`ren-scratch-retrieval-bench`) was not approved and has no policy.**

## What the first run measured

`ren share <target> --from <source>` over the source's whole model:

| | Before this lane | After |
|---|---|---|
| Opt-in | mutual (both policies agree) | mutual |
| Candidate rows | 116 059 | 116 059 |
| Rows shared | **0** | **4** |
| Rows refused | 116 059 | **116 055** |
| Audit lines | one per gate decision | one per decision, gate and share alike |
| Wall clock | ~30 min (one audit flush per row) | **42 s** |

**Why nothing shared.** Every row the engine had measured with a model
carried NO licence record. The lane recorded which model made the
reading (the M5 slot's `method`), and the registry recorded what that
model's licence permits, but nothing joined the two - so the reuse gate,
which fails closed on a row with no record, refused every row,
including the ones measured with a commercially reusable model.

**What shares now.** The four sound-event rows the M5 lane measured
with PANNs (`panns.cnn14_decision_max`) carry a generation record and
the registry's own verdict:

```json
{
  "action": "reused",
  "source_implementation_id": "panns.cnn14_decision_max",
  "output_reuse_class": "commercial",
  "license_evidence_ref": "license_evidence.primary_evidence:panns_weights",
  "target_edition": "commercial",
  "target_purpose": "commercial_client",
  "reason": "reuse permitted"
}
```

Every other row is refused with the same reason - "the row carries no
model provenance, so no licence record governs it" - and the refusal is
in the append-only audit beside the reuse.

## The audit

`project_model/model_evidence_reuse.jsonl` in the TARGET project. It is
append-only: the last run added 116 059 lines, one per candidate row,
and earlier runs' lines are still there.

```
232 118 lines before this run
116 059 added (one per candidate row: 4 reused, 116 055 refused)
348 177 lines after
```

The gate's decisions and the share's own refusals write the same shape,
so the log is one account: `action` is `reused` or `refused`, and
`reason` says which.

## The defects this exercise named

| The defect | The fix |
|---|---|
| a model-derived row the engine measured carried no licence record, so the gate refused it | `library/tools/model_provenance_binding.py`: the lane's own declaration names the implementation, the registry names the licence, and the record is written into the project's own generation-record store and stamped onto the rows it names |
| a project stamped with the Project Model's own id named a producer the grant does not know | the producer is the project's GRANT id (`cross_project_policy.json`), which is the id a share grants by |
| one generation record copied onto every row it names read as several records governing the same row | identical records are one record; two records that DIFFER and both name one row are still refused |
| a share decided a hundred thousand rows one at a time, flushing the audit once per row | the gate is asked once, for every row, and the audit is one append |
| a refusal that left no line in the audit | the share's own refusals are audited in the same append-only log |
| a model rebuilt from the same measurements lost the licence record | the migration stamps the rows the project's generation records name, so a rebuilt model lands on the same rows |

Each is named by a test in
`tests/unit/context/test_model_provenance_binding.py`, and each test
fails on the behaviour it names.

## What is still missing

- **A lane whose measurement does not name its model is not bound.** The
  M4 scene lane records the model only on a window the acquisition path
  measured, so the VLM's own reading of this footage carries no record
  and is refused. That is the safe answer, and it is the answer for
  every model the registry has not cleared for commercial reuse
  (`gemma.vision_4_12b_it_4bit` is `benchmark_only`).
- **A row that merely RESTS on a measured row is not bound either.** The
  cross-modal reports that cite a sound event are produced by a rule
  that read it, not by the model, so they carry no model record and are
  refused.
- **The share is still all-or-nothing per row.** A project that wants
  "the scene observations of this clip" gets everything the gate allows
  for that clip, or nothing.
- **A project cannot see what it has already shared.** The audit is
  append-only and append-only is not a view.
