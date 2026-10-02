"""The span plan is recorded where the pipeline reads it, and refused when empty.

PR 774's resolver distinguishes a model that chose stillness
(`span_no_events_planned` - a decision) from a model whose every beat
was refused (`span_every_event_dropped` - the absence of a decision
surviving). This file proves the three halves that connect that
distinction to the build:

1. A RECORD: `span_record_for_build` resolves one reel's answer and
   returns it in the V6 record's own convention - same REVIEW area,
   same `{"format": ..., "plans": [...]}` envelope, same merge-per-reel
   write - filed by `write_span_records` and read back by
   `read_span_records` / `span_record_for_reel`.
2. A GRADE: F23 (`check_span_plan`) refuses an all-refused plan and
   passes a deliberate stillness, and `verify_reel` threads it through.
3. No look values anywhere on the path: a resolved moment carries what
   is SHOWN and its measured window, never a colour, size, font or
   motion value.

The placer is OUT on purpose: moments carry `shows` as free-text
provenance and segments need declared look values, so laying moments
on PR 776's windows needs its own change carrying those decisions.
What is proven here is that an all-refused plan cannot build green
and silently while that placer is still missing.
"""

from __future__ import annotations

import json

from library.tools import reel_semantic_visual as span
from library.tools.reel_conformance_verifier import (
    FindingClass,
    ReelPlan,
    ReelTimeline,
    check_span_plan,
    verify_reel,
)


