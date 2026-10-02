"""A run's recorded output, keyed by CAPABILITY ID.

The migration rule (punch list item 5): the capability id is
authoritative.  Every reader asks here - for one capability's record
(`read`, `value`) or, where it still thinks in nodes, for the node's
view (`node_output`, `node_outputs`), which is the union of the records
of the capabilities the node's run is.  No reader opens
`state["step_outputs"]` itself.

Why the node slot is not enough
-------------------------------
One node owns several capabilities and its slot is their union.
`reel.ask` once overwrote `step_outputs.build_reels` with its ask-only
payload and the `reel_build` record the build had just placed was lost
(measured 2026-09-19).  A union merge of the slot fixed that one case;
a record keyed by the capability that wrote it cannot have the defect at
all, because no other capability writes it.

How a node's run is recorded
----------------------------
The edit_video runner executes NODES, so `record` partitions the node's
output across the capabilities its run IS
(`dag_adapter.recording_capabilities`): a key goes to the capability
that declares it in `produces`, and a key none declares - a pre-bridge's
table, a model answer returned beside the step's result - to the run's
own capability (`dag_adapter.run_capability`).  Every key lands in
exactly one record, so the node view loses nothing the slot held.

A path that ran ONE capability (`footage_intelligence`, the reels
process) passes its `capability_id`, and the record is that capability's
whole result.

`step_outputs` is no longer written.  It was the node-keyed copy of the
same output, kept beside the records until no reader was left on it.

Old projects
------------
A project recorded before `KEY` existed holds only the legacy
node-keyed `step_outputs`; one recorded during the migration holds both.
Every read here MIGRATES it on read - the slot is partitioned exactly as
`record` would have - and a capability's own record wins key by key, so
a project half-way through the migration reads the same as one wholly on
it.  `migrate` does the same in place, and every writer runs it
(`record`, `forget`, `run_pipeline.save_pipeline_state`), so the next
save carries the records and not the slot.
"""
from __future__ import annotations

import copy

KEY = "capability_outputs"
LEGACY_KEY = "step_outputs"


def _partition(node_id: str, output) -> dict:
    """`{capability id: its slice}` of one node run's output."""
    from library.tools import dag_adapter
    if not isinstance(output, dict):
        return {}
    run = dag_adapter.run_capability(node_id)
    slices: dict = {}
    claimed: set = set()
    for op in dag_adapter.recording_capabilities(node_id):
        own = {k: output[k] for k in op.produces if k in output}
        claimed.update(op.produces)
        if own:
            slices[op.name] = own
    rest = {k: v for k, v in output.items() if k not in claimed}
    if rest and run is not None:
        slices.setdefault(run.name, {}).update(rest)
    return slices


def record(state: dict, node_id: str, output, capability_id: str = "") -> None:
    """Write `output` as node `node_id`'s.

    With `capability_id`, the output is that one capability's result.
    Without, it is the node's whole run, partitioned (module docstring).
    A legacy slot still in `state` is migrated first, so nothing it held
    can outlive the write in a reader's view.
    """
    from library.tools import dag_adapter
    migrate(state)
    records = state.setdefault(KEY, {})
    if capability_id:
        records[capability_id] = copy.deepcopy(output)
        return
    slices = _partition(node_id, output)
    run = dag_adapter.run_capability(node_id)
    for op in dag_adapter.capabilities_at(node_id):
        if op.name in slices:
            records[op.name] = copy.deepcopy(slices[op.name])
        elif op.produces or (run is not None and op.name == run.name):
            # Its keys are the run's now: a record left behind would
            # serve the node view a value this run no longer holds.
            records.pop(op.name, None)


def state_recording(node_id: str, output) -> dict:
    """A state that records one node's run and nothing else - the shape a
    requirement's witness context, or a test's fixture, is built in."""
    state: dict = {}
    record(state, node_id, output)
    return state


def forget(state: dict, node_id: str) -> None:
    """Erase node `node_id`'s output: every record its capabilities hold."""
    migrate(state)
    records = state.get(KEY) or {}
    from library.tools import dag_adapter
    for op in dag_adapter.capabilities_at(node_id):
        records.pop(op.name, None)


def records(state) -> dict:
    """`{capability id: record}`, with a legacy slot migrated on read.

    A view: nothing in `state` is changed.  A capability's own record
    wins over the legacy slot key by key.
    """
    if not isinstance(state, dict):
        return {}
    own = state.get(KEY) or {}
    legacy = state.get(LEGACY_KEY) or {}
    if not legacy:
        return dict(own)
    from library.tools import dag_adapter
    out = dict(own)
    for node_id, output in legacy.items():
        if not isinstance(output, dict):
            continue
        # A key any of the node's own records holds is that record's:
        # the slot is filled in only where no capability speaks for it.
        held = set()
        for op in dag_adapter.capabilities_at(node_id):
            if isinstance(own.get(op.name), dict):
                held.update(own[op.name])
        missing = {k: v for k, v in output.items() if k not in held}
        for cid, part in _partition(node_id, missing).items():
            rec = out.get(cid)
            out[cid] = {**part, **rec} if isinstance(rec, dict) else part
    return out


def migrate(state: dict) -> bool:
    """Move a legacy `step_outputs` onto the records, in place.

    True when there was one to move.
    """
    if not isinstance(state, dict) or LEGACY_KEY not in state:
        return False
    state[KEY] = records(state)
    del state[LEGACY_KEY]
    return True


def read(state, capability_id: str) -> dict:
    """The capability's record, or {}."""
    rec = records(state).get(capability_id)
    return rec if isinstance(rec, dict) else {}


def value(state, capability_id: str, key: str, default=None):
    """One state key a capability's record holds, or `default`."""
    return read(state, capability_id).get(key, default)


def _union(node_id: str, recs: dict) -> dict:
    from library.tools import dag_adapter
    out: dict = {}
    for op in dag_adapter.capabilities_at(node_id):
        rec = recs.get(op.name)
        if isinstance(rec, dict):
            out.update(rec)
    return out


def node_output(state, node_id: str) -> dict:
    """A node's view: the union of its capabilities' records, or {}.

    For a reader that still thinks in nodes - the edge that carries a
    key, the traceback, a whole-output splice.  Registry order, so a
    run's own records (disjoint by construction) read as the slot did.
    """
    return _union(node_id, records(state))


def node_outputs(state) -> dict:
    """`{node id: its view}` for every node any record belongs to."""
    from library.tools import dag_adapter, operations
    recs = records(state)
    nodes = []
    for op in operations.all():
        if op.name in recs:
            node = dag_adapter.node_of(op)
            if node not in nodes:
                nodes.append(node)
    return {node: _union(node, recs) for node in nodes}
