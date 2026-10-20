# Invalidate only what moved

Code: `library/tools/analysis/footage_query.py`,
`library/tools/analysis/footage_segments.py`,
`library/tools/evidence_lifecycle.py`.
Tests: `tests/unit/picture/test_footage_query.py`,
`tests/unit/context/test_evidence_lifecycle.py`.

The punch list's **VI-040**: "Invalidate only affected analysis when media
or analysis versions change."

## What was already there

- `evidence_lifecycle.py` had the DETECTION: `check_artifacts` compares a
  derived artifact's record against the live model and returns
  `stale_source` / `stale_model` / `stale_producer`.
- The source memory is per content digest, and `measurement_layers`
  caches each measurement layer per source digest, so re-measuring after
  a source changed already cost only that source.
- The footage index recorded the whole-ingest fingerprint and could say
  THAT something moved.

## What was missing

**The registry was dead code.** Nothing in `library/` ever called
`evidence_lifecycle.register_artifact`, so `check_artifacts` answered
with an empty report for every project. A registry nobody writes cannot
say that anything went stale.

**"Something moved" is not "this moved."** The index's fingerprint is one
digest over the whole ingest. A reader who learns "an ingest file
changed" re-measures the project; a reader who learns "clip_007 changed"
re-measures one clip. The index now records the per-source content
digests it was cut from, and the staleness verdict names the clips whose
digest moved, the ones that went away and the ones that are new.

**An index is analysis as much as data.** The rows in an index were cut
by a particular version of the code that cuts them, and nothing about
the media moving said so. The payload now records `segment_method`: the
content hash of the modules that decide what a segment is
(`footage_segments`, `source_quality`, `subject_framing`,
`camera_motion`, `source_memory`). Content, not mtime. The list is
curated rather than derived - walking the AST closure of the cut reaches
the whole engine, and an identity that takes minutes is not a staleness
check. It over-invalidates a little on purpose: the cost of a false
stale is a rebuild, and the cost of a false fresh is a silently wrong
index.

**An artifact that does not read the Project Model was judged as if it
did.** `register_artifact` now takes `model_revision=None` for an
artifact built from the media and the ingest output, and `check_artifacts`
skips the model-revision check for it. A search index is not stale
because the model moved.

**The report named nothing.** Every verdict now carries `stale_sources`
(the digests the artifact was built from that are no longer live) and
`new_sources` (the live digests it was never built from), so "only the
affected" is a fact in the report rather than a principle in a
docstring.

## The surface

    python3 -m library.tools.evidence_lifecycle status <project>
    python3 -m library.tools.analysis.footage_query stale <project>

`evidence_lifecycle status` prints the project's derived artifacts and
which of them are stale. A project that has derived nothing answers with
an empty report, which is a fact and not an error.

## What is still missing

- The registry is still mostly unwired: the project-graph cache is its
  first real registrant. The footage index deliberately does NOT register
  itself, because `build_index` writes only into the index directory and
  a build that touched the project would break that contract. The index's
  own payload carries what the registry would have carried.
- A stale index is still rebuilt whole. Rebuilding only the clips whose
  digest moved would be the next step, and the per-source digests are
  what it would key on.
- `ren status` does not read the derived registry. It would have to
  fingerprint the media to judge it, and that is too expensive for a
  status read.
