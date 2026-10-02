"""A span can be planned from its speech: request, accept, refuse.

Blocker 1 (`docs/SPAN_RENDERER_CAPABILITY.md` §3.1): no model plans a
span. `reel_semantic_visual` builds a planning request only for the V6
overlay layer. This file tests the span's own ask - a request built from
measured word windows, a schema the answer must satisfy, and a resolver
that binds picture events to real word timings and REFUSES what it
cannot bind - without Resolve and without a model.

The shape matched is the V6 one in the same module: `write_request`
(request file), `read_answer` (answer file), `motion_graphics_plan`
`resolve_plan` (named drops). The span reuses that pattern with its own
reasons, because the overlay machinery does not generalise: it resolves
element keys, anchors, colours and copy against the overlay roster, and
a span beat is none of those - it is a noun illustrated, cued to words,
leading them.

What the reference needs (`docs/ANIMATION_FIRST_REFERENCE.md` §1):
every picture event illustrates a NOUN from the spoken line, and events
lead their nouns. So each event names `shows` (the noun), an
`anchor_phrase` (words from its own segment), and `lead_seconds` (how
far before the anchor the picture lands). The output carries what is
SHOWN and no look values - no colour, no size, no font, no motion
values - and an entry carrying any of those is refused rather than
read past.
"""

from __future__ import annotations

import json

from library.tools import reel_semantic_visual as span


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


def _words():
    """The measured evidence, in REEL seconds: segment 1 is reel 0-4,
    segment 2 is reel 4-10."""
    return span.span_segment_words(_ranges(), _transcript())


class _Moment:
    number = 9
    timeline_name = "Reel 09 - plays with his mind"


# ── The request is built from measured word windows ──────────────────

def test_the_request_carries_each_segments_measured_words(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    path = span.write_span_request(
        _Moment(), _transcript(), _ranges(), str(project), fps=30.0)
    assert path.endswith("reel_span_09.json")
    with open(path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    assert request["step_id"] == "reel_span_visual"
    assert "span_visual_plan" in request["expected_schema"]
    context = request["context"]
    assert "mind" in context and "vision" in context
    words = _words()
    assert [w["word"] for w in words[0]] == [
        "he", "plays", "with", "his", "mind"]
    assert (words[0][4]["start"], words[0][4]["end"]) == (2.5, 3.0)
    assert [w["word"] for w in words[1]] == ["has", "vision"]


# ── The resolver binds what it can ───────────────────────────────────

def test_a_noun_anchored_event_with_a_lead_resolves():
    resolved = span.resolve_span_plan(
        [{"segment": 1, "shows": "a mind, illustrated",
          "anchor_phrase": "his mind", "lead_seconds": 0.2,
          "why": "the line is about playing with the mind"}],
        segment_words=_words(), ranges=_ranges())
    assert len(resolved.moments) == 1
    moment = resolved.moments[0]
    assert moment["anchor_start"] == 2.0
    assert moment["anchor_end"] == 3.0
    assert moment["event_start"] == 2.0 - 0.2
    assert moment["timing_basis"] == "word_window:his mind"
    assert moment["shows"] == "a mind, illustrated"
    assert resolved.basis == span.SPAN_EVENTS_PLANNED


# ── ... and refuses what it cannot ───────────────────────────────────

def test_an_event_the_resolver_cannot_bind_is_refused_by_name():
    table = [
        ({"segment": 1, "shows": "a goalkeeper",
          "anchor_phrase": "goalkeeper", "why": "not in the speech"},
         "anchor_phrase_not_found"),
        ({"segment": 1, "shows": "a whistle on the first word",
          "anchor_phrase": "he", "lead_seconds": 1.0,
          "why": "a lead longer than the anchor's distance to the edge"},
         "beat_outside_segment"),
        ({"segment": 1, "shows": "a mind", "anchor_phrase": "mind",
          "color": "#123456", "font_size": 96, "entrance": "scale",
          "why": "taste smuggled into a picture plan"},
         "look_value_in_picture_plan"),
        ({"segment": 1, "shows": "a mind", "why": "no anchor at all"},
         "no_anchor_declared"),
    ]
    for beat, reason in table:
        resolved = span.resolve_span_plan(
            [beat], segment_words=_words(), ranges=_ranges())
        assert resolved.moments == [], reason
        assert resolved.basis == span.SPAN_EVERY_EVENT_DROPPED, reason
        assert [d.reason for d in resolved.dropped] == [reason]


# ── The answer file reads like the V6 one ────────────────────────────

def test_a_malformed_span_answer_reads_as_unanswered(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "llm_responses").mkdir(parents=True)
    (project / "pipeline_output" / "llm_responses" / "reel_span_09.json").write_text(
        '{"span_visual_plan": "not a list"}', encoding="utf-8")
    assert span.read_span_answer(str(project), 9) is None


