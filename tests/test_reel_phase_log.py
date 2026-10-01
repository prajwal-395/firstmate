"""The lane writes a per-reel phase log, so a stall has a recorded cause.

2026-09-18, batch 5: reel M05 waited 85 minutes between its plan answers
arriving (10:06:58) and its build landing (11:32:08), with a 64-minute
window with zero writes from any lane. The cause could not be recovered.
`library/tools/reel_phase_log.py` is the instrument: per reel, when the
plan was asked for, when its answers arrived, when the build started and
finished, when verification ran, when consolidation ran, plus a one-line
wait reason by the waiter - each with its OWN timestamp taken at the
moment, never inferred from file times (the `llm_requests` mtime trap).

These tests run without Resolve and without a model, on `tmp_path`
projects only.
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


def test_an_unknown_phase_is_refused_not_filed(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        phase_log.log_event(project, 5, "Reel 05", "vibes",
                            detail="not a phase")
    assert phase_log.read_events(project) == []


def test_malformed_lines_do_not_take_the_log(tmp_path):
    project = _project(tmp_path)
    phase_log.log_event(project, 5, "Reel 05", phase_log.PLAN_ASKED)
    path = os.path.join(project, "pipeline_output", "review",
                        phase_log.FILENAME)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
        handle.write(json.dumps({"no": "phase here"}) + "\n")
    assert len(phase_log.read_events(project)) == 1


def test_no_project_folder_says_so_instead_of_failing():
    event = phase_log.log_event("", 5, "Reel 05", phase_log.PLAN_ASKED)
    assert event.get("unfiled") is True


def test_span_ask_logs_plan_asked_at_the_ask(tmp_path):
    """`write_span_request` files the ask line with its own timestamp -
    the request FILE's mtime is the last rewrite and must never be read
    as the ask."""
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
    path = sem_vis.write_span_request(
        Moment(), transcript, [(10.0, 14.0)], project, fps=30.0)
    assert path.endswith("reel_span_09.json")
    events = phase_log.read_events(project)
    asks = [e for e in events if e["phase"] == phase_log.PLAN_ASKED]
    assert len(asks) == 1
    assert "span" in asks[0]["detail"]
    # The line's own timestamp is the ask moment, not the file's mtime.
    assert _at(asks[0]) >= before
    assert _at(asks[0]) <= datetime.datetime.now(datetime.timezone.utc)


def test_unanswered_span_logs_its_wait_where_it_decides(tmp_path):
    """Asked but unanswered: the waiter (`span_record_for_build`) writes
    the wait line, so a missing answer is never a silence."""
    project = _project(tmp_path)

    class Moment:
        number = 9
        timeline_name = "Reel 09"

    transcript = {"segments": [{
        "words": [
            {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
            {"word": "mind", "start": 12.5, "end": 13.0, "timed": True},
        ]}]}
    record = sem_vis.span_record_for_build(
        Moment(), transcript, [(10.0, 14.0)], project, fps=30.0,
        timeline_name="Reel 09")
    assert record["basis"] == sem_vis.SPAN_NOT_PLANNED
    phases = [e["phase"] for e in phase_log.read_events(project)]
    assert phase_log.PLAN_ASKED in phases
    assert phase_log.WAIT in phases


def _records():
    semantic = {"reel": "Reel 05 (staging)", "basis": "planned",
                "dropped": [{"element": "cutaway@2",
                             "reason": "no_readable_parameters",
                             "detail": "x"}]}
    span = {"reel": "Reel 05 (staging)", "basis": "planned",
            "dropped": [{"element": "beat@4",
                         "reason": "anchor_outside_keep_ranges",
                         "what_the_reason_means": "y"}]}
    motion = {"reel": "Reel 05 (staging)",
              "basis": "awaiting_model_answer",
              "dropped": [{"target_block_position": 3,
                           "effect_type": "push",
                           "reason": "no_model_answer"}]}
    return semantic, span, motion


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


def test_build_summary_records_measured_mic_bleed_audio_suppressions():
    payload = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_PROMOTED,
        mic_bleed_audio_suppressions=[{
            "speaker": "Craig",
            "speaking_speakers": ["Akshita"],
            "source_file": "/Craig.MXF",
            "passage": "the repeated sentence",
            "master_start": 103.0,
            "master_end": 106.0,
            "record_start_frame": 72,
            "record_end_frame": 144,
        }])

    assert payload["mic_bleed_audio_suppressions"] == [{
        "speaker": "Craig",
        "speaking_speakers": ["Akshita"],
        "source_file": "/Craig.MXF",
        "passage": "the repeated sentence",
        "master_start": 103.0,
        "master_end": 106.0,
        "record_start_frame": 72,
        "record_end_frame": 144,
    }]


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


def test_filing_a_summary_never_fails_the_build(tmp_path):
    """The contract, twice: an unwriteable project and a garbage
    payload both return unfiled lines instead of raising."""
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


def test_a_lease_wait_files_zero_as_zero_not_as_silence(tmp_path):
    """An uncontended placement still files: absent and zero differ.

    The fraction that contended is the whole queue question, so the
    denominator - every acquisition - has to be on disk.
    """
    project = _project(tmp_path)
    event = phase_log.log_lease_wait(
        project, 12, "Reel 12", purpose="place Reel 12",
        wait_seconds=0.0, waited_on="")
    assert event["phase"] == phase_log.WAIT
    assert "uncontended" in event["detail"]
    assert event["summary"]["kind"] == phase_log.LEASE_WAIT_KIND
    assert event["summary"]["wait_seconds"] == 0.0
    assert event["summary"]["contended"] is False

    held = phase_log.log_lease_wait(
        project, 12, "Reel 12", purpose="place Reel 12",
        wait_seconds=42.3, waited_on="lane-7 (pid 1 on mac): build")
    assert "42.3s" in held["detail"]
    assert "lane-7" in held["detail"]
    assert held["summary"]["contended"] is True


def test_lease_waits_add_up_to_a_contention_reading(tmp_path):
    """The reader answers the queue question either way it comes out.

    Near-zero contention must read as near-zero - "build no queue" -
    not as missing data, so zeros count as acquisitions.
    """
    project = _project(tmp_path)
    phase_log.log_lease_wait(project, 1, "Reel 01", purpose="place Reel 01",
                             wait_seconds=0.0, waited_on="")
    phase_log.log_lease_wait(project, 2, "Reel 02", purpose="place Reel 02",
                             wait_seconds=2.5,
                             waited_on="lane-7: place Reel 01")
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


def test_a_card_render_files_cached_vs_cold_not_just_a_wall(tmp_path):
    """The 3 s-vs-556 s derivation gap is cached vs cold renders, so the
    line must carry the split - a wall without the counts cannot answer
    which one a reel paid."""
    project = _project(tmp_path)
    event = phase_log.log_cards_render(
        project, 5, "Reel 05", cards_total=4, cards_cached=3,
        cards_rendered=1, wall_seconds=12.345)
    assert event["phase"] == phase_log.CARDS_RENDERED
    assert event["summary"]["cards_total"] == 4
    assert event["summary"]["cards_cached"] == 3
    assert event["summary"]["cards_rendered"] == 1
    assert event["summary"]["wall_seconds"] == 12.345
    assert "1 freshly rendered" in event["detail"]
    assert "3 cached" in event["detail"]
    events = phase_log.read_events(project)
    assert [e["phase"] for e in events] == [phase_log.CARDS_RENDERED]


def test_a_fully_cached_render_reads_as_cached_not_as_silence(tmp_path):
    """A zero-fresh render still files: absent and cached differ, and the
    reader must be able to report "render nothing" either way the log
    comes out."""
    project = _project(tmp_path)
    phase_log.log_cards_render(
        project, 5, "Reel 05", cards_total=2, cards_cached=2,
        cards_rendered=0, wall_seconds=0.4)
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
    returned - a wrapper that reorders, drops or rebuilds cards is a
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


def test_the_timed_wrapper_files_nothing_when_there_is_nothing(tmp_path):
    """A reel with no cards renders nothing: no line, no wall, and the
    input back untouched - an instrument that files zeros for work that
    never happened is another silence."""
    from library.tools import reel_build

    project = _project(tmp_path)
    assert reel_build.render_reel_cards_timed(
        project, 5, "Reel 05", [], "/remotion") == []
    assert phase_log.read_events(project) == []
