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
