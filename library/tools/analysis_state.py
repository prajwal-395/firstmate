"""State boundary for the standalone footage analysis executor.

Analysis and editing share ``pipeline_data.json`` because edit consumes
analysis results. This module keeps that shared state protocol in one
place: legacy migration, source/code invalidation, capability output
ownership, completion records, failures, and step exports.

The actual persisted vocabulary remains owned by ``step_ledger`` and
``capability_outputs``. The small compatibility bridge below is
deliberately isolated so analysis callers never import or drive the
editing process themselves.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _editing_state_services():
    """Load shared editing state primitives without invoking its runner."""
    process_dir = REPO_ROOT / "library" / "processes" / "edit_video"
    # run_pipeline currently owns state-file defaults and the measured
    # source/code invalidation algorithm. Keep this seam here while those
    # reusable primitives are extracted from the process module.
    if str(process_dir) not in sys.path:
        sys.path.insert(0, str(process_dir))
    import run_pipeline
    return run_pipeline


def _nodes(runner) -> dict:
    return {node["id"]: node for node in runner.load_dag()["nodes"]}


def load_state(project: str) -> dict:
    """Load state once, migrating old ledger fields and reconciling identity."""
    from library.tools import step_ledger

    runner = _editing_state_services()
    state = runner.load_pipeline_state(project)
    nodes = _nodes(runner)
    manifests = runner._manifest_map(nodes)
    stage_by_node = runner._stage_map(manifests)
    step_ledger.migrate_legacy(state, stage_by_node)
    runner.apply_source_identity(project, state, stage_by_node, manifests)
    runner.apply_code_identity(state, stage_by_node, manifests, nodes)
    runner.save_pipeline_state(project, state)
    return state


def completed(capability_id: str, state: dict) -> bool:
    """Whether this capability's completion record allows reuse."""
    from library.tools import step_ledger
    return capability_id in step_ledger.completed_capabilities(state)


def check_output_is_real(node: dict, payload: dict):
    """Apply the same non-empty-output check used by the editing process."""
    return _editing_state_services().check_output_is_real(node, payload)


def record_completion(project: str, state: dict, capability_id: str,
                      node_id: str, payload: dict, seconds: float,
                      project_model_source: dict | None = None,
                      project_model_result: dict | None = None) -> None:
    """Record one capability result and its legacy-node completion marker.

    The Project Model receipt (CAP-04) travels on the ledger entry: the
    revision the run started from and the one it left behind, so a reader
    knows what evidence revision a completion stands on.
    """
    from library.tools import capability_outputs, step_ledger

    runner = _editing_state_services()
    node = _nodes(runner)[node_id]
    manifest = runner._manifest_map({node_id: node})[node_id]
    stage = step_ledger.stage_of(manifest, node_id)
    capability_outputs.record(state, node_id, payload,
                              capability_id=capability_id)
    runner._clear_step_failure(state, node_id)
    completion = {
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(seconds, 1),
        "operation": capability_id,
    }
    if project_model_source is not None or project_model_result is not None:
        completion["project_model"] = {
            "source": project_model_source,
            "result": project_model_result,
        }
    ingest = step_ledger.ingest_mark(project)
    if ingest is not None:
        completion["ingest_fingerprint"] = ingest["digest"]
    step_ledger.record(state, stage, node_id, completion)
    runner.save_pipeline_state(project, state)
    runner._export_step_for_review(project, node_id, node["name"], payload,
                                   state)


def record_failure(project: str, state: dict, node_id: str,
                   message: str) -> None:
    """Record failure using the shared state format and save immediately."""
    runner = _editing_state_services()
    runner._record_step_failure(state, node_id, message,
                                project_dir=project)
    runner.save_pipeline_state(project, state)
