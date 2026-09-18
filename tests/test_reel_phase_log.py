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


def test_an_unknown_phase_is_refused_not_filed(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        phase_log.log_event(project, 5, "Reel 05", "vibes",
                            detail="not a phase")
    assert phase_log.read_events(project) == []


def test_a_wait_names_its_reason(tmp_path):
    project = _project(tmp_path)
    event = phase_log.log_wait(project, 5, "Reel 05",
                               "no model answer on file "
                               "(reel_semantic_05.json) - building "
                               "without semantic visuals")
    assert event["phase"] == phase_log.WAIT
    assert "reel_semantic_05.json" in event["detail"]
    assert phase_log.read_events(project)[0]["detail"] == event["detail"]


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


def test_span_ask_with_no_words_logs_a_wait_not_an_ask(tmp_path):
    """A reel with no timed words has no ask; the log says why rather
    than going silent."""
    project = _project(tmp_path)

    class Moment:
        number = 9
        timeline_name = "Reel 09"

    assert sem_vis.write_span_request(
        Moment(), {"segments": []}, [(10.0, 14.0)], project,
        fps=30.0) == ""
    events = phase_log.read_events(project)
    assert [e["phase"] for e in events] == [phase_log.WAIT]
    assert "no timed words" in events[0]["detail"]


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


def test_m05_walkthrough_the_log_names_the_64_minute_silence():
    """The test of this work: had the log existed on 2026-09-18, what
    line would name M05's 64-minute silence?

    Answers arrived 10:06:58; the build started ~85 minutes later with
    no engine event in between (10:17-11:21 zero writes from any lane).
    The summary must put the whole gap in answers-to-build with no
    engine wait recorded between it - locating the stall upstream of
    the engine (worker loop, model turns, another lane's Resolve
    lease) instead of inside derivation or placement.
    """
    base = datetime.datetime(2026, 9, 18, 10, 6, 58,
                             tzinfo=datetime.timezone.utc)

    def at(minutes_after):
        return (base + datetime.timedelta(
            minutes=minutes_after)).isoformat()

    events = [
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.PLAN_ASKED, "at": at(-17.7), "detail": "ask"},
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.ANSWERS_ARRIVED, "at": at(0),
         "detail": "semantic=planned span=planned motion=planned"},
        # The 64 silent minutes file NOTHING - that is the point: no
        # wait line lands between the answers and the build start.
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.BUILD_STARTED, "at": at(84.2),
         "detail": "placing; 5052.0s since answers arrived "
                   "(derivation only, no engine wait recorded between)"},
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.BUILD_FINISHED, "at": at(85.2),
         "detail": "placed"},
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.VERIFIED, "at": at(86.0), "detail": "passed"},
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.CONSOLIDATED, "at": at(87.0),
         "detail": "promoted"},
    ]
    summary = phase_log.summarize(events)["Reel 05"]
    # The whole 85-minute stall sits in answers-to-build ...
    assert summary["seconds_answers_to_build"] == pytest.approx(5052.0)
    # ... the Resolve placement itself took about a minute ...
    assert summary["seconds_build"] == pytest.approx(60.0)
    # ... and no engine wait was recorded inside the gap, so the next
    # investigation knows the silence was upstream of the engine
    # rather than inside it.
    assert summary["waits_between_answers_and_build"] == []
    assert set(summary["phases"]) == {
        phase_log.PLAN_ASKED, phase_log.ANSWERS_ARRIVED,
        phase_log.BUILD_STARTED, phase_log.BUILD_FINISHED,
        phase_log.VERIFIED, phase_log.CONSOLIDATED}


def test_rebuild_records_every_phase_it_promises():
    """Wiring pin: without Resolve the full rebuild cannot run here, so
    this asserts the call sites exist where the phases happen - the
    ask writers log the ask, the rebuild loop logs answers/build, and
    both promotion paths log verification and consolidation."""
    import inspect

    from library.tools import reel_build, reel_look
    from library.steps.step_7_02_verify_reels import step as verify_node

    assert "PLAN_ASKED" in inspect.getsource(
        sem_vis.write_request)
    assert "PLAN_ASKED" in inspect.getsource(
        sem_vis.write_span_request)
    assert "PLAN_ASKED" in inspect.getsource(
        reel_look.write_motion_request)
    body = inspect.getsource(reel_build.rebuild_reels_in_project)
    for phase in ("ANSWERS_ARRIVED", "BUILD_STARTED", "BUILD_FINISHED",
                  "VERIFIED", "CONSOLIDATED"):
        assert phase in body, f"rebuild loop never logs {phase}"
    node = inspect.getsource(verify_node.verify_reels)
    assert "VERIFIED" in node
    assert "CONSOLIDATED" in node


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


