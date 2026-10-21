# The editorial task graph

PLAN-008: a larger editorial assignment is handled as a coherent task, not a
fragile chain of unrelated commands.

This document says what the graph is, what each rule is, and where the code
lives. The rules themselves live with the code that keeps them.

| what | where |
|---|---|
| the record: tasks, dependencies, order, waves | `library/tools/editorial_task_graph.py` |
| the run: concurrent preparation, serialized writes, resume | `library/tools/editorial_task_runner.py` |
| the live proof on a Ren-owned scratch timeline | `tests/live/editorial_task_graph.py` |
| the unit tests | `tests/unit/resolve/test_editorial_task_graph.py`, `tests/unit/resolve/test_editorial_task_runner.py` |

## What a graph is

A graph is a JSON record with two kinds of task.

A **prepare** task is read-only work: retrieve the evidence an edit needs,
read the effect it will act on, measure the geometry it will move. It never
writes, so it never needs the Resolve lease, so independent preparations may
run at the same time.

A **write** task is one AXI request. It mutates the one Resolve instance, so
the graph writes its tasks one at a time, in the order the graph declares.

```json
{
  "id": "reel-2-adjustments",
  "project": "Ren Scratch - fm-p008",
  "timeline": "Reel 2",
  "originator": {"kind": "agent", "name": "ren revise"},
  "provides": {"approval:captain": {"status": "satisfied"}},
  "tasks": [
    {"id": "prep-caption", "kind": "prepare", "reads": ["captions"],
     "provides": ["evidence:caption-text"]},
    {"id": "trim-a", "kind": "write", "op": "clip.trim",
     "prepared_by": ["prep-trim"],
     "operation": {"op": "clip.trim", "target": {"unique_id": "..."},
                   "params": {"in_frame": 0, "out_frame": 23},
                   "expected_effect": "the clip is shorter"}},
    {"id": "caption-b", "kind": "write", "op": "caption.style",
     "depends_on": [{"kind": "edit", "task": "trim-a"},
                    {"kind": "evidence", "provides": "evidence:caption-text"},
                    {"kind": "approval", "provides": "approval:captain"}]}
  ]
}
```

```sh
python3 -m library.tools.editorial_task_graph plan <graph.json>
python3 -m library.tools.editorial_task_runner run  <graph.json> \
    --project-folder <folder> --apply [--resume]
```

## The four dependency kinds

| kind | what it means | how it is satisfied |
|---|---|---|
| `evidence` | the operation's parameters come from evidence that was retrieved | a `provides` key, from the graph's declaration or from a prepare task |
| `effect` | the operation acts on an effect that already exists | a `provides` key |
| `approval` | the captain approved the operation | a `provides` key |
| `edit` | the operation depends on an EARLIER EDIT in this graph | the named task is VERIFIED |

`edit` is the only kind that constrains the ORDER. The other three are
preconditions: a task whose precondition nobody satisfied is reported by name
and does not run. The graph never assumes an approval, an effect or an
evidence.

## The order

The order inside one multi-operation request is the compound transaction's
(`compound_transaction.plan_write_order`): the engine declares the
dependencies it knows - a ripple closes the gap a delete left - the requester
may declare more, and an order that CONTRADICTS a declared dependency is
refused before the first write.

The order BETWEEN two write tasks is the graph's own: a task declares that it
waits for another (`kind: "edit"`), and the engine's dependency table applies
between tasks too. A graph that declares BOTH directions is refused, the same
way the compound rules refuse it inside a request.

The plan also says which preparations may run at the same time: everything in
wave 0 may, and a preparation that waits for a write lands in a later wave.

## What may be prepared at the same time

A preparation may run while a write is pending **unless that write declares it
invalidates the preparation's evidence**:

```json
{"id": "trim", "kind": "write", "invalidates": ["prep-span"]}
```

The graph never GUESSES that a preparation would go stale. A guess either
blocks every preparation (nothing may be prepared ahead of a write) or lets
evidence rot silently; a declaration is the only honest answer, and the
declaration is recorded in the plan.

## The run

`editorial_task_runner.run` keeps five rules.

1. **Preparation is outside the Resolve lease.** Preparations are read-only, so
   they run concurrently, bounded by `max_prepare_workers`, and none of them
   takes a lease.
2. **Writes are serialized.** One Resolve instance, one writer. The graph
   writes its tasks one after another. The runner never holds the Resolve
   lease across a broker submission: the broker takes its own turn, and a
   client that held the lease while queueing behind itself would wait for a
   turn that cannot come.
3. **A step counts as done only when it is VERIFIED.** A receipt at APPLIED is
   not a done step (`transaction_receipt` owns the ladder).
4. **The run stops at the first write that is not verified.** It never
   continues past a mutation whose outcome nobody established.
5. **Resume is the project's own journal.** The progress is written with
   `event_log.checkpoint`, so a resumed run reads the last verified step off
   the same append-only log every other worker reads. A resumed run whose
   graph no longer matches the checkpointed plan REFUSES: the plan changed
   under it, and continuing would write an order nobody approved.

The request id a write carries is stable for the task AND for its content
(`build_request`), so a resumed run submits the same id and the AXI answers
with the prior outcome instead of writing the same mutation twice, while a
task whose content changed gets a new id.

## What it does NOT do

- It does not choose the edits. The operations are the AXI algebra's
  (`edit_algebra`), and the graph carries them.
- It does not qualify an operation. An operation with no production-enabled
  backend is refused by the AXI's own routing, and the graph carries the
  front door that owns the protected staging guarantee in `originator` - it
  names that front door, it does not grant the guarantee.
- It does not own the lease. The AXI transaction and the broker own the
  mutation and the lease; the graph owns the order and the resume point.

## The live proof

`tests/live/editorial_task_graph.py` proves the three claims on a live Resolve
timeline, on a Ren-owned scratch project, through the serving broker:

- the preparations ran at the same time, in a client that held no lease;
- the writes ran one at a time, each through the broker;
- the run resumed at the last verified step, and the fragile chain's repeat
  of that write was refused by the AXI's own prior-outcome rule.

The proof never starts a broker, never touches a captain's project, timeline
or media, and copies its media out of the shared qualification pool.
