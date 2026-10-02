"""A capability's recorded output, keyed by CAPABILITY ID first.

The migration rule (punch list item 5): the capability id is
authoritative, the legacy node-keyed `step_outputs.<node>` keeps being
WRITTEN beside it until nothing reads it, and a reader asks for the
capability first and falls back to the node slot - so a project recorded
before this key existed still loads.

Why the node slot is not enough
-------------------------------
One node owns several capabilities and its slot is their union.
`reel.ask` once overwrote `step_outputs.build_reels` with its ask-only
payload and the `reel_build` record the build had just placed was lost
(`run_reels.record_node_output`, measured 2026-09-19).  The union merge
fixed that one case; a slot keyed by the capability that wrote it cannot
have the defect at all, because no other capability writes it.

Writers, and why there is one entry point
-----------------------------------------
A capability record is read BEFORE the node slot, so a path that rewrote
the node slot and left the capability record behind would serve stale
state to every capability-first reader.  So every writer of a node's
output goes through `record` and every eraser through `forget`:

* the edit_video runner, which runs NODES: `record` partitions the
  node's output into each capability's slice by what it declares it
  `produces`, and drops a slice the output no longer carries;
* a path that ran ONE capability (`footage_intelligence`) passes its
  `capability_id`, and the record is that capability's whole result;
* `library/processes/reels/run_reels.py` keeps its own union merge
  (`record_node_output`, the `only_reels` lanes) and writes both keys.

While the node slot is still written, a capability slice duplicates
part of it in `pipeline_data.json`; that is the price of the migration
(item 5) until no reader is left on the node slot.
"""
from __future__ import annotations

import copy

KEY = "capability_outputs"
LEGACY_KEY = "step_outputs"


def record(state: dict, node_id: str, output, capability_id: str = "") -> None:
    """Write `output` as node `node_id`'s, under both keys.

    With `capability_id`, the output is that one capability's result.
    Without, it is the node's whole output, split by `produces`.
    """
    state.setdefault(LEGACY_KEY, {})[node_id] = output
    records = state.setdefault(KEY, {})
    if capability_id:
        records[capability_id] = copy.deepcopy(output)
        return
    from library.tools import dag_adapter
    for op in dag_adapter.capabilities_at(node_id):
        if not op.produces:
            continue
        own = ({k: copy.deepcopy(output[k]) for k in op.produces
                if k in output} if isinstance(output, dict) else {})
        if own:
            records[op.name] = own
        else:
            records.pop(op.name, None)


def forget(state: dict, node_id: str) -> None:
    """Erase node `node_id`'s output under both keys."""
    (state.get(LEGACY_KEY) or {}).pop(node_id, None)
    records = state.get(KEY) or {}
    from library.tools import dag_adapter
    for op in dag_adapter.capabilities_at(node_id):
        records.pop(op.name, None)


def value(state: dict, capability_id: str, key: str, default=None):
    """One state key a capability wrote: its own record first, then its
    legacy node's slot (a project recorded before `KEY`, or a key the
    capability does not declare producing)."""
    own = ((state.get(KEY) or {}).get(capability_id) or {}
           if isinstance(state, dict) else {})
    if isinstance(own, dict) and key in own:
        return own[key]
    return read_node(state, capability_id).get(key, default)


def read_node(state: dict, capability_id: str) -> dict:
    """The legacy node slot of a capability, or {}."""
    if not isinstance(state, dict):
        return {}
    from library.tools import dag_adapter, operations
    node = dag_adapter.node_of(operations.get(capability_id))
    legacy = (state.get(LEGACY_KEY) or {}).get(node)
    return legacy if isinstance(legacy, dict) else {}


def read(state: dict, capability_id: str) -> dict:
    """The capability's own record, else its legacy node's slot, else {}.

    The fallback is what keeps a project recorded before `KEY` existed
    loading; once every writer writes `KEY` and no project predates it,
    the fallback - and then the legacy write - can go.
    """
    if not isinstance(state, dict):
        return {}
    own = (state.get(KEY) or {}).get(capability_id)
    if isinstance(own, dict):
        return own
    return read_node(state, capability_id)