def test_build_summary_files_what_the_build_computed(tmp_path):
    """The payload round-trips: answers, drops, trims, gain, captions,
    cards, verify slice and drift bracket filed under one line."""
    project = _project(tmp_path)
    semantic, span, motion = _records()
    payload = phase_log.assemble_summary(
        outcome=phase_log.OUTCOME_PROMOTED,
        staging="Reel 05 (staging)", final="Reel 05 - slug",
        decision="rebuilt: the plan changed",
        answers={"semantic": "planned", "span": "planned",
                 "motion": "awaiting_model_answer"},
        semantic_record=semantic, span_record=span,
        motion_record=motion,
        captain_trims={"applied": [{"span_index": 2, "edge": "start",
                                    "was": [10.0, 20.0],
                                    "now": [10.5, 20.0]}],
                       "held": [{"span_index": 3, "edge": "end"}]},
        keep_exclusions=[(1.0, 2.5, "lc-0016")],
        draw_gain_record={"gain": 2.0, "source": "measured",
                          "disagrees_with_fallback": True},
        captions={"planned": 57, "linked": 57, "link_warnings": 0},
        cards=[{"placement": "tail", "render_name": "end_card",
                "reel_start_frame": 1900, "duration_frames": 48}],
        suppressed_overlays=["seg-9"],
        overlay_sweep={"passed": True, "checked": 4},
        transition_placements=2,
        has_freeze_tail=True,
        verify={"passed": True, "errors": 0, "warnings": 1,
                "finding_classes": ["F7"],
                "captions_expected": 57, "captions_actual": 57,
                "uncaptioned_seconds": 0.0,
                "report": phase_log.CONFORMANCE_REPORT_REL,
                "refusal": ""},
        retired_to="Reel 05 - slug (archived round 003)",
        markers={"carried": [{}, {}], "uncarried": []},
        version_control={"committed": True, "commit": "abc123",
                         "files": ["a", "b"]},
        drift_end={"compared": True, "moved": 0, "missing": 0,
                   "total": 41, "factor": None},
        answers_owed=["motion"])
    event = phase_log.file_build_summary(
        project, 5, "Reel 05 - slug", payload)
    assert event["phase"] == phase_log.BUILD_SUMMARY
    assert "unfiled" not in event
    back = phase_log.read_events(project)[0]["summary"]
    assert back["outcome"] == "promoted"
    assert back["dropped"]["semantic"] == [
        "cutaway@2 (no_readable_parameters)"]
    assert back["dropped"]["motion"] == ["push on shot 3 (no_model_answer)"]
    assert back["captain_trims"]["applied"][0]["span_index"] == 2
    assert back["keep_exclusions"] == [
        {"id": "lc-0016", "start": 1.0, "end": 2.5}]
    assert back["draw_gain"] == {"gain": 2.0, "source": "measured",
                                 "disagrees_with_fallback": True}
    assert back["verify"]["finding_classes"] == ["F7"]
    assert back["drift_end"]["total"] == 41
    assert back["markers"]["carried"] == 2
    assert back["version_control"]["files"] == 2
    assert back["cards"][0]["duration_frames"] == 48
    assert back["has_freeze_tail"] is True


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
    assert payload["answers_owed"] == []


def test_build_summary_unknown_outcome_is_refused():
    with pytest.raises(ValueError):
        phase_log.assemble_summary(outcome="vibes")


def test_scalar_summary_is_refused_not_filed(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(TypeError):
        phase_log.log_event(project, 5, "Reel 05",
                            phase_log.BUILD_SUMMARY, summary="placed")
    assert phase_log.read_events(project) == []


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


def test_summarize_ignores_a_malformed_summary():
    events = [
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.BUILD_SUMMARY, "at": None,
         "detail": "", "summary": "placed, trust me"},
        {"format": phase_log.FORMAT, "reel_number": 5, "reel": "Reel 05",
         "phase": phase_log.BUILD_FINISHED, "at": None, "detail": ""},
    ]
    slot = phase_log.summarize(events)["Reel 05"]
    assert slot["build_summary"] is None


def test_summarize_creates_a_slot_for_a_summary_only_reel():
    event = {"format": phase_log.FORMAT, "reel_number": 7,
             "reel": "Reel 07 - slug", "phase": phase_log.BUILD_SUMMARY,
             "at": "2026-09-18T12:00:00+00:00", "detail": "",
             "summary": {"outcome": "left_alone"}}
    slot = phase_log.summarize([event])["Reel 07 - slug"]
    assert slot["build_summary"] == {"outcome": "left_alone"}
    assert slot["seconds_build"] is None


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


def test_rebuild_files_summaries_on_every_path():
    """Wiring pin for the summary: the skip, leave-alone, refusal and
    promotion paths each file one, the end-of-build filing reads the
    gate's report, and the drift end-bracket is captured, not printed
    and dropped."""
    import inspect

    from library.tools import reel_build

    body = inspect.getsource(reel_build.rebuild_reels_in_project)
    for outcome in ("skipped_by_exclusion", "left_alone",
                    "verify_refused", "promoted", "placed_unverified"):
        assert f'outcome="{outcome}"' in body, (
            f"rebuild never files a {outcome} summary")
    assert "conformance_rows" in body
    assert "_drift_end_report" in body
    assert "_verify_payload" in body
    helper = inspect.getsource(reel_build._file_reel_summary)
    assert "assemble_summary" in helper
    assert "file_build_summary" in helper


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
