"""`reel.ask` writes what a pass-1 build writes, without the build.

The captain's standing complaint (2026-09-18): every reel is built
TWICE - pass 1 runs headless and its only surviving output is three
`llm_requests/*.json` asks, pass 2 runs with the answers. `reel.ask`
is the way to write those three asks without running a build to
produce them.

What this file proves, and how
------------------------------
`write_visual_asks` (`library/tools/reel_build.py`) is the ONE
spelling both callers use: the pass-1 loop calls it, and `reel.ask`
calls it. This file pins that the extraction changed nothing by
replaying the loop's PRE-EXTRACTION inline sequence - the exact lines
the loop ran before - beside `write_visual_asks` on identical inputs
and comparing the files:

- `reel_motion_NN.json` carries no timestamp, so it must match RAW,
  byte for byte.
- `reel_semantic_NN.json` and `reel_span_NN.json` carry a cosmetic
  `timestamp` re-stamp (`test_rebuild_determinism.py` names it), so
  they must match with ONLY that field normalised - and the test
  asserts the stamps DID differ, without which the normalisation
  would prove nothing.

The instrument is the file bytes under `pipeline_output/llm_requests/`,
the same distinction the finding demanded: measured from what the
writers wrote, never from spans between files on disk.

What this file does NOT claim
-----------------------------
The reel below is SYNTHETIC - a Reel 09-shaped moment, transcript
and master clip stand-ins, derived through the same functions a real
reel goes through - although a live captain's project exists on this
machine. A disposable lane must not verify against it: that would mean
running a throwaway pass-1 build holding the exclusive Resolve lease
on the captain's own open project, which is exactly the cost this
operation exists to remove. Production equivalence is structural (one
code path), and this file pins that the shared path performs the
pass-1 sequence exactly. A subtly different ask gets a subtly
different creative answer, so any future deviation here is the
finding, not a detail.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from library.tools import operations
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


def _read(path):
    return Path(path).read_bytes()


def _pass1_sequence(moment, transcript, ranges, master_clips,
                    project_folder, name, cards, look_decl):
    """The loop's pre-extraction inline ask sequence, verbatim.

    A snapshot of what `rebuild_reels_in_project` ran before the
    shared `write_visual_asks` existed: semantic write, span write,
    then - under a declared look - the motion spine over the trimmed
    ranges plus its write. If the shared path ever deviates from this,
    the byte comparison below is the finding.
    """
    sem_vis.write_request(
        moment, transcript, ranges, project_folder, fps=FPS)
    sem_vis.write_span_request(
        moment, transcript, ranges, project_folder, fps=FPS)
    if look_decl is not None:
        spine = look.motion_spine(
            build.placements(ranges, master_clips, FPS,
                             lead_frames=build.lead_frames(cards, FPS)),
            FPS)
        look.write_motion_request(
            moment.number, name, spine,
            transcript.get("segments") or [], project_folder)


# ── The asks match, byte for byte past the cosmetic stamp ─────────────

def test_write_visual_asks_matches_pass1_sequence(tmp_path):
    project = _project(tmp_path)
    moment, transcript, ranges = _moment(), _transcript(), [(10.0, 18.0)]
    clips, cards = _clips(), []
    look_decl = {"origin": "test"}

    _pass1_sequence(moment, transcript, ranges, clips,
                    project, NAME, cards, look_decl)
    first = {stem: _read(
        Path(project) / "pipeline_output" / "llm_requests" / stem)
        for stem in ("reel_semantic_09.json", "reel_span_09.json",
                     "reel_motion_09.json")}
    time.sleep(0.05)
    paths = build.write_visual_asks(
        moment, transcript, ranges, clips, project, FPS, NAME,
        cards, look_decl)
    second = {stem: _read(
        Path(project) / "pipeline_output" / "llm_requests" / stem)
        for stem in ("reel_semantic_09.json", "reel_span_09.json",
                     "reel_motion_09.json")}

    assert paths["reel_semantic"].endswith("reel_semantic_09.json")
    assert paths["reel_span"].endswith("reel_span_09.json")
    assert paths["reel_motion"].endswith("reel_motion_09.json")

    # The motion ask carries no timestamp: raw bytes, no normalisation.
    assert first["reel_motion_09.json"] == second["reel_motion_09.json"], (
        "the motion ask moved a byte outside any timestamp - a subtly "
        "different ask gets a subtly different creative answer")

    # The semantic and span asks carry the cosmetic re-stamp, and only it.
    stamps = []
    for stem in ("reel_semantic_09.json", "reel_span_09.json"):
        before = json.loads(first[stem].decode("utf-8"))
        after = json.loads(second[stem].decode("utf-8"))
        stamps.append((before.pop("timestamp"), after.pop("timestamp")))
        assert before == after, (
            f"{stem} moved more than its timestamp between the pass-1 "
            f"sequence and the shared path")
    assert all(a != b for a, b in stamps), (
        "expected the cosmetic timestamp re-stamp; without it this test "
        "proves nothing about what differs")


def test_no_look_means_no_motion_ask_either_way(tmp_path):
    """The loop's `if reel_look_decl is not None` gate, both spellings."""
    project = _project(tmp_path)
    moment, transcript, ranges = _moment(), _transcript(), [(10.0, 18.0)]

    _pass1_sequence(moment, transcript, ranges, _clips(),
                    project, NAME, [], None)
    paths = build.write_visual_asks(
        moment, transcript, ranges, _clips(), project, FPS, NAME,
        [], None)
    assert paths["reel_motion"] == ""
    assert not (Path(project) / "pipeline_output" / "llm_requests"
                / "reel_motion_09.json").exists()
    assert paths["reel_semantic"].endswith("reel_semantic_09.json")
    assert paths["reel_span"].endswith("reel_span_09.json")


