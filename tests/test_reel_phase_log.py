"""The lane writes a per-reel phase log, so a stall has a recorded cause.

Each event carries its OWN timestamp taken at the moment, never inferred
from file times. Runs on `tmp_path` only, no Resolve, no model. History
(the M05 85-minute stall): docs/evidence/reel_phase_log.md.
"""

from __future__ import annotations

import datetime
import json
import os

import pytest

from library.tools import reel_phase_log as phase_log
from library.tools import reel_semantic_visual as sem_vis


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    return str(project)


def _at(event):
    return datetime.datetime.fromisoformat(event["at"])


def test_events_carry_their_own_timestamps_in_filed_order(tmp_path):
    project = _project(tmp_path)
    first = phase_log.log_event(project, 5, "Reel 05", phase_log.PLAN_ASKED,
                                detail="semantic ask written")
    second = phase_log.log_event(project, 5, "Reel 05",
                                 phase_log.ANSWERS_ARRIVED,
                                 detail="semantic=planned span=planned")
    events = phase_log.read_events(project)
    assert [e["phase"] for e in events] == [
        phase_log.PLAN_ASKED, phase_log.ANSWERS_ARRIVED]
    # Own timestamps: parseable ISO, taken at the call, monotonic.
    assert _at(second) >= _at(first)
    for event in events:
        assert event["format"] == phase_log.FORMAT
        assert event["reel_number"] == 5
        assert event["reel"] == "Reel 05"


def test_latest_event_filters_to_one_plan_channel(tmp_path):
    project = _project(tmp_path)
    motion = phase_log.log_event(
        project, 5, "Reel 05", phase_log.PLAN_ASKED,
        detail="motion ask written: reel_motion_05.json")
    semantic = phase_log.log_event(
        project, 5, "Reel 05", phase_log.PLAN_ASKED,
        detail="semantic ask written: reel_semantic_05.json")

    selected = phase_log.latest_event(
        project, 5, phase_log.PLAN_ASKED,
        detail_prefix="motion ask written: reel_motion_05.json")

    assert selected == motion
    assert selected != semantic


def test_the_log_holds_only_well_formed_events(tmp_path):
    """An unknown phase is refused, not filed; malformed lines on disk
    do not take the log down."""
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        phase_log.log_event(project, 5, "Reel 05", "vibes",
                            detail="not a phase")
    assert phase_log.read_events(project) == []
    phase_log.log_event(project, 5, "Reel 05", phase_log.PLAN_ASKED)
    path = os.path.join(project, "pipeline_output", "review",
                        phase_log.FILENAME)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
        handle.write(json.dumps({"no": "phase here"}) + "\n")
    assert len(phase_log.read_events(project)) == 1


def test_an_unanswered_span_logs_its_ask_and_its_wait(tmp_path):
    """The waiter (`span_record_for_build`) files the ask line with its
    own timestamp - the request FILE's mtime is the last rewrite and must
    never be read as the ask - and the wait line, so a missing answer is
    never a silence."""
    project = _project(tmp_path)

    class Moment:
        number = 9
        timeline_name = "Reel 09"

    transcript = {"segments": [{
        "words": [
            {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
            {"word": "mind", "start": 12.5, "end": 13.0, "timed": True},
        ]}]}
    before = datetime.datetime.now(datetime.timezone.utc)
    record = sem_vis.span_record_for_build(
        Moment(), transcript, [(10.0, 14.0)], project, fps=30.0,
        timeline_name="Reel 09")
    assert record["basis"] == sem_vis.SPAN_NOT_PLANNED
    events = phase_log.read_events(project)
    asks = [e for e in events if e["phase"] == phase_log.PLAN_ASKED]
    assert len(asks) == 1
    assert "span" in asks[0]["detail"]
    assert _at(asks[0]) >= before
    assert _at(asks[0]) <= datetime.datetime.now(datetime.timezone.utc)
    assert phase_log.WAIT in [e["phase"] for e in events]


def test_build_summary_absent_rather_than_estimated():
    """No pieces, no fiction: every field the caller could not supply
    is None or empty, never a zero that reads as measured."""
    payload = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_LEFT_ALONE,
        staging="Reel 05 (staging)", final="Reel 05 - slug",
        decision="leaving alone: digests match")
    assert payload["answers"] == {"semantic": None, "span": None,
                                  "motion": None}
    assert payload["dropped"] == {"semantic": [], "span": [],
                                  "motion": []}
    assert payload["draw_gain"] == {"gain": None, "source": None,
                                    "disagrees_with_fallback": None}
    assert payload["captions"] == {"planned": None, "linked": None,
                                   "link_warnings": None}
    assert payload["verify"] is None
    assert payload["drift_end"] is None
    assert payload["retired_to"] is None
    assert payload["cards"] == []
    assert payload["mic_bleed_audio_suppressions"] == []
    assert payload["answers_owed"] == []


