"""Analysis state shares the edit format without importing its process."""
from __future__ import annotations

import inspect
import json

from library.processes.edit_video import run_pipeline
from library.tools import analysis_state, capability_outputs, footage_intelligence, step_ledger
from library.tools.project_layout import ProjectLayout


def test_analysis_executor_uses_state_api_without_importing_runner():
    source = inspect.getsource(footage_intelligence)
    assert "analysis_state" in source
    assert "import run_pipeline" not in source
    assert "step_ledger.record" not in source
    assert "step_ledger.completed_capabilities" not in source
    assert "capability_outputs.record" not in source


def test_legacy_only_state_migrates_once_with_same_reuse_and_output():
    nodes = {node["id"]: node for node in run_pipeline.load_dag()["nodes"]}
    stage_by_node = run_pipeline._stage_map(
        run_pipeline._manifest_map(nodes))
    state = {
        "steps_completed": {"scan": {"operation": "footage.scan"}},
        "step_outputs": {"scan": {"total_files": 2}},
        "source_fingerprints": {"clip_001": {"size_bytes": 8}},
        "preflight_code_hashes": {"scan": "recorded-hash"},
    }

    # The old fields already drove this same skip decision before migration.
    before_reuse = "footage.scan" in step_ledger.completed_capabilities(state)
    before_output = capability_outputs.value(state, "footage.scan",
                                             "total_files")
    assert before_reuse
    assert before_output == 2

    step_ledger.migrate_legacy(state, stage_by_node)
    assert capability_outputs.migrate(state)
    migrated_snapshot = dict(state)
    # A second load/save boundary is idempotent.
    assert step_ledger.migrate_legacy(state, stage_by_node) == []
    assert not capability_outputs.migrate(state)

    assert ("footage.scan" in step_ledger.completed_capabilities(state)) == \
        before_reuse
    assert capability_outputs.value(state, "footage.scan",
                                    "total_files") == before_output
    assert state == migrated_snapshot
    assert "step_outputs" not in state
    assert state["source_fingerprints"] == {
        "clip_001": {"size_bytes": 8}}
    assert state["preflight_code_hashes"] == {"scan": "recorded-hash"}


def _write_pipeline_data(root, state: dict) -> None:
    path = ProjectLayout(str(root)).pipeline_data_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_analysis_state_migrates_legacy_project_once_with_same_skip_decisions(
        tmp_path):
    """The CAP-05 acceptance path, end to end through the state API.

    A project recorded before the split carries only the legacy ledger
    and the legacy node-keyed outputs. Loading it through
    ``analysis_state`` migrates it once, keeps the exact skip/reuse
    decisions the old fields drove, and never writes ``step_outputs``
    back - on migration, on completion, or on failure.
    """
    legacy = {
        "steps_completed": {
            "scan": {"operation": "footage.scan"},
            "catalog": {"operation": "footage.catalog"},
        },
        "step_outputs": {
            "scan": {"total_files": 2},
            "catalog": {"total_clips": 3},
        },
    }
    before_reuse = set(step_ledger.completed_capabilities(dict(legacy)))
    assert before_reuse == {"footage.scan", "footage.catalog"}

    _write_pipeline_data(tmp_path, legacy)
    state = analysis_state.load_state(str(tmp_path))

    # Identical skip decisions: done stays done, the rest stays runnable.
    assert {cid for cid in ("footage.scan", "footage.catalog",
                            "semantics.analyse", "temporal.index",
                            "prosody.analyse")
            if analysis_state.completed(cid, state)} == before_reuse
    assert capability_outputs.value(state, "footage.scan",
                                    "total_files") == 2
    assert capability_outputs.value(state, "footage.catalog",
                                    "total_clips") == 3
    assert "step_outputs" not in state
    assert "steps_completed" not in state

    # A second load migrates nothing and decides the same.
    again = analysis_state.load_state(str(tmp_path))
    assert {cid for cid in ("footage.scan", "footage.catalog",
                            "semantics.analyse", "temporal.index",
                            "prosody.analyse")
            if analysis_state.completed(cid, again)} == before_reuse
    assert capability_outputs.value(again, "footage.scan",
                                    "total_files") == 2

    # The completion path records the capability without a legacy slot.
    analysis_state.record_completion(
        str(tmp_path), state, "prosody.analyse", "prosody_analysis",
        {"available": True, "profiles": [{"clip_id": "clip_001"}]}, 1.0)
    assert analysis_state.completed("prosody.analyse", state)
    assert "step_outputs" not in state

    # The failure path clears the skip so the capability re-runs.
    analysis_state.record_failure(str(tmp_path), state, "prosody_analysis",
                                  "boom")
    assert not analysis_state.completed("prosody.analyse", state)
    assert "step_outputs" not in state
