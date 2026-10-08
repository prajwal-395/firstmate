"""The broker's typed promotion job cannot target user projects or run code."""

import pytest

from library.tools.resolved import jobs, reel_promotion
from library.tools.resolved.jobs import QUALIFICATION_PROJECT, JobRefused
from tests.resolve_double import FakeProject, FakeResolve, FakeTimeline

FINAL = "Q Promo Live"
STAGING = FINAL + reel_promotion.STAGING_SUFFIX
MASTER = "Q Seed"
TRACK_PLAN = {"video_tracks": [], "audio_tracks": []}


def _params(**overrides):
    return {
        "project": QUALIFICATION_PROJECT,
        "master_timeline_name": MASTER,
        "final_timeline_name": FINAL,
        "staging_timeline_name": STAGING,
        "track_plan": TRACK_PLAN,
        **overrides,
    }


class _BorrowableProjectManager:
    def __init__(self, captain, qualification):
        self.projects = {captain.GetName(): captain,
                         qualification.GetName(): qualification}
        self.current = captain
        self.saved = []
        self.loaded = []

    def GetCurrentProject(self):
        return self.current

    def SaveProject(self):
        self.saved.append(self.current.GetName())
        return True

    def LoadProject(self, name):
        self.loaded.append(name)
        self.current = self.projects.get(name)
        if self.current and self.current.GetTimelineCount():
            self.current._current = self.current.GetTimelineByIndex(1)
        return self.current


def _borrowable_resolve(*, final_is_current=False):
    captain_timeline = FakeTimeline("Reel 07", uid="captain-timeline")
    captain = FakeProject("Podcast (field test)", [captain_timeline],
                          current=captain_timeline)
    master = FakeTimeline(MASTER, uid="master-id")
    retiring = FakeTimeline(FINAL, uid="final-id")
    staged = FakeTimeline(STAGING, uid="staging-id")
    timelines = ([retiring, master, staged] if final_is_current else
                 [master, retiring, staged])
    qualification = FakeProject(
        QUALIFICATION_PROJECT, timelines,
        current=retiring if final_is_current else master)
    resolve = FakeResolve(captain)
    manager = _BorrowableProjectManager(captain, qualification)
    resolve._manager = manager
    return resolve, manager, captain, qualification, master, retiring, staged


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    from library.tools import resolve_lock

    directory = tmp_path / "resolve-lock"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    return directory


def test_prepare_is_exactly_one_qualification_reel_and_exclusive():
    from library.tools.resolved.scheduler import EXCLUSIVE

    shape = jobs.prepare(reel_promotion.KIND, _params())

    assert shape["mode"] == EXCLUSIVE
    assert shape["executed"] is True
    assert shape["qualification"] is True
    assert shape["project"] == QUALIFICATION_PROJECT
    assert shape["timeline"] == STAGING
    assert shape["coalesce_key"] is None


def test_qualification_fixture_job_is_closed_and_qualification_only():
    from library.tools.resolved import reel_promotion_fixture as fixture

    shape = jobs.prepare(
        fixture.KIND, {"project": QUALIFICATION_PROJECT})

    assert shape["mode"] == "exclusive"
    assert shape["qualification"] is True
    assert shape["project"] == QUALIFICATION_PROJECT
    assert shape["timeline"] == fixture.FINAL
    with pytest.raises(JobRefused, match="accepts only"):
        jobs.prepare(fixture.KIND, {
            "project": QUALIFICATION_PROJECT,
            "timeline": "Podcast (field test)"})


def test_fixture_duplicate_placements_use_the_same_source_range():
    from library.tools.resolved import reel_promotion_fixture as fixture

    class Timeline:
        def __init__(self):
            self.items = []
            self.track_name = ""

        def GetItemListInTrack(self, _kind, _index):
            return self.items

        def SetTrackName(self, _kind, _index, name):
            self.track_name = name
            return True

        def GetTrackName(self, _kind, _index):
            return self.track_name

    class Pool:
        def __init__(self):
            self.timeline = Timeline()
            self.specs = []

        def CreateEmptyTimeline(self, _name):
            return self.timeline

        def AppendToTimeline(self, specs):
            self.specs = specs
            self.timeline.items = [object() for _ in specs]
            return self.timeline.items

    pool = Pool()
    timeline, items = fixture._create_timeline(
        object(), pool, object(), fixture.AMBIGUOUS_STAGING, 2)

    assert timeline is pool.timeline
    assert len(items) == 2
    assert [(spec["startFrame"], spec["endFrame"])
            for spec in pool.specs] == [(0, 71), (0, 71)]


