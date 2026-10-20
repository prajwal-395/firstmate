# Preservation as an execution invariant

The punch list's section 2 asks for ten things of an edit: an authorized
mutation scope, a pre-edit snapshot, an expected semantic delta computed
before the write, an actual delta compared against it, detection of
unrelated changes, refusal when the guarantee cannot be established, and
proof across repeated cycles, human edits, staging and rebuilds.

This file says, per item, what already existed when the lane took it and
what the lane added. The line numbers point at the code, not at prose.

The one module that owns the invariant end to end is
`library/tools/preservation_invariant.py`. It is a small vocabulary:

    scope    - what the edit is authorized to touch
    before   - the state it was authorized against
    expected - the semantic delta that authorization permits
    after    - the state the write left behind
    actual   - the semantic delta that actually happened
    verdict  - preserved, violated, or unprovable

## The unit of authorization is the (target, domain) pair

The engine already decides concurrency this way. A patch declares the
conflict domains it touches (`edit_patch.CONFLICT_DOMAINS`), an
operation's domain is fixed by its type in `edit_algebra.ALGEBRA`, and a
patch that does not declare the domain of an operation it carries is
refused before Resolve is touched (`edit_patch.validate`). A field-level
expectation would need a second table guessing what each of the
eighty-four operations writes into a snapshot whose shape differs per
operation - and a wrong guess there reads as "unrelated change" on
correct work. The pair is what the patch already promised, so the pair is
what is checked.

The observed side is classified into the same domains, so a report says
what moved: `timeline_structure` (clips, rows, timing), `picture_transform`,
`color` (grades), `fusion` (comps), `audio_mix`, `markers`, and
`source_identity` (the media a clip plays).

## Per item

### PRES-008 - Represent each edit's authorized mutation scope

Already: `edit_patch.EditPatch` carries `affected_spans` and
`conflict_domains` (`library/tools/edit_patch.py:180`), the vocabulary is
`edit_patch.CONFLICT_DOMAINS` (`library/tools/edit_patch.py:105`), the
domain of an operation is its type's (`library/tools/edit_algebra.py:207`),
and a patch that says less than it does is refused before Resolve
(`library/tools/edit_patch.py:375`). Preparation computes what the edit
reaches (`library/tools/edit_preparation.py:70`).

Added: `preservation_invariant.scope_for` turns a set of operations into
the authorized `(target, domain)` pairs in ONE place, and says which rows
the edit may bring into existence (a row an insert needs and only Resolve
can number) and which target's media identity it may re-point. Before
this, the scope was spread across the patch, the compiled request and the
adapters, and no two paths read it the same way.

### PRES-009 - Capture a pre-edit snapshot of the relevant timeline and dependent state

Already: the shadow store keeps every timeline Ren has read, with a
generation per change (`library/tools/timeline_shadow.py:1195`,
`:1214`); the replacement guard reads a full editable state
(`library/tools/reel_replace_guard.py:413`); the undo journal records a
before and an after for every touch (`library/tools/undo_journal.py:314`).

Added: `preservation_invariant.pre_edit_snapshot` names the recorded head
as the pre-edit state and refuses when there is none, rather than
proceeding against an empty state.

### PRES-010 - Compute the expected semantic delta before applying the edit

Already: `edit_preview.preview_program`
(`library/tools/edit_preview.py:809`) computes what each op will change
over a recorded snapshot. It is a preview artifact for a human, and it
models a simplified snapshot shape, so it is not the execution's
expectation.

Added: `preservation_invariant.expected_delta` computes the expectation
FROM THE OPERATIONS, before the write, naming the target, the domain, the
operation and the fields that operation declared. The receipt carries it
as `preservation.expected`.

### PRES-011 - Compare the actual post-edit delta against the expected delta

Already: `edit_algebra.verifies` judges each operation's read-back
postcondition (`library/tools/edit_algebra.py:4417`), and
`transaction_orchestration.unrelated_clip_changes` reports changed clips
outside the declared spans (`library/tools/transaction_orchestration.py:192`).
Neither compares a delta against a delta.

Added: `preservation_invariant.compare` reads the actual delta off the
post-write snapshot and compares it against the expected one. It is wired
into the pass condition of
`transaction_orchestration.verify_readback` (the EditPatch path) and of
`resolve_axi_core._verify_registered_full_request` (the AXI batch path),
and into the adapters
(`library/tools/edit_execution_adapters.py:88`), so a write that changed
something it was not told to change is refused, not committed.

### PRES-012 - Detect unrelated changes to clips, audio, tracks, grades, transforms and timing

Already: `unrelated_clip_changes` compares each clip's WHOLE dictionary,
so a grade, a transform, a mix or a timing change on another clip is
caught. It cannot see a ROW's name, a row that appeared or disappeared, a
timeline marker, or the timeline's own duration - a clip-only scan has no
row and no timeline in it.

