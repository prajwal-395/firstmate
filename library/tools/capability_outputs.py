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

Writers: `library/processes/reels/run_reels.py`, the one place a
capability's own output is persisted outside the DAG runner.  The runner
runs NODES, not single capabilities, and keeps writing `step_outputs`
only.
"""
from __future__ import annotations

KEY = "capability_outputs"


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
    from library.tools import dag_adapter, operations
    node = dag_adapter.node_of(operations.get(capability_id))
    legacy = (state.get("step_outputs") or {}).get(node)
    return legacy if isinstance(legacy, dict) else {}
