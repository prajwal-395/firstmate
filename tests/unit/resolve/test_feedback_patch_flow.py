"""The feedback-patch wiring: patched notes never reach the rebuild.

Slice 3 pins the flow through `handle_note` with the classifier and the
applier injected (both ship as separate slices): a rebuild verdict
touches nothing; a patch verdict applies, records the verified patch,
and writes the green reply AFTER the verified record - never before,
never without. The hold-back half runs through the REAL routing and
resolution code: a recorded `feedback_patch_holds` verification makes
the next routing hold the note back from planning (`BASIS_ANSWERED`),
and a timeline that moved since re-opens it.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import feedback_patch_flow as flow  # noqa: E402
from library.tools import marker_resolution as resolution  # noqa: E402
from library.tools import marker_routing as routing  # noqa: E402
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402


def _note(**kw) -> dict:
    note = {
        "note_id": "Q_Markers:timeline_marker:712",
        "source": "timeline_marker",
        "name": "feedback",
        "note": "the subtitles should read 2021",
        "text": "feedback\n\nthe subtitles should read 2021",
        "frame": 712,
        "timecode": "00:00:00:00",
        "frame_in_timeline_space": 712,
        "unplaced_reason": "",
        "target": {"kind": "moment", "basis": "timeline"},
        "outcome": "routed",
        "basis": "vocabulary",
        "steps": ["plan_subtitles"],
        "evidence": {},
    }
    note.update(kw)
    return note


@pytest.fixture
def live_snapshot(monkeypatch):
    """Stub the live read seam for `handle_note` tests only.

    Scoped per-test, never autouse: it also stubs `snapshot_hash`,
    which the shadow-store tests below must compute for real.
    """
    from library.tools import timeline_shadow as shadow

    monkeypatch.setattr(
        shadow, "read_live",
        lambda *args, **kw: {"tracks": [],
                             "markers": {"timeline": [], "notes": []}})
    monkeypatch.setattr(
        shadow, "timeline_key",
        lambda *args, **kw: ("Ren Qualification", "tid", "Q Markers"))
    monkeypatch.setattr(shadow, "snapshot_hash", lambda snapshot: "post")


@pytest.fixture
def calls():
    return []


def _decide_patch(note, *, calls):
    calls.append("decide")
    return {"verdict": "patch", "reason": "caption wording stays in cards",
            "steps": ["plan_subtitles"]}


def _decide_rebuild(note, *, calls):
    calls.append("decide")
    return {"verdict": "rebuild", "reason": "changes the order",
            "steps": ["speech_sequence"]}


def _apply(*, calls, receipt=None):
    def run(**kw):
        calls.append("apply")
        return receipt or {"backup": {"generation": 7, "hash": "pre"},
                           "patch": {"status": "committed"},
                           "outside_scope": []}
    return run


def _record(*, calls, status="resolved_verified"):
    def run(*args, **kw):
        calls.append("record")
        assert kw.get("check") == resolution.CHECK_FEEDBACK_PATCH
        return {"record": {"status": status}}
    return run


def _reply(*, calls):
    def run(**kw):
        calls.append("reply")
        return {"frame": 712}
    return run


def _outcome_writer(*, calls):
    from types import SimpleNamespace

    def record_plan(*args, **kwargs):
        calls.append("plan")
        return {"status": "planned"}

    def record_blocked(*args, **kwargs):
        calls.append("blocked")
        return {"status": "blocked"}

    return SimpleNamespace(record_plan=record_plan,
                           record_blocked=record_blocked)


def _live():
    from types import SimpleNamespace

    return (SimpleNamespace(GetName=lambda: "Ren Qualification"),
            SimpleNamespace(GetName=lambda: "Q Markers"))


# ── The wiring ───────────────────────────────────────────────────────

def test_rebuild_verdict_touches_nothing(calls, live_snapshot):
    out = flow.handle_note(
        routed_note=_note(), project_folder="/tmp/nowhere",
        operations=[], scope={}, idempotency_key="k",
        decide=lambda note: _decide_rebuild(note, calls=calls),
        apply_patch=_apply(calls=calls), record=_record(calls=calls),
        write_reply=_reply(calls=calls),
        record_outcome=_outcome_writer(calls=calls))
    assert out["action"] == flow.ACTION_REBUILD
    assert out["outcome"]["status"] == "planned"
    assert calls == ["decide", "plan"]


def test_patch_records_then_replies_in_order(calls, live_snapshot):
    project, timeline = _live()
    out = flow.handle_note(
        routed_note=_note(), project_folder="/tmp/nowhere",
        operations=[{"op": "marker.add", "frame": 713}],
        scope={"clips": [], "marker_frames": [713], "clip_markers": []},
        idempotency_key="k", project=project, timeline=timeline,
        decide=lambda note: _decide_patch(note, calls=calls),
        apply_patch=_apply(calls=calls), record=_record(calls=calls),
        write_reply=_reply(calls=calls))
    assert out["action"] == flow.ACTION_PATCHED
    assert calls == ["decide", "apply", "record", "reply"]


def test_apply_refusal_records_nothing(calls, live_snapshot):
    project, timeline = _live()

    def refuse(**kw):
        calls.append("apply")
        raise RuntimeError("the patch moved outside its scope")

    with pytest.raises(RuntimeError):
        flow.handle_note(
            routed_note=_note(), project_folder="/tmp/nowhere",
            operations=[], scope={}, idempotency_key="k",
            project=project, timeline=timeline,
            decide=lambda note: _decide_patch(note, calls=calls),
            apply_patch=refuse, record=_record(calls=calls),
            write_reply=_reply(calls=calls),
            record_outcome=_outcome_writer(calls=calls))
    assert calls == ["decide", "apply", "blocked"]


def test_unverified_record_writes_no_reply(calls, live_snapshot):
    project, timeline = _live()
    with pytest.raises(flow.FlowNotReady):
        flow.handle_note(
            routed_note=_note(), project_folder="/tmp/nowhere",
            operations=[], scope={}, idempotency_key="k",
            project=project, timeline=timeline,
            decide=lambda note: _decide_patch(note, calls=calls),
            apply_patch=_apply(calls=calls),
            record=_record(calls=calls, status="addressed_unverified"),
            write_reply=_reply(calls=calls))
    assert calls == ["decide", "apply", "record"]


def test_patch_without_live_handles_refuses(calls, live_snapshot):
    with pytest.raises(flow.FlowNotReady):
        flow.handle_note(
            routed_note=_note(), project_folder="/tmp/nowhere",
            operations=[], scope={}, idempotency_key="k",
            decide=lambda note: _decide_patch(note, calls=calls),
            apply_patch=_apply(calls=calls), record=_record(calls=calls),
            write_reply=_reply(calls=calls),
            record_outcome=_outcome_writer(calls=calls))
    assert calls == ["decide", "blocked"]


def test_readback_failure_records_applied_patch_as_unverified(
        calls, live_snapshot):
    from types import SimpleNamespace

    recorded = {}

    def record_blocked(*args, **kwargs):
        calls.append("blocked")
        recorded.update(kwargs)
        return {"status": "blocked"}

    outcomes = SimpleNamespace(record_plan=lambda *args, **kwargs: {},
                               record_blocked=record_blocked)

    def fail_get_name():
        raise RuntimeError("Resolve read-back unavailable")

    project = SimpleNamespace(GetName=fail_get_name)
    timeline = SimpleNamespace(GetName=lambda: "Q Markers")
    with pytest.raises(RuntimeError, match="Resolve read-back unavailable"):
        flow.handle_note(
            routed_note=_note(), project_folder="/tmp/nowhere",
            operations=[{"op": "marker.add", "frame": 713}],
            scope={"clips": [], "marker_frames": [713], "clip_markers": []},
            idempotency_key="k", project=project, timeline=timeline,
            decide=lambda note: _decide_patch(note, calls=calls),
            apply_patch=_apply(calls=calls), record=_record(calls=calls),
            write_reply=_reply(calls=calls), record_outcome=outcomes)

    assert calls == ["decide", "apply", "blocked"]
    assert "patch applied" in recorded["reason"]
    assert recorded["summary"] == (
        "The patch was applied, but the timeline read-back failed; "
        "completion is unverified.")
    assert recorded["reel_versions"]["before"]["generation"] == 7
    assert recorded["reel_versions"]["after"]["generation"] is None


# ── The green reply sits beside the blue, never on it ───────────────

def _fake_timeline(markers):
    from types import SimpleNamespace

    return SimpleNamespace(
        GetName=lambda: "T",
        GetStartFrame=lambda: 1000,
        GetMarkers=lambda: dict(markers))


def test_reply_lands_one_frame_past_the_note(monkeypatch):
    from library.tools import feedback_patch_flow as flow_mod
    from library.tools import marker_feedback

    seen = {}
    monkeypatch.setattr(
        marker_feedback, "place_reply_marker",
        lambda timeline, frame, *args, **kw: seen.update(frame=frame)
        or {"frame": frame})
    timeline = _fake_timeline({40: {"color": "Blue"}})
    out = flow_mod.default_reply_writer(
        project=object(), timeline=timeline,
        routed_note=_note(frame=1040, frame_in_timeline_space=40),
        report="done", summary="done", idempotency_key="k")
    assert out["frame"] == 1041
    assert seen["frame"] == 1041


def test_reply_refuses_an_occupied_frame(monkeypatch):
    from library.tools import feedback_patch_flow as flow_mod

    timeline = _fake_timeline({40: {"color": "Blue"},
                               41: {"color": "Green"}})
    with pytest.raises(flow.FlowNotReady):
        flow_mod.default_reply_writer(
            project=object(), timeline=timeline,
            routed_note=_note(frame=1040, frame_in_timeline_space=40),
            report="done", summary="done", idempotency_key="k")


# ── The check ────────────────────────────────────────────────────────

def test_patch_check_passes_only_on_same_hash():
    assert resolution.verify(
        resolution.CHECK_FEEDBACK_PATCH,
        {"post_hash": "abc", "head_hash": "abc"})[0] is True
    passed, evidence = resolution.verify(
        resolution.CHECK_FEEDBACK_PATCH,
        {"post_hash": "abc", "head_hash": "def"})
    assert passed is False
    assert "moved" in evidence["reason"]


def test_patch_check_fails_without_a_read():
    passed, evidence = resolution.verify(
        resolution.CHECK_FEEDBACK_PATCH, {"post_hash": "abc"})
    assert passed is False
    assert "head_hash" in evidence["reason"]


def test_resolve_without_handles_keeps_the_marker(tmp_path):
    note = _note()
    out = resolution.resolve_note(
        str(tmp_path), note, action="patched in place",
        rationale="stays in cards", check=resolution.CHECK_FEEDBACK_PATCH,
        measured={"post_hash": "abc", "head_hash": "abc",
                  "resolve_project": "Ren Qualification",
                  "timeline_id": "tid"},
        verifier="test")
    assert out["record"]["status"] == "resolved_verified"
    assert out["record"]["marker_removed"] is False
    assert out["marker_touched"] is False


# ── The hold-back, end to end through real routing ───────────────────

RAW = {
    "source": "timeline_marker",
    "name": "feedback",
    "note": "the subtitles should read 2021",
    "text": "feedback\n\nthe subtitles should read 2021",
    "frame": 712,
    "timecode": "00:00:00:00",
    "frame_in_timeline_space": 712,
    "unplaced_reason": "",
    "color": "Blue",
    "duration_frames": 1,
    "custom_data": {},
    "custom_data_raw": "",
    "clips": [],
    "attachments": [],
    "read_at": "",
}

TIMELINE = "Q Markers"
RESOLVE_PROJECT = "QualificationUnitTest"
TIMELINE_ID = "unit-test-timeline"


@pytest.fixture
def answered_project(tmp_path, monkeypatch):
    """A project whose one note carries a verified patch record."""
    from library.tools.timeline_shadow import ShadowStore, snapshot_hash

    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    folder = str(tmp_path / "project")
    pull_name = f"{TIMELINE}.20251007T000000Z.markers.json"
    pull_path = ProjectLayout(folder).write_path(
        Area.MARKER_FEEDBACK, pull_name)
    pull_path.write_text(json.dumps({
        "format": "marker_feedback/1",
        "pulled_at": "2025-10-07T00:00:00+00:00",
        "timeline": TIMELINE,
        "timeline_fps": 24.0,
        "timeline_start_frame": 0,
        "notes": [RAW],
    }), encoding="utf-8")
    snapshot = {"tracks": [], "markers": {"timeline": [], "notes": []}}
    store = ShadowStore()
    store.record(project=RESOLVE_PROJECT, timeline_id=TIMELINE_ID,
                 timeline_name=TIMELINE, snapshot=snapshot,
                 source="observed", expected_head=0)
    post_hash = snapshot_hash(snapshot)
    note_id = routing._note_id(RAW, TIMELINE, str(pull_path))
    resolution.record_resolution(
        folder, {"note_id": note_id, "text": RAW["text"]},
        resolution.STATUS_RESOLVED_VERIFIED,
        action="patched in place", rationale="stays in cards",
        check=resolution.CHECK_FEEDBACK_PATCH,
        evidence={"params": {"resolve_project": RESOLVE_PROJECT,
                             "timeline_id": TIMELINE_ID,
                             "post_hash": post_hash}},
        verifier="test")
    return folder, str(pull_path), store, snapshot


def test_verified_patch_holds_the_note_from_planning(answered_project):
    from library.tools import edit_spec

    folder, pull_path, _store, _snapshot = answered_project
    pending = edit_spec.pending_note_states(
        folder, rows=[], include_untranslated=False)
    link = routing.edit_link_id(RAW, TIMELINE, pull_path)
    assert pending[link]["state"] == "answered"
    routed = routing.route_note(
        RAW, TIMELINE, pull_path, timeline_fps=24.0,
        timeline_start_frame=0,
        pending_edit_spec=pending.get(link))
    assert routed.outcome == routing.OUTCOME_UNROUTED
    assert routed.basis == routing.BASIS_ANSWERED


def test_moved_timeline_reopens_the_note(answered_project):
    from library.tools import edit_spec

    folder, pull_path, store, snapshot = answered_project
    moved = {"tracks": [{"type": "video", "index": 1, "name": "V1",
                         "clips": []}],
             "markers": {"timeline": [], "notes": []}}
    assert moved != snapshot
    store.record(project=RESOLVE_PROJECT, timeline_id=TIMELINE_ID,
                 timeline_name=TIMELINE, snapshot=moved,
                 source="observed", expected_head=1)
    pending = edit_spec.pending_note_states(
        folder, rows=[], include_untranslated=False)
    link = routing.edit_link_id(RAW, TIMELINE, pull_path)
    assert pending.get(link, {}).get("state") != "answered", (
        "a timeline that moved since the patch must re-open the note, "
        "never hold it answered")
