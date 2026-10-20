# Share the source intelligence another project measured

Code: `library/tools/cross_project_evidence.py`.
Tests: `tests/unit/context/test_cross_project_evidence.py`.

The punch list's **VI-041**: "Support explicit opt-in sharing of reusable
footage knowledge between projects."

## What was already there

- `cross_project_policy.json` in a project root, declaring a stable
  `project_id` and the projects it grants access to. Absent
  declarations mean private; malformed ones refuse the query.
- `model_evidence_provenance.reuse_evidence`: the gate. It checks the
  mutual grant, the licence, the edition and the workload purpose,
  refuses unknown output terms, and appends every decision to an
  append-only audit.
- `project_model_store.merge_evidence_records`: the compare-and-swap
  merge that puts rows into a project's canonical model.

## What was missing

The middle. The gate had no way to be handed ANOTHER PROJECT's
evidence, because nothing ever read it out of one. A project could
share its editorial graph (`project_graph/cross_project.py`) and its
model-derived evidence could be reused inside one project
(`project_model_acquisition.py`), but the intelligence one project
measured never crossed to another.

`ren share <target> --from <source>` is that crossing.

## The rules

**The opt-in is checked BEFORE the source is read.** A project that has
not granted access is not opened: the request returns without touching
the source's model, and the report says the grant is absent. A share
that is only audited after the fact is not an opt-in.

**The gate decides, this module does not.** Every model-derived row
passes through `reuse_evidence`, which is the only thing that knows
what a licence permits for a given edition and purpose. There is no
second path around it: a row the gate refuses is refused here, with
the gate's own reason.

**Deterministic measurements are not in this path.** A row with no
model provenance has no licence record, and the gate fails closed on
those. That is the right answer for a model-derived row and the wrong
one for a deterministic measurement - but the deterministic measurements
are already shared machine-wide by the per-source memory, keyed by
content digest, so excluding them here excludes nothing that is not
already available. What this path adds is the part that was NOT
shared: model-derived intelligence, gated and audited.

**The import is a merge, not a copy.** The rows go into the target's
canonical model by stable id, under a compare-and-swap against the
target's current revision. The target must already hold the asset and
the clock the rows sit on - the ids are derived from the content
digest, so a target with the same footage has them. A target without
them is told, rather than being given rows that would make its model
invalid.

## The surface

    ren share <target-project> --from <source-project> [--digest D] [--dry-run]

`--dry-run` decides and reports without importing. Every decision is
audited in the TARGET's `project_model/model_evidence_reuse.jsonl`.

## What is still missing

- The deterministic measurements - the transcript, the scene
  observations, the quality profile - are shared IMPLICITLY, by the
  machine-wide per-source memory keyed by content digest. Making THAT
  explicit would mean every slot recording which project produced it,
  and every reader checking the grant. Nothing here changes it.
- A project cannot see what it has already shared. The audit is
  append-only and append-only is not a view.
- The share selects by content digest or not at all. There is no query
  for "the scene observations of this clip", only "everything this
  project measured about this footage".