Added: `preservation_invariant.snapshot_delta` covers the clip fields, the
clip's media identity, the rows (their name and their appearance) and the
timeline's own markers and duration, and classifies each change by domain
so a report names what moved. The verdict is `violated` when a change was
outside the authorized scope.

### PRES-013 - Refuse edits when the requested preservation guarantee cannot be established

Already: `edit_patch.validate` refuses an unknown capability and an
operation whose domain the patch did not declare
(`library/tools/edit_patch.py:375`); `reel_replace_guard.ReplaceGuardUnreadable`
refuses rather than overwrite what it cannot see
(`library/tools/reel_replace_guard.py:483`); `edit_state_preservation`
refuses a state it cannot carry (`library/tools/edit_state_preservation.py:31`).

Added: `preservation_invariant.PreservationUnprovable` and the
`unprovable` verdict, raised when an operation's authorized scope cannot
be determined at all - an operation the algebra does not know has no
domain, so nothing can be told apart from collateral. A domain the
snapshot cannot SEE is reported as `unobservable_domains` rather than
claimed as preserved: the shadow read does not enumerate a caption row,
so a guarantee over captions is named, not asserted.

### PRES-014 - Test preservation across repeated editing cycles, not only individual isolated changes

Already: `tests/scenarios/test_version_control_regressions.py` exercises
multi-act version lifecycles and `tests/unit/reels/test_reel_edit_history.py`
covers twelve classes of repeated act.

Added: `tests/unit/resolve/test_preservation_invariant.py::test_preservation_holds_across_repeated_editing_cycles`
- three sequential edits over one timeline, each cycle's preservation
verdict, and the earlier cycle's approved result proven intact after the
later ones.

### PRES-015 - Verify that human edits made between Ren operations are retained

Already, and not re-done here: `library/tools/editor_edit_carry.py`
carries the captain's edits through a rebuild
(`:676` derive, `:1300` plan, `:2293` carry, `:2371` verify);
`library/tools/reel_replace_guard.py:1090` detects editor changes and
`:1202` refuses an edit that would overwrite them;
`library/tools/timeline_reconciliation.py:73` proves offline that a hand
edit stays the base and a conflicting Ren change becomes a named variant;
`edit_patch.StalePatch` refuses a patch whose base a hand edit moved.
Tests: `tests/contracts/test_timeline_reconciliation.py`,
`tests/unit/resolve/test_editor_edit_carry.py`.

### PRES-016 - Verify that staging, promotion, rollback and reopening do not alter approved content

Already, and not re-done here: `library/tools/versions/store.py` stages
(`:843`), commits a build (`:1255`), records a promotion (`:1357`) and
records the finished timeline (`:1587`);
`library/tools/undo_journal.py` reverses a touch in place (`:2985`) and
rolls a rebuild back to the version it replaced; the checkpoints
(`library/tools/versions/checkpoints.py:133`, `:175`, `:198`) restore a
project and a timeline; `library/tools/versions/reel_versions.py` keeps
every state a reel has held, in order, never overwritten. Tests:
`tests/scenarios/test_version_control_regressions.py`,
`tests/unit/resolve/test_undo_journal.py`,
`tests/unit/reels/test_reel_edit_history.py`.

### PRES-017 - Preserve source media identity and relevant clip metadata throughout rebuilds

Already: `library/tools/reel_replace_guard.py:364` computes a clip's
source identity, `:144` names the fields a snapshot captures, `:156` is
the honest per-property matrix of what Resolve can and cannot report,
`:631` diffs two snapshots and `:897` says whether a change was carried;
`library/tools/edit_state_preservation.py:42` captures and `:141`
restores a replaced clip's enabled state, colour, flags, markers, links
and grade; `library/tools/structural_patch.py:23` keeps a stable
placement lineage across a rebuild.

Added: media identity is its own change class in the delta
(`preservation_invariant.SOURCE_IDENTITY`), so a clip re-pointed at
different media is named as such instead of hiding inside a
`timeline_structure` diff, and only an operation that re-places media may
re-point its own target.

## Live evidence this lane needs

The offline double cannot produce a grade change on an untouched clip, so
the live lane (`vep-axc-live2`) has to confirm against a real Resolve:

1. a trim through the AXI path leaves another clip's grade, another row's
   name and the timeline's markers untouched, and the receipt says
   `preservation.status == "preserved"`;
2. a write that DOES move an unrelated grade is refused by the adapter
   (`edit_execution_adapters._refuse_collateral`) rather than committed;
3. the AXI batch read-back reports `preservation` beside its existing
   `unrelated_items_unchanged`.

## What this lane did not change

The read-back judgement that proves the REQUESTED edit happened is still
`edit_algebra.verifies`, still judged once, on the same snapshot. The
preservation verdict judges unrelated state only, and says so in its
docstring. `unrelated_clip_changes` keeps its own stricter clip-level
reading; the two run over the same two snapshots and the strictest of
them governs.