def test_qualification_fixture_seeds_a_comp_the_promotion_can_export(
        tmp_path, monkeypatch):
    from library.tools import reel_fusion_comps
    from library.tools.resolved import reel_promotion_fixture as fixture
    from tests.resolve_double import FakeTimelineItem

    class NeutralEffect:
        def __init__(self):
            self.values = {}

        def GetAttrs(self):
            return {"TOOLS_RegID": "BrightnessContrast"}

        def SetInput(self, key, value):
            self.values[key] = value

        def GetInput(self, key):
            return self.values.get(key)

    class FusionComp:
        def AddTool(self, name, *_position):
            assert name == "BrightnessContrast"
            self.effect = NeutralEffect()
            return self.effect

    master = FakeTimeline(fixture.MASTER)
    final = FakeTimeline(fixture.FINAL)
    staging = FakeTimeline(fixture.STAGING)
    ambiguous_final = FakeTimeline(fixture.AMBIGUOUS_FINAL)
    ambiguous_staging = FakeTimeline(fixture.AMBIGUOUS_STAGING)
    original = FakeTimelineItem("qual_b", final)
    item = FakeTimelineItem("qual_b", staging)
    ambiguous_original = FakeTimelineItem("qual_b", ambiguous_final)
    ambiguous_items = [FakeTimelineItem("qual_b", ambiguous_staging)
                       for _ in range(2)]
    staging.add_item("video", 1, item)
    staging.SetTrackName("video", 1, "A-Roll")
    comp_handle = FusionComp()
    item.AddFusionComp = lambda: item._add_comp(comp_handle)
    original_comp_handle = FusionComp()
    original.AddFusionComp = lambda: original._add_comp(
        original_comp_handle)

    export_calls = []

    def export_comp(path, index):
        from pathlib import Path

        export_calls.append((path, index))
        Path(path).write_text("-- neutral fixture comp\n", encoding="utf-8")
        return True

    item.ExportFusionComp = export_comp
    original.ExportFusionComp = export_comp

    batches = iter([
        (final, [original]), (staging, [item]),
        (ambiguous_final, [ambiguous_original]),
        (ambiguous_staging, ambiguous_items),
    ])

    def create_timeline(_project, _pool, _media, name, copies):
        timeline, items = next(batches)
        assert timeline.GetName() == name
        assert len(items) == copies
        return timeline, items

    class Project:
        def GetTimelineCount(self):
            return 0

        def GetCurrentTimeline(self):
            return master

        def GetMediaPool(self):
            return object()

    class Manager:
        def SaveProject(self):
            return True

    monkeypatch.setattr(fixture, "_exact_timeline",
                        lambda _project, _name: master)
    monkeypatch.setattr(fixture, "_pool_clip", lambda _project: object())
    monkeypatch.setattr(fixture, "_create_timeline", create_timeline)
    monkeypatch.setattr(fixture, "_add_clip_marker",
                        lambda _item: {"source_frame": 24})
    monkeypatch.setattr(fixture, "_track_plan",
                        lambda _items: {"video_tracks": [],
                                        "audio_tracks": [],
                                        "material": {}})
    monkeypatch.setattr(
        "library.tools.qualification_project.promotion_project_folder",
        lambda: tmp_path / "promotion_project")
    inventories = iter(([{"name": "before"}], [{"name": "after"}]))
    monkeypatch.setattr("library.tools.reel_replace_guard.timeline_inventory",
                        lambda _project: next(inventories))
    seeded = {}

    def begin_inventory(_review_dir, _operation, _before, **kwargs):
        seeded.update(kwargs)
        return "fixture-operation"

    completed = []
    monkeypatch.setattr("library.tools.plan_provenance.begin_timeline_inventory",
                        begin_inventory)
    monkeypatch.setattr(
        "library.tools.plan_provenance.finish_timeline_inventory",
        lambda _review_dir, operation, after: completed.append(
            (operation, after)))
    snapshots = []
    monkeypatch.setattr(
        "library.tools.reel_replace_guard.full_timeline_snapshot",
        lambda timeline, _project, folder: {
            "timeline": {"name": timeline.GetName()}, "folder": folder})
    monkeypatch.setattr(
        "library.tools.plan_provenance.record_timeline_snapshot",
        lambda review_dir, name, snapshot, **kwargs: snapshots.append(
            (review_dir, name, snapshot, kwargs)))
    monkeypatch.setattr("library.tools.resolve_lock.assert_current_timeline",
                        lambda _project, _timeline: None)

    result = fixture._build(Project(), Manager())
    report = reel_fusion_comps.export_reel_comps(
        staging, fixture.FINAL, str(tmp_path))

    assert result["staging_fusion_comp"] == {
        "name": "Composition 1", "index": 1, "count": 1,
        "tool": "BrightnessContrast", "neutral": True,
        "preflight_exported": True}
    assert seeded["ren_created_names"] == {
        fixture.FINAL, fixture.STAGING, fixture.AMBIGUOUS_FINAL,
        fixture.AMBIGUOUS_STAGING}
    assert set(seeded["ren_created_ids"]) == {
        timeline.GetUniqueId() for timeline in
        (final, staging, ambiguous_final, ambiguous_staging)}
    assert completed == [("fixture-operation", [{"name": "after"}])]
    assert [entry[1] for entry in snapshots] == [
        fixture.FINAL, fixture.AMBIGUOUS_FINAL]
    assert all(entry[2]["timeline"]["name"] == entry[1]
               and entry[3]["action"] ==
               "qualification_promotion_fixture_baseline"
               for entry in snapshots)
    assert comp_handle.effect.values == {
        "Gain": 1.0, "Gamma": 1.0, "Lift": 0.0, "Contrast": 1.0,
        "Saturation": 1.0}
    assert report["comp_count"] == 1
    assert report["errors"] == []
    assert result["final_fusion_comp"]["count"] == 1
    assert original_comp_handle.effect.values == comp_handle.effect.values
    assert [index for _path, index in export_calls] == [1, 1, 1]
    assert [path.rsplit("/", 1)[-1] for path in report["files"]] == [
        "Q_Promotion_Proof_R5__V01_item000_c1.comp"]
    assert (tmp_path / "pipeline_output" / "steps" / "7_01_build_reels"
            / "fusion_comps" / "Q_Promotion_Proof_R5__V01_item000_c1.comp"
            ).is_file()


