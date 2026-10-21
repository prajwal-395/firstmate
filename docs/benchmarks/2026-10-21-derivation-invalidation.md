# Derivation-aware invalidation, measured on a real Project Model

**Lane:** BL-6 (producer provenance, derivation-aware invalidation, opt-in
cross-project sharing) and defect D3.
**Punch list:** B-06, B-13, B-15.
**Measured on:** a Ren-owned scratch copy of `ren-scratch-breaktest` (seven
source files, 116,059 observations). No captain project, timeline or media
was touched; the model store was copied to a scratch directory, measured
there, and the copy was discarded afterwards.

## The gap, measured

The audit that named this lane measured the Project Model of that project
and found:

| What | Before |
|---|---|
| `provenance` rows with `producer_version: null` | **34 of 35** |
| Observations carrying a derivation digest | 111,667 of 116,059 |
| Model rows that depend on an observation (events, interpretations, identity links) | **2,351** |
| A way to mark a dependant stale when its observation is revised | **none** |
| `derived_artifacts.json` in the project | **absent** |

The last two are the same defect seen twice. The model's citations say
what a row rests on, but nothing recorded the DIGEST a dependant rested
on, so a revision had nothing to be different from. A revised detector box
left 2,351 rows silently resting on evidence that no longer existed, and
nothing in the model said so.

## The change

**The edge is the citation; the digest is what makes it checkable.** An
observation carries `payload.evidence_contract.derivation.derivation_digest`
- the digest of the derivation that produced it. An event, an
interpretation and an identity link cite the observations they rest on.
`library/tools/derivation_staleness.py` reads those two facts together and
writes the derivation registry (`project_model/derivation_state.json`)
beside the model: the edges, the digest each cited observation had when the
edge was recorded, and the stale marks.

The registry is a projection, in the same sense `reel-projection.json` is:
the model stays canonical, and marking stale writes no model row at all.

**Producer provenance is stamped, never null.** The migration stamps each
provenance row's `producer_version` from the version the lane's own record
carries. A lane written by the current engine carries its recorded version;
a lane whose slot predates the envelope is stamped `unknown`, which says
the version was not recorded. A null said nothing at all.

**Model-derived observations carry their licence.** The generation record's
verdict - implementation, checkpoint, checkpoint revision, approval status,
obligations, workload purpose - is copied onto the observation's payload
(`license_context`, `checkpoint_revision`) so the row a reader holds names
the model that produced it.

**The derived-artifact registry is written.** The graph cache was already
declared; the footage index and the temporal-event index are declared now,
each by its owner, so a project that derives anything has a registry that
says what it derives and what it was built from.

**A candidate link may carry no support.** D3: `reassociate_tracks` could
not express "a proposal with no evidence behind it yet", because the
endpoint observations were always added as support. An explicitly empty
support list is now recorded as such; only a VERIFIED link must carry
support, and the Project Model validator refuses a verified
re-identification without it.

## The measurement, after

Reproduce with:

```
python3 scripts/benchmark_derivation_invalidation.py <scratch-project>
```

The script revises ONE detector box in the project's canonical model, marks
the dependants stale, and prints what moved and what did not.

```
model:
  observations:                              116,059
  provenance_rows:                                35
  provenance_rows_without_a_producer_version:      34   (the model as it stands:
                                                  built before this change)
  observations_with_a_derivation_digest:     111,667
  dependant_rows_with_an_edge:                2,351
    events 182 | identity_links 2,154 | interpretations 15

revised_observation:
  obs-...-m3b-0170   modality face   box [682.7, 176.9, 787.1, 322.4]
  dependants_before_revision: 1

event_log:
  revision_before: 20   revision_after: 21   events_appended: 1
  collections_touched: ["observations"]   rows_touched: [obs-...-m3b-0170]

invalidation:
  stale_count: 1
  stale_by_collection: {"identity_links": 1}
  untouched_dependants: 2,350
  model_rows_untouched: 12,568
```

The event log grew by exactly one event, touching exactly one row: the
revised observation. Every other model row was carried, not rewritten.
The one dependant that rests on that box - the identity link that claims
the face belongs to that person - is marked stale, and the other 2,350
dependants are untouched. This is the acceptance gate B in numbers: "if
one piece of evidence is revised, dependent knowledge is updated or marked
stale without corrupting unrelated data".

**Producer provenance, measured on the same footage.** Re-running the
migration over the copied project with this change:

| | Before | After |
|---|---|---|
| provenance rows | 34 | 34 |
| `producer_version: null` | 34 | **0** |
| `producer_version: "unknown"` (the slot recorded no version) | 0 | 33 |
| `producer_version` read from the lane's own record | 0 | **1** |

The one row carrying a real version is the M1 lane of the digest whose
slot was rewritten through the engine's own writer
(`source_memory.write_json`), which stamps the evidence envelope. That is
the whole shape of the fix: the producer records a version at write time,
and the migration carries it. The legacy slots in this project predate the
envelope, and `unknown` is the truth for them.

**The derived-artifact registry, measured on the same project.** Before:
`project_model/derived_artifacts.json` absent. After the model link
declares what the project derives:

```
python3 -m library.tools.evidence_lifecycle status <scratch-project>
  temporal_event_index: status current, model_revision 21,
  seven source digests, rebuild library.tools.temporal_events:build_temporal_events
```

## What this measurement does NOT say

- It is one project, one revision, one detector box. The counts are that
  project's counts.
- The invalidation marks a dependant STALE. It does not re-derive it: the
  deriver re-runs and `refresh` resolves the mark when the row's digests
  move again. "Marked stale" is the honest half of B-13; "re-derived" is
  the deriver's own work.
- The revision was made by the benchmark script through the Project Model
  store, so it is a real revision with a real event. It is not a detector
  re-run: no model was re-measured.
- Cross-project sharing was NOT exercised end to end. It needs two
  `cross_project_policy.json` files, one per project, and only the captain
  can approve a pair. See the open decision.
