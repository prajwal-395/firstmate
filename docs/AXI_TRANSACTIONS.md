# AXI transactions

A request with more than one operation is a transaction. This document says
what AXI does with one, and where each rule lives.

The owner is `library/tools/axi_transaction.py`. It writes nothing to Resolve
and decides no editorial intent; it answers the questions a caller asks about
a multi-operation request, before and after the write.

## The compound shapes

`axi_transaction.COMPOUND_SCENARIOS` is the one place a multi-operation
shape is declared. Each entry carries:

| field | meaning |
|---|---|
| `operations` | the operation ids the shape covers |
| `backends` | the backends with a physical path for it today |
| `target_relation` | `any`, `same_target` or `distinct_targets` |
| `atomicity` | how the shape commits - `single_operation`, `recoverable_sequence` or `single_edit_patch_commit` |
| `recovery` | how it is undone when it fails halfway - `reverse_order_undo` or `per_operation_undo` |
| `qualification` | the predicates under which it is qualified |
| `implemented` | whether the physical path AND its compensation exist today |
| `live_evidence` | the live run that would qualify a shape that does not have one |

A shape whose `implemented` is False is DECLARED, not enabled. The preflight
refuses it before the first write and names the evidence that would qualify
it. Two of the shapes the AXI brief names are in that state today:

- `remove_ripple` - a track-scoped ripple that closes the gap left by a
  removed clip. It needs a live run against a row that holds more than one
  clip, with the removed clip restored and compared.
- `broll_over_linked_audio` - a B-roll cover placed over a clip whose audio is
  LINKED to the video, with the dialogue left audible and the underlying
  clip's transform and grade unchanged.

Both are live-lane work (`vep-axc-live2`). A compound qualification is
recorded the same way a single-operation one is: a
`ren-axi-compound-qualification-result/v1` report naming the scenario and the
backend, imported with the same command. The rows live beside the
single-operation rows in `library/tools/axi_qualification_records.json`, under
`compound_records`, and carry the same evidence rules - a shape is enabled by
measured evidence, never by a declaration.

## The checks before a write

`axi_transaction.detect_conflict` decides two things, both before anything is
written:

- **identity** - the live project and timeline are the ones the request
  names, exactly. A near-miss name is a conflict, not a tolerance.
- **revision** - the recorded generation and the Project Model head are the
  ones the request was planned against.

`axi_transaction.require_recovery_clear` is the AXI-048 gate. While a
transaction on the timeline owes a recovery - its last durable stage is
neither a commit nor a delivery - no further mutation is accepted. The gate
is asked before the first operation, so an unsettled timeline is not made
worse.

A write whose read-back failed stays unresolved: it is recorded as
`recorded` with a failed verification, and it keeps blocking until it is
recovered or abandoned.

The journal is per Ren project, and the recovery anchor only exists
where the journal does. The broker's patch job names the RESOLVE
project, so the registry maps it back to the folder that holds the
journal (`project_registry.project_folder_for_resolve_name`). Without a
journal there is no anchor, and a failed write is owed a recovery nobody
can see.

Closing that state is a decision, and it is recorded:

```sh
python3 -m library.tools.axi_transaction <project-folder> --transaction-id <id> \
    --action abandoned --reason "the captain accepted the timeline as it stands"
```

`--action recovered` closes a recovery that was applied; `--action abandoned`
closes one the captain decided to live with. Omitting `--transaction-id`
reports the project's outstanding recoveries instead of closing any.

## The compound transaction rules (AXI-014)

`library/tools/compound_transaction.py` owns the rules that make a
MULTI-OPERATION request a transaction. It writes nothing to Resolve and
decides no editorial intent.

### The write order and the dependencies

The engine DECLARES the dependencies it knows
(`compound_transaction.ENGINE_DEPENDENCIES`): a ripple closes the gap a
delete left, so the delete is written first; a join restores a split; a
resize preserves the position the overlay was given. The requester may
declare more (`depends_on` on a request operation), and an operation that
acts on a clip another operation in the same request CREATES waits for
the creator.

`plan_write_order` returns the order and the dependency that produced
it. A request whose declared order CONTRADICTS a declared dependency is
REFUSED before the first write - the engine does not choose an order the
requester did not ask for.

### The checks before a write

`compound_transaction.preflight` is the one gate, and it answers before
anything is written:

- the declared dependencies resolve (above);
- every named preservation requirement (`preservation_invariant`'s
  vocabulary) can be satisfied BY THE WRITES the request asks for. A
  request that requires a clip to be unchanged and writes to that clip
  is a contradiction, and it is refused before the mutation instead of
  being noticed on the read-back;
- this transaction is not a silent retry of a mutation that may already
  have committed. The journal's prior outcome decides: a clean commit is
  read back, and an UNSETTLED transaction is refused with
  `recovery_required`.

### The recovery

A partial mutation is recovered by reversing what landed
(`edit_recovery`), and the recovery is only CLAIMED when a fresh
read-back matches the VALIDATED generation - the newest generation a
verified write left. The comparison is `timeline_shadow.diff`, the one
the differ and the replace guard use. A compensation that ran is not a
proof that the timeline is back.

`compound_transaction.terminal_stage` decides the transaction's durable
end:

| stage | when |
|---|---|
| `abandoned` | the request never wrote - it owes no recovery and leaves no hold |
| `recovered` | it wrote, and the timeline reads as the validated generation |
| `recovery_required` | it wrote, and the timeline does not - the hold stays |

## The receipt

`axi_transaction.build_receipt` publishes the seven things a receipt has to
say:

| key | what it carries |
|---|---|
| `requested_changes` | the operations as asked |
| `actual_changes` | what the operations did |
| `untouched_regions` | the regions outside the authorized scope, and whether each changed |
| `route` | the route that was taken |
| `errors` | what went wrong |
| `recovery` | the recovery outcome |
| `verification_evidence` | the evidence that proves the result |

`state_ladder` names the six states the AXI brief distinguishes, and which
of them the transaction reached.

## The ladder

`transaction_receipt.STAGES` is the ladder:

```
PLANNED → VALIDATED → PREPARED → EXECUTING → APPLIED → VERIFIED
        → COMMITTED → PROMOTED → DELIVERED
```

- `PREPARED` - the durable intent reached the project journal before the
  first write. It is a claim about the journal, not about the write.
- `APPLIED` - the operations landed; this is not yet a verified result.
- `VERIFIED` - the read-back proved the requested effect and the operation
  acceptance contracts passed.
- `COMMITTED` - the result is persisted, as a recorded generation and, where
  the project has one, a Project Model revision.
- `PROMOTED` - a staged change reached the live timeline. A commit is not a
  promotion.
- `DELIVERED` - the requested export exists and its checks passed.

A receipt reports the highest stage its evidence establishes, and never one
above it.

## Idempotency

A retry is a retry of the same intent, not a second write:

- `timeline.apply_patch` - the patch id IS the idempotency key. The broker
  records it durably; a retry with the same id and the same patch returns
  the prior job, and the same id with a different patch is refused.
- `timeline.semantic_edit` - the request's idempotency key is the broker's
  durable key, with the same rule.
- `axi_transaction.prior_outcome` reads a project's journal for a transaction
  id and answers a retry, a conflict (the same id, different work) or an
  unsettled transaction.
