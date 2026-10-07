"""The broker's typed promotion job cannot target user projects or run code."""

import pytest

from library.tools.resolved import jobs, reel_promotion
from library.tools.resolved.jobs import JobRefused, QUALIFICATION_PROJECT
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


def test_run_refuses_unexpected_open_project_before_promotion(monkeypatch):
    resolve = FakeResolve(FakeProject("Podcast", [FINAL, STAGING, MASTER]))
    monkeypatch.setattr(
        reel_promotion, "run",
        lambda *_args: pytest.fail("the wrong project reached the runner"))
    job = {"id": "wrong-project", "kind": reel_promotion.KIND,
           "project": QUALIFICATION_PROJECT, "timeline": STAGING,
           "params": _params()}

    with pytest.raises(JobRefused, match="not 'Ren Qualification'"):
        jobs.run(job, resolve)


def test_run_executes_promotion_and_record_on_same_deadline_handle(
        tmp_path, monkeypatch):
    retiring = FakeTimeline(FINAL)
    staged = FakeTimeline(STAGING)
    project = FakeProject(QUALIFICATION_PROJECT,
                          [FakeTimeline(MASTER), retiring, staged])
    resolve = FakeResolve(project)
    monkeypatch.setattr(
        "library.tools.resolve_lock.exclusive_held", lambda: True)
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
                    f"{reel_promotion._safe_filename(FINAL)}"
                    ".before-job-123.timeline.json"],
            }},
        }

    monkeypatch.setattr(
        "library.tools.reel_build.promote_staged_reels", promote)
    monkeypatch.setattr(
        "library.tools.versions.store.record_reel_promotion", record)
    job = {"id": "job-123", "kind": reel_promotion.KIND,
           "project": QUALIFICATION_PROJECT, "timeline": STAGING,
           "params": _params()}

    result = jobs.run(job, resolve)

    assert [name for name, _handle in calls] == ["promote", "record"]
    assert calls[0][1] is calls[1][1]
    assert result["evidence"]["all_artifacts_exist"] is True
    assert result["evidence"]["prior_snapshot_superseded"] is True
    assert result["evidence"]["snapshot_files"] == [
        "pipeline_output/review/Q_Promo_Live.timeline.json"]
    assert result["evidence"]["fusion_comp_files"] == [
        "pipeline_output/steps/7_01_build_reels/fusion_comps/"
        "Q_Promo_Live__transform.comp"]
    assert isinstance(result["promotion"]["markers"][FINAL][
        "clip_carried"], list)


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


def test_run_refuses_to_delete_the_current_approved_timeline(monkeypatch):
    retiring = FakeTimeline(FINAL)
    project = FakeProject(QUALIFICATION_PROJECT,
                          [FakeTimeline(MASTER), retiring,
                           FakeTimeline(STAGING)], current=retiring)
    resolve = FakeResolve(project)
    monkeypatch.setattr(
        "library.tools.resolve_lock.exclusive_held", lambda: True)
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

    with pytest.raises(JobRefused, match="is current"):
        jobs.run(job, resolve)