def test_drops_cap_bounds_a_pathological_line():
    record = {"basis": "planned",
              "dropped": [{"element": f"e{i}", "reason": "r"}
                          for i in range(30)]}
    drops = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_PROMOTED)["dropped"]
    assert drops["semantic"] == []
    payload = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_PROMOTED,
        semantic_record=record)["dropped"]["semantic"]
    assert len(payload) == phase_log.MAX_DROPS_PER_CHANNEL + 1
    assert payload[-1] == "(+10 more)"


def test_filing_never_fails_the_build(tmp_path):
    """No project folder, an unwriteable project and a garbage payload
    all return unfiled lines instead of raising."""
    assert phase_log.log_event(
        "", 5, "Reel 05", phase_log.PLAN_ASKED).get("unfiled") is True
    project = _project(tmp_path)
    review = os.path.join(project, "pipeline_output", "review")
    os.rmdir(review)
    with open(review, "w", encoding="utf-8") as handle:
        handle.write("a file where the review dir belongs")
    event = phase_log.file_build_summary(
        project, 5, "Reel 05", {"outcome": "promoted"})
    assert event.get("unfiled") is True
    event = phase_log.file_build_summary(
        project, 5, "Reel 05", "not a dict")  # type: ignore[arg-type]
    assert event.get("unfiled") is True


def test_summarize_surfaces_the_summary_on_both_names():
    """Staging and final are two slots for one reel; the summary filed
    under the final name attaches to both, by reel number."""
    base = datetime.datetime(2026, 9, 18, 12, 0, 0,
                             tzinfo=datetime.timezone.utc)

    def at(minutes_after):
        return (base + datetime.timedelta(
            minutes=minutes_after)).isoformat()

    def line(reel, phase, minute, summary=None):
        event = {"format": phase_log.FORMAT, "reel_number": 5,
                 "reel": reel, "phase": phase, "at": at(minute),
                 "detail": ""}
        if summary is not None:
            event["summary"] = summary
        return event

    first = {"outcome": "promoted", "final": "Reel 05 - slug"}
    second = {"outcome": "promoted", "final": "Reel 05 - slug",
              "verify": {"passed": True, "errors": 0}}
    events = [
        line("Reel 05 (staging)", phase_log.ANSWERS_ARRIVED, 0),
        line("Reel 05 (staging)", phase_log.BUILD_FINISHED, 8,
             summary=None),
        line("Reel 05 - slug", phase_log.BUILD_SUMMARY, 9,
             summary=first),
        line("Reel 05 - slug", phase_log.BUILD_SUMMARY, 10,
             summary=second),
        line("Reel 05 - slug", phase_log.CONSOLIDATED, 11),
    ]
    summary = phase_log.summarize(events)
    # Latest filed wins, on every slot sharing the number.
    assert summary["Reel 05 (staging)"]["build_summary"] == second
    assert summary["Reel 05 - slug"]["build_summary"] == second
    assert summary["Reel 05 - slug"]["build_summary_at"] == at(10)