# ── The derivation the asks are written from ──────────────────────────

def test_derive_ranges_and_cards_matches_reel_ranges(tmp_path):
    """No exclusions, no trims, no ending, no cards: the layers above
    `reel_ranges` must be no-ops on this reel, and the derivation must
    say which ending it found (none)."""
    project = _project(tmp_path)
    moment, transcript = _moment(), _transcript()
    cuts, insisted = build.moment_cuts_and_insistences(
        moment, transcript, [], [])
    assert cuts == [] and insisted == []
    ranges, cards, ending = build.derive_reel_ranges_and_cards(
        moment, transcript, _clips(), project, FPS, NAME,
        cuts, insisted, card_declarations=[], look_decl=None,
        reel_width=1080, reel_height=1920)
    assert ranges == build.reel_ranges(moment, transcript)
    assert cards == []
    assert ending is None


def test_span_split_preserves_combined_behavior(tmp_path):
    """`span_record_for_build` is write-then-resolve; resolving against
    an already-written ask must record exactly what the combined form
    records, and the unasked branch must stay SPAN_NOT_PLANNED."""
    project = _project(tmp_path)
    moment, transcript, ranges = _moment(), _transcript(), [(10.0, 18.0)]

    combined = sem_vis.span_record_for_build(
        moment, transcript, ranges, project, fps=FPS,
        timeline_name=NAME)
    asked_path = sem_vis.write_span_request(
        moment, transcript, ranges, project, fps=FPS)
    split = sem_vis.resolve_span_record(
        moment, transcript, ranges, project, fps=FPS,
        timeline_name=NAME, asked=bool(asked_path))
    assert split == combined

    unasked = sem_vis.resolve_span_record(
        moment, {"segments": []}, ranges, project, fps=FPS,
        timeline_name=NAME, asked=False)
    assert unasked["basis"] == sem_vis.SPAN_NOT_PLANNED


# ── The operation a lane calls ────────────────────────────────────────

def test_reel_ask_is_registered_with_a_help_line():
    op = operations.get("reel.ask")
    assert op.owning_node == "build_reels"
    assert op.owning_dir == "step_7_01_build_reels"
    assert op.attr == "ask_reels"
    assert "without building" in op.summary
    assert "reel.ask" in operations.describe()
    assert op in operations.by_node("build_reels")


def test_ask_reels_refuses_without_its_inputs():
    from library.steps.step_7_01_build_reels.step import (
        ReelBuildRefused,
        ask_reels,
    )
    import pytest

    with pytest.raises(ReelBuildRefused):
        ask_reels({})
    with pytest.raises(ReelBuildRefused):
        ask_reels({"project_folder": "/nonexistent"})
