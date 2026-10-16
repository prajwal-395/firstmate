import json
from contextlib import contextmanager

from library.tools import footage_analysis, heavy_work_lock, resource_scheduler
from library.tools.project_layout import ProjectLayout


def test_analysis_capabilities_reserve_model_memory_before_lane_grant(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(project).ensure()
    assert resource_scheduler.demand_for(
        "semantics.analyse:model_memory") == {"ram_gb": 15}
    assert resource_scheduler.demand_for(
        "semantics.analyse:inference") == {}
    active_profiles = []

    @contextmanager
    def fake_reservation(_owner, profile, *, nested_profiles):
        assert profile == "semantics.analyse:model_memory"
        assert nested_profiles == ("semantics.analyse:inference",)
        active_profiles.append(profile)
        try:
            yield
        finally:
            active_profiles.remove(profile)

    @contextmanager
    def fake_lock(_owner, profile=heavy_work_lock.MACHINE):
        if profile == "semantics.analyse:inference":
            assert active_profiles == ["semantics.analyse:model_memory"]
        active_profiles.append(profile)
        try:
            yield
        finally:
            active_profiles.remove(profile)

    monkeypatch.setattr(heavy_work_lock, "heavy_work_reservation",
                        fake_reservation)
    monkeypatch.setattr(heavy_work_lock, "heavy_work_lock", fake_lock)

    def run_steps(*_args):
        with (heavy_work_lock.heavy_work_reservation(
                "semantic model", "semantics.analyse:model_memory",
                nested_profiles=("semantics.analyse:inference",)),
              heavy_work_lock.heavy_work_lock(
                  "semantic inference", "semantics.analyse:inference")):
            assert active_profiles == [
                "semantics.analyse:model_memory",
                "semantics.analyse:inference"]
        return {"status": "complete"}

    monkeypatch.setattr(footage_analysis, "run_steps", run_steps)
    monkeypatch.setattr(
        footage_analysis, "catalog_sources",
        lambda _project: [{"clip_id": "clip_001", "digest": "abc",
                           "digest_basis": "fixture",
                           "duration_seconds": 1.0}])

    def run_lane(_lane, _project, _sources, _root):
        assert active_profiles == [heavy_work_lock.MACHINE]
        return {"lane": "frames", "sources": {"clip_001": {
            "status": footage_analysis.BUILT}}}

    monkeypatch.setattr(footage_analysis, "run_lane", run_lane)
    result = footage_analysis.analyze(
        str(project), lanes=("frames",), root=tmp_path / "memory")

    assert result["steps"] == {"status": "complete"}
    assert result["status"] == "complete"
    assert active_profiles == []


def test_analysis_step_failure_still_releases_model_reservation(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(project).ensure()
    assert resource_scheduler.demand_for(
        "semantics.analyse:model_memory") == {"ram_gb": 15}
    active_profiles = []

    @contextmanager
    def fake_reservation(_owner, profile, *, nested_profiles):
        assert profile == "semantics.analyse:model_memory"
        assert nested_profiles == ("semantics.analyse:inference",)
        active_profiles.append(profile)
        try:
            yield
        finally:
            active_profiles.remove(profile)

    monkeypatch.setattr(heavy_work_lock, "heavy_work_reservation",
                        fake_reservation)

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

    assert active_profiles == []


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


def _fixture_project(tmp_path, monkeypatch, digest):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(project).ensure()
    lock_dir = tmp_path / "heavy-work.lock"
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR", lock_dir)
    monkeypatch.setattr(
        footage_analysis, "catalog_sources",
        lambda _project: [{"clip_id": "clip_001", "digest": digest,
                           "digest_basis": "fixture",
                           "duration_seconds": 10.0}])
    return project


def test_fresh_analysis_joins_the_m10_that_just_built(tmp_path, monkeypatch):
    """A fresh M9 contains the M10 relationships built in the same run.

    The defect: M9 was scheduled before M10, so a fresh build joined
    whatever M10 happened to be on disk - usually nothing - and the
    relationships the run just measured never reached the world model.
    The request below names M9 first on purpose: producer order, not
    request order, must decide the schedule.
    """
    from library.tools import relationships as relationships_mod
    from library.tools import source_memory, world_model

    digest = "d" * 64
    project = _fixture_project(tmp_path, monkeypatch, digest)
    root = tmp_path / "memory"
    holding = [{"owners": ["face_001", "face_001"], "start": 1.0,
                "end": 3.0}]

    def fake_relationships(project_folder, clip_ids=None, root=None):
        sdir = source_memory.source_dir(digest, root)
        source_memory.write_json(
            sdir / source_memory.SLOT_RELATIONSHIPS,
            {"content_digest": digest, "source_file": "/nowhere/FILE.MXF",
             "status": "built", "frame_count": 20,
             "relationships": {
                 "holding": {"basis": "test", "spans": holding},
                 "pointing": {"basis": "test", "spans": []},
                 "looking_at": {"basis": "test", "spans": []}}})
        return {"project_folder": project_folder,
                "clips": [{"clip_id": "clip_001",
                           "content_digest": digest}],
                "failed": []}

    def fake_world_model(project_folder, root=None):
        m10 = source_memory.read_relationships(digest, root)
        sdir = source_memory.source_dir(digest, root)
        source_memory.write_json(
            sdir / source_memory.SLOT_WORLD_MODEL,
            {"content_digest": digest, "source_file": "/nowhere/FILE.MXF",
             "status": "built",
             "relationships_seen": (m10 or {}).get("relationships")})
        return {"project_folder": project_folder,
                "clips": [{"clip_id": "clip_001",
                           "content_digest": digest}],
                "failed": []}

    monkeypatch.setattr(relationships_mod, "build_project_relationships",
                        fake_relationships)
    monkeypatch.setattr(world_model, "build_project_world_model",
                        fake_world_model)

    record = footage_analysis.analyze(
        str(project), lanes=("world_model", "relationships"),
        steps=False, root=root)
    assert record["status"] == "complete"

    with open(source_memory.source_dir(digest, root)
              / source_memory.SLOT_WORLD_MODEL,
              encoding="utf-8") as handle:
        m9 = json.load(handle)
    assert (m9["relationships_seen"]["holding"]["spans"]
            == holding)


def test_changed_m10_makes_m9_stale_then_rebuilt(tmp_path, monkeypatch):
    """A later M10 update invalidates M9, and the next run rebuilds it.

    The defect: M9's freshness fingerprint excluded M10, so an M10
    rebuild left a stale join reading as fresh - the world model kept
    answering off relationships that no longer existed.
    """
    from library.tools import relationships as relationships_mod
    from library.tools import source_memory, world_model

    digest = "e" * 64
    project = _fixture_project(tmp_path, monkeypatch, digest)
    root = tmp_path / "memory"
    sdir = source_memory.source_dir(digest, root)
    sdir.mkdir(parents=True)
    for slot in (source_memory.SLOT_SOURCE,
                 source_memory.SLOT_TRANSCRIPT,
                 source_memory.SLOT_PERSONS,
                 source_memory.SLOT_IDENTITY,
                 source_memory.SLOT_EXPRESSIONS,
                 source_memory.SLOT_SCENES,
                 source_memory.SLOT_SOUND,
                 source_memory.SLOT_CLOCK,
                 source_memory.SLOT_EVENTS,
                 source_memory.SLOT_VERDICTS):
        source_memory.write_json(sdir / slot, {"content_digest": digest})
    source_memory.write_json(
        sdir / source_memory.SLOT_RELATIONSHIPS,
        {"content_digest": digest, "relationships": {"holding": []}})
    source_memory.write_json(
        sdir / source_memory.SLOT_WORLD_MODEL,
        {"content_digest": digest, "relationships_seen": {"holding": []}})

    lane = next(item for item in footage_analysis.LANES
                if item.name == "world_model")
    assert footage_analysis.slot_is_fresh(lane, digest, root)

    import os
    import time
    time.sleep(0.02)
    source_memory.write_json(
        sdir / source_memory.SLOT_RELATIONSHIPS,
        {"content_digest": digest,
         "relationships": {"holding": [{"owners": ["face_001"],
                                        "start": 2.0, "end": 4.0}]}})
    newest = (sdir / source_memory.SLOT_RELATIONSHIPS).stat().st_mtime
    os.utime(sdir / source_memory.SLOT_WORLD_MODEL,
             (newest - 10, newest - 10))

    assert not footage_analysis.slot_is_fresh(lane, digest, root)

    rebuilt = {}

    def fake_world_model(project_folder, root=None):
        rebuilt["ran"] = True
        m10 = source_memory.read_relationships(digest, root)
        source_memory.write_json(
            sdir / source_memory.SLOT_WORLD_MODEL,
            {"content_digest": digest,
             "relationships_seen": m10.get("relationships")})
        return {"project_folder": project_folder,
                "clips": [{"clip_id": "clip_001",
                           "content_digest": digest}],
                "failed": []}

    def fake_relationships(project_folder, clip_ids=None, root=None):
        return {"project_folder": project_folder,
                "clips": [{"clip_id": "clip_001", "content_digest": digest,
                           "reused": True}],
                "failed": []}

    monkeypatch.setattr(world_model, "build_project_world_model",
                        fake_world_model)
    monkeypatch.setattr(relationships_mod, "build_project_relationships",
                        fake_relationships)

    record = footage_analysis.analyze(
        str(project), lanes=("relationships", "world_model"),
        steps=False, root=root)
    assert record["status"] == "complete"
    assert rebuilt.get("ran") is True
    with open(sdir / source_memory.SLOT_WORLD_MODEL,
              encoding="utf-8") as handle:
        m9 = json.load(handle)
    assert m9["relationships_seen"]["holding"] == [
        {"owners": ["face_001"], "start": 2.0, "end": 4.0}]


def test_missing_m10_builds_m9_as_absent_not_empty(tmp_path, monkeypatch):
    """Without M10, M9 still builds - and says M10 is absent, not empty.

    The defect this pins: recording no relationships file as "measured
    and nobody relates to anybody" would read as an answer, when the
    truth is "never measured". The world model's `lanes` is the
    signal: M10 joins it only when measured.
    """
    from library.tools import source_memory, world_model

    digest = "f" * 64
    root = tmp_path / "memory"
    target = root / digest
    target.mkdir(parents=True)
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))

    def write(slot, payload):
        import json as _json
        from pathlib import Path as _Path
        path = _Path(target) / slot
        path.write_text(_json.dumps(payload), encoding="utf-8")

    write(source_memory.SLOT_SOURCE, {
        "content_digest": digest, "size_bytes": 12345,
        "observed_paths": ["/nowhere/FILE.MXF"], "duration_seconds": 3.0,
        "video_streams": [], "audio_streams": [],
        "measured_levels_db": {}, "program_track": {}, "gop_frames": None})
    assert (target / source_memory.SLOT_RELATIONSHIPS).exists() is False

    record = world_model.build_source_world_model(digest, "/nowhere/FILE.MXF",
                                                  root)
    assert "m10" not in record["lanes"]
    model = source_memory.read_world_model(digest, root)
    entry = world_model.query_at_time(model, 0.0)
    assert entry["relationships"] == {
        "holding": [], "pointing": [], "looking_at": []}


def test_lane_cycle_is_refused(tmp_path):
    """Two lanes producing each other's inputs refuse, never half-run.

    The defect: without a cycle check the producer sort would either
    loop or silently run one lane off the other's stale output.
    """
    first = footage_analysis.Lane(
        "cycle_a", "X", "fixture", ("slot_a",), ("slot_b",),
        False, False, lambda *_args: {})
    second = footage_analysis.Lane(
        "cycle_b", "X", "fixture", ("slot_b",), ("slot_a",),
        False, False, lambda *_args: {})
    try:
        footage_analysis.order_lanes((first, second))
    except ValueError as exc:
        assert "cycle" in str(exc)
    else:
        raise AssertionError("a lane cycle should refuse")