def test_conformance_rows_slice_the_report_the_gate_wrote(tmp_path):
    project = _project(tmp_path)
    assert phase_log.conformance_rows(project) == {}
    report = {
        "reels": [{
            "reel_name": "Reel 05 (staging)",
            "reel_number": 5,
            "plan_seconds": 84.3,
            "actual_frames": 2016,
            "captions": "57/57",
            "uncaptioned_seconds": 0.0,
            "errors": 0, "warnings": 1,
            "findings": [{"finding_class": "F7",
                          "reel": "Reel 05 (staging)",
                          "message": "m", "severity": "warning"}],
        }],
    }
    path = os.path.join(project, "pipeline_output", "review",
                        phase_log.CONFORMANCE_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)
    rows = phase_log.conformance_rows(project)
    assert rows["Reel 05 (staging)"] == {
        "errors": 0, "warnings": 1, "finding_classes": ["F7"],
        "captions_expected": 57, "captions_actual": 57,
        "uncaptioned_seconds": 0.0, "plan_seconds": 84.3,
        "actual_frames": 2016}
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("not json")
    assert phase_log.conformance_rows(project) == {}


def test_build_side_helpers_shape_slices_and_file(tmp_path):
    """The rebuild's own helpers, without Resolve: the verify slice
    keeps the verdict when the report is unreadable, the owed lookup
    tries every spelling, and the filing helper never raises."""
    from library.tools import reel_build

    row = {"errors": 0, "warnings": 2, "finding_classes": ["F7"],
           "captions_expected": 9, "captions_actual": 9,
           "uncaptioned_seconds": 0.4, "plan_seconds": 29.6,
           "actual_frames": 707}
    payload = reel_build._verify_payload(row, passed=True)
    assert payload["passed"] is True
    assert payload["finding_classes"] == ["F7"]
    assert payload["report"] == phase_log.CONFORMANCE_REPORT_REL
    assert payload["refusal"] == ""
    blind = reel_build._verify_payload(None, passed=False,
                                       refusal="F17+F8")
    assert blind["errors"] is None
    assert blind["refusal"] == "F17+F8"

    awaiting = {"reels": [
        {"reel": "Reel 05 (staging)", "layers": ["motion"]}]}
    assert reel_build._owed_layers(
        awaiting, "Reel 05 - slug", "Reel 05 (staging)") == ["motion"]
    assert reel_build._owed_layers(awaiting, "Reel 99") == []

    project = _project(tmp_path)
    facts = {"staging": "Reel 05 (staging)", "final": "Reel 05 - slug",
             "number": 5,
             "decision": {"reason": "rebuilt: the plan changed"},
             "answers": {"semantic": "planned", "span": "planned",
                         "motion": "awaiting_model_answer"}}
    reel_build._file_reel_summary(
        project, number=5, name="Reel 05 - slug", facts=facts,
        outcome="promoted", verify=payload,
        gain_record={"gain": 1.0, "source": "fallback",
                     "disagrees_with_fallback": False})
    slot = phase_log.summarize(
        phase_log.read_events(project))["Reel 05 - slug"]
    assert slot["build_summary"]["outcome"] == "promoted"
    assert slot["build_summary"]["draw_gain"]["source"] == "fallback"
    # Garbage facts still never raise.
    reel_build._file_reel_summary(
        project, number=5, name="Reel 05 - slug", facts=None,  # type: ignore[arg-type]
        outcome="promoted")