def test_marker_loss_probe_catches_named_loss_and_restores_fixture_marker():
    from library.tools.resolved import marker_loss_probe as probe
    from tests.resolve_double import FakeMediaPoolItem, FakeTimelineItem

    timeline = FakeTimeline(probe.FINAL, uid="marker-probe-timeline")
    pool_item = FakeMediaPoolItem("qual_b")
    pool_item.SetClipProperty("File Path", "/qualification/qual_b.mp4")
    marker = {
        "color": "Blue", "name": "Ren live promotion anchor",
        "note": "Keep this clip anchored note on qual_b.",
        "duration": 1, "customData": "ren-qualification-promotion-proof",
    }
    item = FakeTimelineItem("qual_b", timeline, duration=72,
                            pool_item=pool_item, markers={24: marker})
    timeline.add_item("video", 1, item)
    timeline.SetTrackName("video", 1, "A-Roll")

    class Manager:
        def SaveProject(self):
            return True

    result = probe._probe(timeline, Manager())

    assert result["gate_caught"] is True
    assert "MARKER GATE LOST" in result["gate_message"]
    assert marker["name"] in result["gate_message"]
    assert marker["note"] in result["gate_message"]
    assert result["marker_restored"] is True
    assert item.GetMarkers()[24] == marker


def test_marker_loss_probe_is_closed_and_qualification_only():
    from library.tools.resolved import marker_loss_probe as probe

    shape = jobs.prepare(probe.KIND, {"project": QUALIFICATION_PROJECT})

    assert shape["qualification"] is True
    assert shape["project"] == QUALIFICATION_PROJECT
    assert shape["timeline"] == probe.FINAL
    with pytest.raises(JobRefused, match="accepts only"):
        jobs.prepare(probe.KIND, {"project": "Podcast (field test)"})
    with pytest.raises(JobRefused, match="accepts only"):
        jobs.prepare(probe.KIND, {
            "project": QUALIFICATION_PROJECT, "timeline": "other"})