def _transcript():
    """Two keep ranges' worth of timed words, in MASTER seconds."""
    return {"segments": [{
        "words": [
            {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
            {"word": "plays", "start": 11.0, "end": 11.4, "timed": True},
            {"word": "with", "start": 11.5, "end": 11.8, "timed": True},
            {"word": "his", "start": 12.0, "end": 12.2, "timed": True},
            {"word": "mind", "start": 12.5, "end": 13.0, "timed": True},
            {"word": "has", "start": 20.5, "end": 20.9, "timed": True},
            {"word": "vision", "start": 21.5, "end": 22.0, "timed": True},
        ]}]}


def _ranges():
    return [(10.0, 14.0), (20.0, 26.0)]


class _Moment:
    number = 9
    timeline_name = "Reel 09 - plays with his mind"


def _answer_file(project, beats):
    responses = project / "pipeline_output" / "llm_responses"
    responses.mkdir(parents=True, exist_ok=True)
    (responses / "reel_span_09.json").write_text(
        json.dumps({"span_visual_plan": beats}), encoding="utf-8")


def _resolving_beat():
    return {"segment": 1, "shows": "a mind, illustrated",
            "anchor_phrase": "his mind", "lead_seconds": 0.2,
            "why": "the line is about playing with the mind"}


def _refused_beat():
    # "goalkeeper" is spoken nowhere in `_transcript`: the resolver
    # drops this as `anchor_phrase_not_found`, and with nothing else
    # proposed the plan lands on SPAN_EVERY_EVENT_DROPPED. This is the
    # concrete input that makes the F23 refusal fire.
    return {"segment": 1, "shows": "a goalkeeper",
            "anchor_phrase": "goalkeeper",
            "why": "not in the speech"}


# ── The record: resolve, file, read back ─────────────────────────────

def test_a_resolved_plan_records_its_moments(tmp_path):
    project = tmp_path / "proj"
    _answer_file(project, [_resolving_beat()])
    record = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    assert record["reel"] == "Reel 09 - plays with his mind"
    assert record["basis"] == span.SPAN_EVENTS_PLANNED
    assert record["proposed"] == 1 and record["resolved"] == 1
    assert len(record["moments"]) == 1
    assert record["moments"][0]["event_start"] == 2.0 - 0.2
    assert record["dropped"] == []


def test_no_answer_file_is_not_a_decision_for_no_pictures(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    record = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    assert record["basis"] == span.SPAN_NOT_PLANNED
    assert record["moments"] == []


# ── The file: the V6 convention, a span payload ──────────────────────

def test_records_merge_per_reel_the_v6_way(tmp_path):
    project = tmp_path / "proj"
    _answer_file(project, [_resolving_beat()])
    first = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    path = span.write_span_records(str(project), [first])
    assert path.endswith("span_visual_plans.json")
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle)
    assert stored["format"] == "span_visual_plans/1"

    # A partial build recording a second reel must not delete the first.
    other = dict(first, reel="Reel 10 - vision")
    span.write_span_records(str(project), [other])
    records = span.read_span_records(str(project))
    assert span.span_record_for_reel(records, "Reel 09 - plays with his mind")[
        "basis"] == span.SPAN_EVENTS_PLANNED
    assert span.span_record_for_reel(records, "Reel 10 - vision") is not None

    # Re-recording a reel replaces it whole, including with an emptier basis.
    span.write_span_records(
        str(project), [dict(first, basis=span.SPAN_NO_EVENTS_PLANNED,
                            moments=[])])
    records = span.read_span_records(str(project))
    assert span.span_record_for_reel(records, "Reel 09 - plays with his mind")[
        "basis"] == span.SPAN_NO_EVENTS_PLANNED
    assert span.span_record_for_reel(records, "No such reel") is None
    assert span.span_record_for_reel(None, "Reel 09 - plays with his mind") is None


# ── The grade: every-dropped refuses, stillness passes ───────────────

def _record(basis, proposed=0, reasons=()):
    return {"reel": "Reel 09 - plays with his mind", "basis": basis,
            "entries": [{}] * proposed,
            "dropped": [{"element": "a goalkeeper", "reason": reason,
                         "what_the_reason_means": "...",
                         "detail": "..."} for reason in reasons],
            "moments": [],
            "proposed": proposed, "resolved": 0}


def test_an_all_refused_span_plan_fails_f23():
    findings = check_span_plan(
        "Reel 09 - plays with his mind",
        _record(span.SPAN_EVERY_EVENT_DROPPED, proposed=1,
                reasons=["anchor_phrase_not_found"]))
    assert [f.finding_class for f in findings] == [FindingClass.F23]
    assert findings[0].severity == "error"
    assert "every one was refused" in findings[0].message
    assert "anchor_phrase_not_found" in findings[0].message


# ── End to end: resolve, record, grade ───────────────────────────────

def test_every_dropped_and_no_events_reach_different_outcomes(tmp_path):
    """The defect this change closes: counting moments alone cannot tell
    an all-refused plan from a deliberately still reel. The RECORD can,
    and the grade follows it."""
    project = tmp_path / "proj"

    _answer_file(project, [_refused_beat()])
    refused = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    assert refused["basis"] == span.SPAN_EVERY_EVENT_DROPPED
    assert refused["proposed"] == 1 and refused["resolved"] == 0
    assert refused["moments"] == []
    assert [d["reason"] for d in refused["dropped"]] == [
        "anchor_phrase_not_found"]
    span.write_span_records(str(project), [refused])

    _answer_file(project, [])
    still = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 10 - vision")
    # an empty answer is a DECISION for no pictures
    assert still["basis"] == span.SPAN_NO_EVENTS_PLANNED
    assert still["moments"] == [] and still["dropped"] == []
    span.write_span_records(str(project), [still])

    records = span.read_span_records(str(project))
    refused_findings = check_span_plan(
        "Reel 09 - plays with his mind",
        span.span_record_for_reel(records, "Reel 09 - plays with his mind"))
    still_findings = check_span_plan(
        "Reel 10 - vision",
        span.span_record_for_reel(records, "Reel 10 - vision"))
    assert [f.finding_class for f in refused_findings] == [FindingClass.F23]
    assert still_findings == []


def _plan():
    return ReelPlan(
        reel_name="Reel 09 - plays with his mind", reel_number=9,
        plan_seconds=10.0, plan_frames=300.0,
        span_start=0.0, span_end=10.0, placements=(),
        keep_ranges=((0.0, 10.0),))


def _timeline():
    return ReelTimeline(
        reel_name="Reel 09 - plays with his mind", fps=30.0,
        total_frames=300, video_items=(), audio_items=(),
        caption_items=())


def test_verify_reel_refuses_an_all_refused_span_plan():
    result = verify_reel(
        _plan(), _timeline(),
        span_plan=_record(span.SPAN_EVERY_EVENT_DROPPED, proposed=1,
                         reasons=["anchor_phrase_not_found"]))
    assert FindingClass.F23 in [f.finding_class for f in result.errors]