def test_lease_waits_add_up_to_a_contention_reading(tmp_path):
    """The reader answers the queue question either way it comes out.

    An uncontended placement still files (absent and zero differ), so
    near-zero contention reads as near-zero, not as missing data.
    """
    project = _project(tmp_path)
    event = phase_log.log_lease_wait(
        project, 1, "Reel 01", purpose="place Reel 01",
        wait_seconds=0.0, waited_on="")
    assert event["phase"] == phase_log.WAIT
    assert "uncontended" in event["detail"]
    assert event["summary"]["kind"] == phase_log.LEASE_WAIT_KIND
    assert event["summary"]["contended"] is False
    held = phase_log.log_lease_wait(
        project, 2, "Reel 02", purpose="place Reel 02",
        wait_seconds=2.5, waited_on="lane-7: place Reel 01")
    assert "2.5s" in held["detail"] and "lane-7" in held["detail"]
    assert held["summary"]["contended"] is True
    phase_log.log_lease_wait(project, 3, "Reel 03", purpose="place Reel 03",
                             wait_seconds=1.0,
                             waited_on="lane-7: place Reel 01")
    report = phase_log.summarize_lease_waits(
        phase_log.read_events(project))
    assert report["acquisitions"] == 3
    assert report["contended"] == 2
    assert report["fraction_contended"] == 0.667
    assert report["total_wait_seconds"] == 3.5
    assert report["mean_wait_seconds"] == 1.167
    assert report["max_wait_seconds"] == 2.5
    assert report["by_holder"]["lane-7: place Reel 01"] == {
        "acquisitions": 2, "total_wait_seconds": 3.5}
    assert phase_log.summarize_lease_waits([])["acquisitions"] == 0


def test_a_fully_cached_render_reads_as_cached_not_as_silence(tmp_path):
    """The line carries the cached/cold split, not just a wall (the
    3 s-vs-556 s gap), and a zero-fresh render still files: absent and
    cached differ."""
    project = _project(tmp_path)
    event = phase_log.log_cards_render(
        project, 5, "Reel 05", cards_total=2, cards_cached=2,
        cards_rendered=0, wall_seconds=0.4)
    assert event["phase"] == phase_log.CARDS_RENDERED
    assert event["summary"]["cards_cached"] == 2
    assert event["summary"]["cards_rendered"] == 0
    assert "2 cached" in event["detail"]
    phase_log.log_cards_render(
        project, 6, "Reel 06", cards_total=3, cards_cached=0,
        cards_rendered=3, wall_seconds=556.0)
    report = phase_log.summarize_cards_renders(
        phase_log.read_events(project))
    assert report["renders"] == 2
    assert report["cold_renders"] == 1
    assert report["fraction_cold"] == 0.5
    assert report["cards_total"] == 5
    assert report["cards_cached"] == 2
    assert report["cards_rendered"] == 3
    assert report["total_wall_seconds"] == 556.4
    assert report["max_wall_seconds"] == 556.0
    assert report["max_render"]["reel"] == "Reel 06"
    assert phase_log.summarize_cards_renders([])["renders"] == 0


def test_the_timed_wrapper_returns_the_renderer_output_untouched(tmp_path):
    """Timing only: the wrapper must hand back exactly what the renderer
    returned, and files nothing for an empty reel - a wrapper that reorders, drops or rebuilds cards is a
    content change wearing an instrument's clothes."""
    from library.tools import full_frame_element as cards_mod
    from library.tools import reel_build

    project = _project(tmp_path)
    planned = [
        {"render_name": "reel_05_head", "rendered_path": "/disk/head.mov"},
        {"render_name": "reel_05_tail", "rendered_path": ""},
    ]
    drawn = [dict(card, rendered_path=f"/disk/{card['render_name']}.mov")
             for card in planned]

    def fake_render(cards, remotion_dir, output_dir, project_folder=""):
        assert list(cards) == planned
        return drawn

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cards_mod, "render_reel_cards", fake_render)
    try:
        out = reel_build.render_reel_cards_timed(
            project, 5, "Reel 05", planned, "/remotion")
    finally:
        monkeypatch.undo()
    assert out == drawn
    assert out is drawn
    events = phase_log.read_events(project)
    assert len(events) == 1
    summary = events[0]["summary"]
    assert summary["cards_total"] == 2
    assert summary["cards_cached"] == 1
    assert summary["cards_rendered"] == 1
    assert summary["wall_seconds"] >= 0.0
    # No cards: no line, no wall - zeros for work that never happened
    # would be another silence.
    assert reel_build.render_reel_cards_timed(
        project, 6, "Reel 06", [], "/remotion") == []
    assert len(phase_log.read_events(project)) == 1
