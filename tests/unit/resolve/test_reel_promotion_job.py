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
