"""One call reads all three visual asks; one call writes all three answers.

`reel.ask` writes every approved reel's three visual asks, and Batch 5
measured the answering lane spending ~10 tool calls per reel reading
the three asks and writing the three answers one at a time. The batch
helpers beside `write_visual_asks` (`library/tools/reel_build.py`)
close that gap: `read_visual_asks` reads all three asks in one call,
`write_visual_answers` submits all three answers in one.

What this file proves, and how
------------------------------
- Reading back returns exactly what was written: a full write through
  `write_visual_answers` round-trips byte-identical through
  `read_visual_answers`, and the three payloads exercise both answer
  shapes pass 2 accepts (a dict under its plan key, and a bare list).
- A partial write is never silently accepted as three answers: a
  missing key, an extra key, or a None payload RAISES - and raises
  before anything is written, so a refused write leaves no answer
  file behind.
- The plumbing lands where pass 2 already reads: what the batch
  writer wrote is accepted by the three per-ask readers
  (`reel_semantic_visual.read_answer`, `read_span_answer`,
  `reel_look.read_motion_answer`), and an unwritten ask still reads
  as None on both sides.

What this file does NOT claim
-----------------------------
The reel is SYNTHETIC (the Reel 09-shaped stand-ins from
`test_reel_ask_matches_pass1.py`), and the answers are fixed
plumbing payloads, not creative work. Nothing here judges what an
answer SHOULD be - the batch writer carries no default, no template
and no reading of the payload, and a test asserting otherwise would
belong in a different file. No Resolve, no renders, no pipeline run.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import reel_build as build
from library.tools import reel_look as look
from library.tools import reel_semantic_visual as sem_vis

FPS = 24000 / 1001
NAME = "Reel 09 - plays with his mind (rebuild staging)"


def _moment():
    return SimpleNamespace(
        number=9,
        timeline_name="Reel 09 - plays with his mind",
        timeline_start=10.0, timeline_end=18.0)


def _transcript():
    return {"segments": [
        {"timeline_start": 10.0, "timeline_end": 14.0,
         "resolve_item_id": "item1", "source_file": "clip_a.mov",
         "source_start": 100.0, "source_end": 104.0,
         "words": [
             {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
             {"word": "plays", "start": 11.0, "end": 11.4, "timed": True},
             {"word": "mind", "start": 12.5, "end": 13.0, "timed": True}]},
        {"timeline_start": 14.0, "timeline_end": 18.0,
         "resolve_item_id": "item2", "source_file": "clip_a.mov",
         "source_start": 104.0, "source_end": 108.0,
         "words": [
             {"word": "again", "start": 14.2, "end": 14.6,
              "timed": True}]}]}


def _clips():
    return [SimpleNamespace(
        timeline_start=0.0, timeline_end=30.0, source_in=90.0,
        track_index=1, track_type="video", speaker="ALEX",
        source_file="clip_a.mov")]


def _project(tmp_path):
    root = tmp_path / "proj"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return str(root)


def _answers():
    return {
        "reel_semantic": {"motion_graphics_plan": [
            {"element": "caption_callout", "subject": "a mind",
             "anchor_phrase": "mind", "hold_seconds": 2.0,
             "anchor": "centre", "row": 0,
             "copy": {"display": "a mind"}, "why": "the noun"}]},
        "reel_span": [{"segment": 1, "shows": "a mind",
                       "anchor_phrase": "mind", "lead_seconds": 0.2,
                       "why": "the noun"}],
        "reel_motion": {look.MOTION_PLAN_KEY: [
            {"target_block_position": 1, "effect_type": "push_in",
             "magnitude": 0.5, "why": "the line leans in"}]},
    }


def _response_files(project):
    return {stem: Path(project) / "pipeline_output" / "llm_responses" / stem
            for stem in ("reel_semantic_09.json", "reel_span_09.json",
                         "reel_motion_09.json")}


# ── One call reads all three asks ───────────────────────────────────

def test_read_visual_asks_returns_all_three_in_one_call(tmp_path):
    project = _project(tmp_path)
    build.write_visual_asks(
        _moment(), _transcript(), [(10.0, 18.0)], _clips(), project,
        FPS, NAME, [], {"origin": "test"})

    asks = build.read_visual_asks(project, 9)

    assert set(asks) == {"reel_semantic", "reel_span", "reel_motion"}
    for stem, key in (("reel_semantic_09.json", "reel_semantic"),
                      ("reel_span_09.json", "reel_span"),
                      ("reel_motion_09.json", "reel_motion")):
        on_disk = json.loads(
            (Path(project) / "pipeline_output" / "llm_requests"
             / stem).read_text(encoding="utf-8"))
        assert asks[key] == on_disk


def test_unwritten_ask_reads_as_none(tmp_path):
    """No declared look, so no motion ask - and both sides say so."""
    project = _project(tmp_path)
    paths = build.write_visual_asks(
        _moment(), _transcript(), [(10.0, 18.0)], _clips(), project,
        FPS, NAME, [], None)
    assert paths["reel_motion"] == ""

    asks = build.read_visual_asks(project, 9)

    assert asks["reel_motion"] is None
    assert asks["reel_semantic"] is not None
    assert asks["reel_span"] is not None


# ── A partial write is not three answers ────────────────────────────

def test_partial_write_is_refused_before_anything_lands(tmp_path):
    project = _project(tmp_path)
    full = _answers()

    with pytest.raises(ValueError):
        build.write_visual_answers(
            project, 9,
            {"reel_semantic": full["reel_semantic"],
             "reel_span": full["reel_span"]})
    with pytest.raises(ValueError):
        build.write_visual_answers(
            project, 9, {**full, "reel_extra": []})
    with pytest.raises(ValueError):
        build.write_visual_answers(
            project, 9, {**full, "reel_span": None})

    for path in _response_files(project).values():
        assert not path.exists(), (
            f"{path.name} landed from a refused write - a partial "
            f"write must leave nothing behind")


# ── Reading back returns exactly what was written ───────────────────

def test_write_then_read_round_trips_exactly(tmp_path):
    project = _project(tmp_path)
    written = _answers()

    paths = build.write_visual_answers(project, 9, written)

    assert set(paths) == {"reel_semantic", "reel_span", "reel_motion"}
    assert paths["reel_semantic"].endswith("reel_semantic_09.json")
    assert paths["reel_span"].endswith("reel_span_09.json")
    assert paths["reel_motion"].endswith("reel_motion_09.json")
    assert build.read_visual_answers(project, 9) == written


def test_batch_answers_land_where_pass_2_reads(tmp_path):
    """The batch writer is plumbing: pass 2's own readers accept what
    it wrote, unwrapping both answer shapes, and silence stays
    silence - no answer file still reads as unanswered."""
    project = _project(tmp_path)
    written = _answers()
    build.write_visual_answers(project, 9, written)

    assert (sem_vis.read_answer(project, 9)
            == written["reel_semantic"]["motion_graphics_plan"])
    assert sem_vis.read_span_answer(project, 9) == written["reel_span"]
    assert (look.read_motion_answer(project, 9)
            == written["reel_motion"][look.MOTION_PLAN_KEY])

    assert build.read_visual_answers(project, 10) == {
        "reel_semantic": None, "reel_span": None, "reel_motion": None}
    assert sem_vis.read_answer(project, 10) is None
    assert sem_vis.read_span_answer(project, 10) is None
    assert look.read_motion_answer(project, 10) is None
