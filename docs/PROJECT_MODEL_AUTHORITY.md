# Project Model authority boundary

`project_model/events.jsonl` is the canonical revision history for evidence and
the modeled edit/timeline state. `snapshot.json` is a replayable cache.
`pipeline_data.json` remains an orchestration store and carries only the
`project_model_projection` revision and event hash pointer; evidence readers
must load the Project Model event history instead of treating capability
outputs as canonical evidence. Analysis completion records capture the
Project Model head before execution and after the capability returns.

Use `project_model_store.load_project_model` for a verified revision and model.
Evidence updates use `merge_evidence_records` with the revision read by the
writer; a stale expected revision refuses. The evidence-query acquisition path
records both the revision it read and the revision after acquisition in its
result receipt. `parity_report` compares the event head with the snapshot and
pipeline pointer and names the repair action for drift.
The evidence migration command uses `commit_evidence_import`: it creates the
initial model when none exists, then merges evidence collections by stable ID
with CAS while preserving editorial and timeline collections.

Timeline compositions are represented by `timeline_states`, alongside the
existing clip uses, edit operations, and frame maps. The model validator checks
their stable IDs and references. `project_model_editorial.timeline_state_for`
reads the current state, while `commit_timeline_state` writes it with CAS into
the same event history. `edit_patch.apply_live_patch` reads the current model
revision when it constructs a patch; a patch with an absent or stale revision
is refused before Resolve is touched. After application, the same revision is
used as the CAS for an event that records the prepared operations, execution
receipt, Resolve snapshot, source-to-timeline frame mappings, and the timeline
state's edit-operation reference. A concurrent model change makes the write
refuse to overwrite newer model state; the applied Resolve generation remains
in the timeline shadow for reconciliation. The bulk manifest renderer records
the completed live timeline in the shadow, then commits that exact generation
as the Project Model `timeline_states` projection with the model revision read
before Resolve was changed. If a concurrent model writer advances the event
head, only the rendered timeline state is replayed onto the newer head; the
event reason records both the original and conflicting revisions. The shadow
generation is linked to the committed model revision after the model event
lands. Projects without a canonical model retain legacy render behavior until
their model is created.

A clip use's id is derived from the CONTENT it plays - the asset, the role
and the passage - and not from its position in the placement list. A
positional id renames every later clip when one is inserted in front of them,
and because the minting skipped an id it already held, that rename left the
model describing the clip that used to be there. `library/tools/editorial_identity.py`
owns the identity: what a clip IS (the material it plays and the role it
plays it in), as distinct from where it sits and what Resolve calls it. The
Project Model's `use:<reel>:<token>` ids are minted from it; the older
`use:<reel>:0000` rows are still read, because a project built before the
ids became content-derived still carries them.

After every committed mutation the model is reconciled against the live
Resolve timeline. `library/tools/timeline_state_reconcile.py` compares the
two - joining a live clip to the model's clip use on the material and the
timeline extent - and commits what the timeline HOLDS: the `timeline_states`
row naming its clip uses, and the `nle_bindings` row joining each of them
to the live Resolve item that plays it. The comparison is a pure function of
the model and the read, so it runs against a recorded generation with no
Resolve connection, and an extent that cannot be placed on the model's ticks
is UNAVAILABLE rather than a disagreement.
