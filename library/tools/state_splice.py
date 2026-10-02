"""The ONE thing allowed to write part of `pipeline_data.json`.

Why there is exactly one
------------------------
`save_pipeline_state` rewrites the whole state file, and every step gets
there the same way: run the step, replace `step_outputs.<node_id>`
entirely, save.  That is correct for a step, which recomputes everything
it owns.  It is wrong for a region operation, which recomputes a slice
and must leave the rest of a step's output exactly as it was.

So a region operation needs a partial write, and a partial write is
dangerous in a way a whole one is not: nothing about it is obviously
wrong when it clobbers a neighbour.  Rather than let each operation grow
its own, there is one module, it is small, and everything about a partial
write that has to be true is asserted here.

Four rules
----------
**It writes what steps already read.**  A splice lands in
`step_outputs.<node_id>` in the shape that step's own consumers expect -
it is a PRODUCER of ordinary state, not a parallel store.  Nothing
downstream learns that a region operation exists.

**It reads, verifies, then writes, and it re-reads first.**  State is a
single JSON blob with no lock, and this pipeline runs several lanes
against one machine.  The state is re-read immediately before the write
so a splice cannot serialise a view of the file that was already stale
when the operation started.

**It refuses a write it cannot justify.**  The verifier is passed in and
is run against the merged result BEFORE the file is touched.  A splice
whose verifier raises leaves the file byte-identical.

**It says what it changed.**  Every splice returns a report and records
one in `state["region_splices"]`, because a partial write with no trace
is indistinguishable from a step having produced that output - and the
whole provenance story (increment 2) is keyed on knowing which is which.

The backup this does NOT give you
---------------------------------
`run_pipeline.save_pipeline_state` backs the state up ONCE PER PROCESS,
not per save.  A session that splices five regions gets one snapshot,
taken before the first.  That is stated here rather than worked around:
undo-one-splice is not available, and a caller who wants it takes their
own copy first.  `snapshot_path` returns where this module put its own
pre-splice copy, which is per SPLICE and is the thing to restore from.

`tests/test_state_splice.py`.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from library.tools.project_layout import ProjectLayout
from library.tools.ren_refusal import RenRefusal

SPLICE_LOG_KEY = "region_splices"
"""Where the record of every partial write lives, in the state itself."""


class StateSpliceRefused(RenRefusal):
    """The write did not happen, and the file was not touched."""


def _state_path(project_folder: str) -> Path:
    return Path(ProjectLayout(project_folder).pipeline_data_path)


def read_step_output(project_folder: str, node_id: str) -> dict:
    """One step's recorded output, or `{}` when it has none."""
    path = _state_path(project_folder)
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    return (state.get("step_outputs") or {}).get(node_id) or {}


def snapshot_path(project_folder: str, label: str) -> Path:
    """Where a pre-splice copy of the state goes.

    Beside the state rather than in the layout's backup store: the layout
    bounds that store and rotates it, and a splice snapshot that can be
    rotated away by an unrelated run is not a snapshot.
    """
    stamp = time.strftime("%Y%m%dT%H%M%S")
    return _state_path(project_folder).with_suffix(
        f".json.pre-splice-{label}-{stamp}")


def splice_step_output(project_folder: str,
                       node_id: str,
                       mutate: Callable[[dict], dict],
                       label: str,
                       verify: Optional[Callable[[dict], None]] = None,
                       report: Optional[dict] = None) -> dict:
    """Replace PART of one step's output, atomically and verifiably.

    `mutate` receives that step's current output and returns the new one.
    It must be pure: it is called on a fresh read, and it may be called
    against a state that changed since the caller last looked.

    `verify` is called with the mutated output before anything is
    written.  Anything it raises becomes a refusal and the file is not
    touched.

    Returns the report, with the snapshot path added.
    """
    path = _state_path(project_folder)
    if not path.exists():
        raise StateSpliceRefused(
            f"no pipeline state at {path}",
            "a splice edits a run that happened, and this project has "
            "not run",
            "run the pipeline first (`ren edit <project>`), then splice")

    # Re-read immediately before the write.  See "reads, verifies, then
    # writes" above - the caller's view may be minutes old.
    state = json.loads(path.read_text(encoding="utf-8"))
    outputs = state.get("step_outputs") or {}
    if node_id not in outputs:
        raise StateSpliceRefused(
            f"step_outputs has no {node_id!r}, so there is nothing to "
            f"splice into",
            f"the step never wrote its output on this run. Known: "
            f"{', '.join(sorted(outputs)) or '(none)'}",
            f"run the step that writes {node_id!r} first, then splice")

    before = outputs[node_id]
    after = mutate(json.loads(json.dumps(before)))

    if verify is not None:
        try:
            verify(after)
        except Exception as exc:
            raise StateSpliceRefused(
                f"refusing to write {node_id!r}",
                f"the mutated output failed verification: {exc}",
                "fix the mutation so it passes verification, then "
                "splice again - the file was not touched") from exc

    snapshot = snapshot_path(project_folder, label)
    shutil.copy2(path, snapshot)

    from library.tools import capability_outputs
    capability_outputs.record(state, node_id, after)

    record = dict(report or {})
    record.update({
        "node_id": node_id,
        "label": label,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "snapshot": str(snapshot),
    })
    state.setdefault(SPLICE_LOG_KEY, []).append(record)

    # Write through a temporary file in the same directory and replace.
    # A partial write here is the one failure that would leave the
    # project with no readable state at all.
    tmp = path.with_suffix(".json.splice-tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)

    return record
