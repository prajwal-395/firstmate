import json

from library.tools import footage_analysis, heavy_work_lock
from library.tools.project_layout import ProjectLayout
from library.tools.resource_scheduler import Scheduler


def test_analysis_capabilities_reserve_model_memory_before_lane_grant(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(project).ensure()
    lock_dir = tmp_path / "heavy-work.lock"
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR", lock_dir)
    scheduler = Scheduler(lock_dir)

    def run_steps(*_args):
        with heavy_work_lock.heavy_work_reservation(
                "semantic model", "semantics.analyse:model_memory",
                nested_profiles=("semantics.analyse:inference",)):
            outer_demand = scheduler.jobs()[0]["demand"]
            assert set(outer_demand) == {"ram_gb"}
            with heavy_work_lock.heavy_work_lock(
                    "semantic inference", "semantics.analyse:inference"):
                jobs = scheduler.jobs()
                assert len(jobs) == 2
                demands = [job["demand"] for job in jobs]
                assert any(set(demand) == {"ram_gb"}
                           for demand in demands)
                assert any(set(demand) == {"cpu", "gpu"}
                           and demand["gpu"] == 1
                           for demand in demands)
        return {"status": "complete"}

    monkeypatch.setattr(footage_analysis, "run_steps", run_steps)
    monkeypatch.setattr(
        footage_analysis, "catalog_sources",
        lambda _project: [{"clip_id": "clip_001", "digest": "abc",
                           "digest_basis": "fixture",
                           "duration_seconds": 1.0}])

    def run_lane(_lane, _project, _sources, _root):
        assert any(job["owner"] == footage_analysis.LOCK_OWNER
                   for job in scheduler.jobs())
        return {"lane": "frames", "sources": {"clip_001": {
            "status": footage_analysis.BUILT}}}

    monkeypatch.setattr(footage_analysis, "run_lane", run_lane)
    result = footage_analysis.analyze(
        str(project), lanes=("frames",), root=tmp_path / "memory")

    assert result["steps"] == {"status": "complete"}
    assert result["status"] == "complete"
    assert scheduler.jobs() == []


def test_analysis_step_failure_still_releases_scheduler_grants(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(project).ensure()
    lock_dir = tmp_path / "heavy-work.lock"
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR", lock_dir)
    scheduler = Scheduler(lock_dir)

    def fail_steps(*_args):
        with heavy_work_lock.heavy_work_reservation(
                "semantic model", "semantics.analyse:model_memory",
                nested_profiles=("semantics.analyse:inference",)):
            raise RuntimeError("fixture failure")

    monkeypatch.setattr(footage_analysis, "run_steps", fail_steps)

    try:
        footage_analysis.analyze(str(project), lanes=(), root=tmp_path)
    except RuntimeError as exc:
        assert str(exc) == "fixture failure"
    else:
        raise AssertionError("analysis step failure should propagate")

    assert scheduler.jobs() == []


def test_json_mode_sends_progress_to_stderr(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(footage_analysis, "resolve_target",
                        lambda *_args: (str(project), False))

    def analyze(*_args, **_kwargs):
        print("analysis progress")
        return {"status": "complete"}

    monkeypatch.setattr(footage_analysis, "analyze", analyze)

    assert footage_analysis.main([str(project), "--json"]) == 0
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {"status": "complete"}
    assert captured.err == "analysis progress\n"


def test_old_expression_classifier_output_is_stale(tmp_path):
    from library.tools import source_memory

    digest = "expression-method-version"
    sdir = source_memory.source_dir(digest, tmp_path)
    sdir.mkdir(parents=True)
    source_memory.write_json(
        sdir / source_memory.SLOT_PERSONS,
        {"content_digest": digest})
    source_memory.write_json(
        sdir / source_memory.SLOT_FRAMES_INDEX,
        {"content_digest": digest})
    source_memory.write_json(
        sdir / source_memory.SLOT_EXPRESSIONS,
        {"content_digest": digest,
         "instrument": {"method": "expression-classifier-v1"}})
    lane = next(item for item in footage_analysis.LANES
                if item.name == "expressions")

    assert not footage_analysis.slot_is_fresh(lane, digest, tmp_path)
