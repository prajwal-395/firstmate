# The evidence an approved project decision still cites

Code: `library/tools/decision_evidence.py`.
Tests: `tests/unit/context/test_decision_evidence.py`.

The punch list's **VI-042**: "Prevent cache cleanup from deleting evidence
still referenced by approved project decisions."

## What was already protected

`caption_asset_gc.py` protects the Resolve timelines and the pipeline's
own step records and manifest. `workspace_gc.py` protects the
source-memory digests the catalog and the runner's recorded
fingerprints name, and keeps the newest qualification runs. The
project's declared inputs are protected by the layout itself.

None of those is an APPROVAL. A clip that leaves the catalog stops being
protected by the catalog, and the measurements of the footage an
approved reel was cut from become collectable - which quietly
invalidates the decision that cites them.

## What an approval is

Three kinds, and one enumeration:

- a **proposal moment** the captain approved
  (`reel_proposals_v2.json` carries `approval: "approved"`): it names
  the raw footage it plays, as `source_spans` -
  `{source_file, source_start, source_end}`;
- a **Project Model edit** that is evidence-backed: it carries
  `evidence_ids`, and the observations it names carry the asset whose
  bytes they were measured from;
- a **signed-off reel**: the captain approved that reel as it stands.
  Its source footage is the approved moments it was built from, so it
  is covered by them; the timeline it lives on is covered by the
  Resolve roots.

A PROPOSED moment cites nothing. A proposal is a question, not a ruling.

## The rules

**An approval that cannot be read is a refusal, not an absence.** A
decision record that exists but cannot be parsed is reported under
`unreadable`, and `cited_digests` raises rather than coming back short.
A collector that cannot read the approvals must not collect the
evidence they protect, so `workspace_gc` refuses the whole collection
when the approvals cannot be read.

**Identity, not a name.** A cited source file is located by its PATH
(the decision says which file it played) and identified by its CONTENT
DIGEST. The digest is what a collector matches on, and a path that
cannot be fingerprinted is still a path that must survive.

## Where it is wired

`workspace_gc._referenced_digests` adds the approved decisions' digests
to the reachability set it keeps, so the source-memory measurements of
that footage are not collected.

## What is still missing

- The CACHE EVICTION path is not gated by the project state.
  `cache_lifecycle.plan_cache_evictions` evicts by cost and by
  retention class, and it does not read the approved decisions, so a
  measurement-layer cache entry for protected footage can still be
  evicted. The eviction reads the artifact lifecycle, and the decision
  reachability is not in it.
- `caption_asset_gc` has no approved-decision root. Its roots are the
  Resolve timelines and the pipeline's own records; a decision that
  cites a project-side artefact rather than a media file is not
  covered.
- A signed-off reel is covered through the approved moments it was
  built from. A reel signed off whose proposal file was since deleted
  is not.