def test_broker_submit_persists_the_shape_qualification_fence(
        tmp_path, monkeypatch):
    from library.tools.resolved.server import Broker
    from library.tools.resolved.store import JobStore

    monkeypatch.setattr(Broker, "_schedule_loop", lambda _self: None)
    monkeypatch.setattr(Broker, "_execute_loop", lambda _self: None)
    store = JobStore(tmp_path / "jobs.sqlite3")
    broker = Broker(store, connect=lambda: pytest.fail(
        "a queued job must not connect Resolve"))
    try:
        submitted = broker.submit(
            reel_promotion.KIND, _params(), qualification=False)
        saved = store.get(submitted["id"])

        assert saved["qualification"] is True
        assert broker._queued[submitted["id"]].qualification is True
    finally:
        broker.stop()
        store.close()


@pytest.mark.parametrize("params, message", [
    (_params(project="Podcast"), "only target 'Ren Qualification'"),
    (_params(staging_timeline_name="Q Other (rebuild staging)"),
     "exact staging name"),
    (_params(project_folder="/tmp/other-project"), "refuses promotion"),
    (_params(allow_drops=["video:V1"]), "refuses promotion"),
    (_params(accept_editor_changes=True), "refuses promotion"),
    (_params(supersede=[FINAL]), "refuses promotion"),
    (_params(retain=[FINAL]), "refuses promotion"),
])
def test_prepare_refuses_wrong_scope_and_unsafe_options(params, message):
    with pytest.raises(JobRefused, match=message):
        jobs.prepare(reel_promotion.KIND, params)


