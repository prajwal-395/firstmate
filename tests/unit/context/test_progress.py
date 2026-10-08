import json
from pathlib import Path

from library.tools import progress
from ren.commands import VERBS


def test_progress_is_registered_in_both_ren_front_doors():
    by_name = {verb.name: verb for verb in VERBS}
    assert by_name["progress"].module_argv == ("library.tools.progress",)
    native = (Path(__file__).resolve().parents[3]
              / "native/ren-cli/src/commands.rs").read_text(encoding="utf-8")
    assert 'name: "progress"' in native


def test_progress_reports_work_and_only_history_backed_estimate(tmp_path,
                                                                  monkeypatch):
    monkeypatch.setattr(progress, "_dag", lambda: [
        {"id": "catalog", "name": "Catalog Footage"},
        {"id": "semantic_analysis", "name": "Dialogue and visual analysis"},
        {"id": "temporal_index", "name": "Index dialogue"},
    ])
    monkeypatch.setattr(progress.run_control, "read_run_status", lambda _:
                        {"status": "running", "steps_to_run": [
                            "catalog", "semantic_analysis", "temporal_index"],
                         "step_timings": {"catalog": {"duration_s": 4}},
                         "active_steps": ["semantic_analysis"],
                         "current_step": "semantic_analysis",
                         "current_step_started_at": "2026-10-08T12:00:00",
                         "semantic_profile_stream": {"run_id": "current"}})
    monkeypatch.setattr(progress.perf_ledger, "read_rows", lambda _:
                        [{"kind": "capability", "run_id": rid,
                          "node": node, "status": "ok", "wall_s": wall}
                         for rid, node, wall in [
                             ("old1", "semantic_analysis", 100),
                             ("old2", "semantic_analysis", 120),
                             ("old1", "temporal_index", 10),
                             ("old2", "temporal_index", 14)]])
    report = progress.progress_report(str(tmp_path), broker_reader=lambda: {
        "serving": True, "busy": True, "lease": {"purpose": "render"}})
    assert report["done"] == ["Catalog Footage"]
    assert report["running"] == ["Dialogue and visual analysis"]
    assert report["up_next"] == ["Index dialogue"]
    assert report["estimated_seconds_remaining"] is not None
    assert report["resolve"]["busy"] is True


def test_progress_omits_estimate_without_two_history_samples(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(progress, "_dag", lambda: [
        {"id": "catalog", "name": "Catalog Footage"}])
    monkeypatch.setattr(progress.run_control, "read_run_status", lambda _:
                        {"status": "running", "steps_to_run": ["catalog"]})
    monkeypatch.setattr(progress.perf_ledger, "read_rows", lambda _: [])
    report = progress.progress_report(str(tmp_path), broker_reader=lambda: {})
    assert report["estimated_seconds_remaining"] is None
    assert report["estimate_basis"] is None


def test_progress_capability_requires_built_search_index(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_dag", lambda: [
        {"id": "temporal_index", "name": "Index dialogue"}])
    monkeypatch.setattr(progress.run_control, "read_run_status", lambda _:
                        {"status": "success", "steps_to_run": ["temporal_index"],
                         "step_timings": {"temporal_index": {"duration_s": 2}}})
    monkeypatch.setattr(progress.perf_ledger, "read_rows", lambda _: [])
    temporal_output = (tmp_path / "pipeline_output/steps/1_04_temporal_index"
                       / "output.json")
    temporal_output.parent.mkdir(parents=True)
    temporal_output.write_text("{}", encoding="utf-8")
    report = progress.progress_report(str(tmp_path), broker_reader=lambda: {})
    assert report["available_capabilities"] == [{
        "id": "footage.search_index",
        "label": "Dialogue analysis is available; build its searchable index with `ren search-index`.",
    }]
    index = tmp_path / "pipeline_output/scratch/footage_index/footage_index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"segments": []}), encoding="utf-8")
    report = progress.progress_report(str(tmp_path), broker_reader=lambda: {})
    assert report["available_capabilities"][0]["id"] == "footage.search"