def test_broker_worker_allows_borrowed_promotion_from_captains_project(
        monkeypatch, lock_dir):
    (resolve, manager, captain, _project, _master, _retiring,
     _staged) = _borrowable_resolve()
    from library.tools import resolve_switch_guard
    from library.tools.resolved import server

    monkeypatch.setattr(resolve_switch_guard, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(resolve_switch_guard, "note_project_switch",
                        lambda: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.begin_qualification_session",
        lambda _name: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.end_qualification_session",
        lambda: None)

    def refuse_after_open(job, params, handle, project):
        from library.tools.resolve_deadline import DeadlineProxy

        assert job["kind"] == reel_promotion.KIND
        assert params["project"] == QUALIFICATION_PROJECT
        assert isinstance(handle, DeadlineProxy)
        assert project.GetName() == QUALIFICATION_PROJECT
        raise JobRefused("stopped after exact Qualification read-back")

    monkeypatch.setattr(reel_promotion, "_promote_and_record",
                        refuse_after_open)
    broker = object.__new__(server.Broker)
    broker._resolve_handle = lambda: resolve
    job = {"id": "borrow-from-captain", "kind": reel_promotion.KIND,
           "mode": "exclusive", "qualification": True,
           "executed": True, "project": QUALIFICATION_PROJECT,
           "timeline": STAGING, "params": _params()}

    state, result, error = server.Broker._run(broker, job)

    assert state == "rejected"
    assert result is None
    assert error == "stopped after exact Qualification read-back"
    assert manager.loaded == [QUALIFICATION_PROJECT, "Podcast (field test)"]
    assert manager.saved == ["Podcast (field test)", QUALIFICATION_PROJECT]
    assert manager.GetCurrentProject() is captain
    assert captain.GetCurrentTimeline().GetUniqueId() == "captain-timeline"


def test_broker_worker_borrows_for_fixture_setup_and_restores_captain(
        monkeypatch, lock_dir):
    from library.tools import resolve_switch_guard
    from library.tools.resolved import reel_promotion_fixture as fixture
    from library.tools.resolved import server
    from library.tools.resolve_deadline import DeadlineProxy

    resolve, manager, captain, _project, *_ = _borrowable_resolve()
    monkeypatch.setattr(resolve_switch_guard, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(resolve_switch_guard, "note_project_switch",
                        lambda: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.begin_qualification_session",
        lambda _name: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.end_qualification_session",
        lambda: None)

    def build(project, project_manager):
        assert isinstance(project, DeadlineProxy)
        assert isinstance(project_manager, DeadlineProxy)
        assert project.GetName() == QUALIFICATION_PROJECT
        return {"project": QUALIFICATION_PROJECT,
                "timelines_created": [fixture.FINAL, fixture.STAGING]}

    monkeypatch.setattr(fixture, "_build", build)
    broker = object.__new__(server.Broker)
    broker._resolve_handle = lambda: resolve
    params = {"project": QUALIFICATION_PROJECT}
    shape = jobs.prepare(fixture.KIND, params)
    job = {"id": "borrow-for-fixture", "kind": fixture.KIND,
           **shape, "params": params}

    state, result, error = server.Broker._run(broker, job)

    assert state == "done", error
    assert result["project"] == QUALIFICATION_PROJECT
    assert result["restored"]["project"] == "Podcast (field test)"
    assert result["restored"]["timeline_id"] == "captain-timeline"
    assert manager.loaded == [QUALIFICATION_PROJECT, "Podcast (field test)"]
    assert manager.saved == ["Podcast (field test)", QUALIFICATION_PROJECT]
    assert manager.GetCurrentProject() is captain
    assert captain.GetCurrentTimeline().GetUniqueId() == "captain-timeline"


def test_other_qualification_jobs_still_require_qualification_open(
        monkeypatch):
    from contextlib import nullcontext

    from library.tools import resolve_lock
    from library.tools.resolved import server

    resolve = FakeResolve(FakeProject("Podcast (field test)", ["Reel 07"]))
    broker = object.__new__(server.Broker)
    broker._resolve_handle = lambda: resolve
    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(jobs, "run", lambda *_args: pytest.fail(
        "the non-promotion qualification job must be refused first"))
    job = {"id": "qualification-session", "kind": "resolve.qualification_session",
           "mode": "exclusive", "qualification": True,
           "executed": True, "project": QUALIFICATION_PROJECT,
           "timeline": "", "params": {}}

    state, result, error = server.Broker._run(broker, job)

    assert state == "rejected"
    assert result is None
    assert "Resolve has 'Podcast (field test)'" in error


def test_broker_worker_refuses_a_nonqualification_promotion_target(
        monkeypatch):
    from contextlib import nullcontext

    from library.tools import resolve_lock
    from library.tools.resolved import server

    resolve = FakeResolve(FakeProject("Podcast (field test)", ["Reel 07"]))
    broker = object.__new__(server.Broker)
    broker._resolve_handle = lambda: pytest.fail(
        "a non-Qualification target must refuse before connecting")
    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *_args, **_kwargs: nullcontext())
    job = {"id": "wrong-target", "kind": reel_promotion.KIND,
           "mode": "exclusive", "qualification": True,
           "executed": True, "project": "Podcast (field test)",
           "timeline": STAGING, "params": _params()}

    state, result, error = server.Broker._run(broker, job)

    assert state == "rejected"
    assert result is None
    assert "may only target 'Ren Qualification'" in error
    assert resolve.GetProjectManager().GetCurrentProject().GetName() == (
        "Podcast (field test)")


def test_run_borrows_qualification_and_restores_captains_timeline(
        tmp_path, monkeypatch, lock_dir):
    (resolve, manager, captain, _project, _master, retiring,
     staged) = _borrowable_resolve()
    from library.tools import resolve_switch_guard

    monkeypatch.setattr(resolve_switch_guard, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(resolve_switch_guard, "note_project_switch",
                        lambda: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.begin_qualification_session",
        lambda _name: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.end_qualification_session",
        lambda: None)
    root = tmp_path / "qualification-record"
    monkeypatch.setattr(
        "library.tools.qualification_project.promotion_project_folder",
        lambda: root)
    monkeypatch.setattr(
        "library.tools.versions.store.init_project_repo",
        lambda _folder: {"initialised": True})
    monkeypatch.setattr(
        "library.tools.timeline_serializer.serialize_timeline_state",
        lambda resolve_mock, timeline: {
            "metadata": {"name": timeline.GetName()}, "tracks": []})
    monkeypatch.setattr(
        "library.tools.reel_replace_guard.timeline_inventory",
        lambda _project: [{"name": FINAL}, {"name": STAGING}])

    from library.tools.resolve_deadline import DeadlineProxy

    calls = []

    def promote(folder, project_name, master_name, mapping, **kwargs):
        calls.append(("promote", kwargs["resolve"]))
        assert isinstance(kwargs["resolve"], DeadlineProxy)
        assert manager.GetCurrentProject().GetName() == QUALIFICATION_PROJECT
        assert folder == str(root)
        assert project_name == QUALIFICATION_PROJECT
        assert master_name == MASTER
        assert mapping == {FINAL: STAGING}
        assert kwargs["organise"] is False
        assert kwargs.get("allow_drops") is None
        assert kwargs.get("accept_editor_changes") is None
        assert kwargs["allow_cursor_excursion"] is False
        retiring._name = FINAL + " (pre-rebuild backup)"
        staged._name = FINAL
        return {"promoted": [FINAL], "markers": {FINAL: {
            "clip_carried": [{"name": "anchor", "to_frame": 42}],
            "clip_uncarried": [],
        }}}

    def record(folder, project_name, names, *, resolve):
        calls.append(("record", resolve))
        assert isinstance(resolve, DeadlineProxy)
        assert manager.GetCurrentProject().GetName() == QUALIFICATION_PROJECT
        assert folder == str(root)
        assert project_name == QUALIFICATION_PROJECT
        assert names == [FINAL]
        review = root / "pipeline_output" / "review"
        comps = (root / "pipeline_output" / "steps" / "7_01_build_reels"
                 / "fusion_comps")
        review.mkdir(parents=True, exist_ok=True)
        comps.mkdir(parents=True, exist_ok=True)
        snapshot = review / f"{reel_promotion._safe_filename(FINAL)}.timeline.json"
        comp = comps / f"{reel_promotion._safe_filename(FINAL)}__transform.comp"
        snapshot.write_text("{}\n", encoding="utf-8")
        comp.write_text("-- comp\n", encoding="utf-8")
        return {
            "committed": True,
            "snapshots": [str(snapshot)],
            "fusion_comps": {"files": [str(comp)]},
            "snapshot_supersession": {FINAL: {
                "snapshot": snapshot.name,
                "superseded_now": [
                    (f"{reel_promotion._safe_filename(FINAL)}"
                     ".before-job-123.timeline.json")],
            }},
        }

    monkeypatch.setattr(
        "library.tools.reel_build.promote_staged_reels", promote)
    monkeypatch.setattr(
        "library.tools.versions.store.record_reel_promotion", record)
    job = {"id": "job-123", "kind": reel_promotion.KIND,
           "project": QUALIFICATION_PROJECT, "timeline": STAGING,
           "params": _params()}

    from library.tools import resolve_lock
    with resolve_lock.resolve_lease("test typed reel promotion"):
        result = jobs.run(job, resolve)

    assert [name for name, _handle in calls] == ["promote", "record"]
    assert calls[0][1] is calls[1][1]
    assert result["evidence"]["all_artifacts_exist"] is True
    assert result["evidence"]["prior_snapshot_superseded"] is True
    assert result["evidence"]["snapshot_files"] == [
        "pipeline_output/review/Q_Promo_Live.timeline.json"]
    assert result["evidence"]["fusion_comp_files"] == [
        ("pipeline_output/steps/7_01_build_reels/fusion_comps/"
         "Q_Promo_Live__transform.comp")]
    assert isinstance(result["promotion"]["markers"][FINAL][
        "clip_carried"], list)
    assert manager.loaded == [QUALIFICATION_PROJECT, "Podcast (field test)"]
    assert manager.saved == ["Podcast (field test)", QUALIFICATION_PROJECT]
    assert manager.GetCurrentProject() is captain
    assert captain.GetCurrentTimeline().GetUniqueId() == "captain-timeline"
    assert result["borrowed_from"] == {
        "project": "Podcast (field test)", "timeline": "Reel 07",
        "timeline_id": "captain-timeline", "page": "edit"}
    assert result["restored"] == result["borrowed_from"]


@pytest.mark.parametrize("missing, expected", [
    ("commit", "not committed"),
    ("snapshot", "snapshot missing"),
    ("comp", "comp export missing"),
    ("supersession", "not marked superseded"),
])
def test_record_verification_fails_closed_on_missing_evidence(
        tmp_path, missing, expected):
    root = tmp_path / "qualification-record"
    review = root / "pipeline_output" / "review"
    comp_dir = (root / "pipeline_output" / "steps" / "7_01_build_reels"
                / "fusion_comps")
    review.mkdir(parents=True)
    comp_dir.mkdir(parents=True)
    stem = reel_promotion._safe_filename(FINAL)
    snapshot = review / f"{stem}.timeline.json"
    comp = comp_dir / f"{stem}__V01_item000_c1.comp"
    baseline = review / f"{stem}.before-job.timeline.json"
    snapshot.write_text("{}\n", encoding="utf-8")
    comp.write_text("-- comp\n", encoding="utf-8")
    baseline.write_text("{}\n", encoding="utf-8")
    record = {
        "committed": True,
        "snapshots": [str(snapshot)],
        "fusion_comps": {"files": [str(comp)], "errors": [],
                         "reels": {FINAL: {"errors": []}}},
        "snapshot_supersession": {FINAL: {
            "snapshot": snapshot.name,
            "superseded_now": [baseline.name],
        }},
    }
    if missing == "commit":
        record["committed"] = False
    elif missing == "snapshot":
        record["snapshots"] = []
    elif missing == "comp":
        record["fusion_comps"]["files"] = []
    elif missing == "supersession":
        record["snapshot_supersession"][FINAL]["superseded_now"] = []

    with pytest.raises(RuntimeError, match=expected):
        reel_promotion._verified_record_evidence(
            root, review, FINAL, baseline, record)


def test_run_refuses_to_delete_current_approved_timeline_and_restores(
        monkeypatch, lock_dir):
    (resolve, manager, captain, _project, _master, _retiring,
     _staged) = _borrowable_resolve(final_is_current=True)
    from library.tools import resolve_switch_guard

    monkeypatch.setattr(resolve_switch_guard, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(resolve_switch_guard, "note_project_switch",
                        lambda: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.begin_qualification_session",
        lambda _name: None)
    monkeypatch.setattr(
        "library.tools.qualification_project.end_qualification_session",
        lambda: None)
    monkeypatch.setattr(
        "library.tools.reel_build.promote_staged_reels",
        lambda *_args, **_kwargs: pytest.fail(
            "the current approved timeline reached promotion"))
    job = {"id": "current-final", "kind": reel_promotion.KIND,
           "project": QUALIFICATION_PROJECT, "timeline": STAGING,
           "params": _params()}

    from library.tools import resolve_lock
    with (resolve_lock.resolve_lease("test refusal restores cursor"),
          pytest.raises(JobRefused, match="is current")):
        jobs.run(job, resolve)
    assert manager.GetCurrentProject() is captain
    assert captain.GetCurrentTimeline().GetUniqueId() == "captain-timeline"
    assert manager.loaded == [QUALIFICATION_PROJECT, "Podcast (field test)"]
