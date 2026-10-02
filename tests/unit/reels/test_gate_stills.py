"""The gate-stills loop, pinned against fake Resolve objects.

A reel lane's visual gate was always the same hand-written loop - make
the reel's timeline current, move the playhead to each named frame,
gallery-grab it, put the entry timeline and playhead back (batch 5's
`/tmp/fm-batch5/gate_stills.py`, three edits then one run per reel).
These tests drive the real `gate_stills` against fakes (the module
duck-types the application, so no Resolve is needed), pinning what that
script proved in production: exact-name lookup, per-frame positioning
with read-back, one grab per frame, out-of-range refusal, and entry
restore.
"""
from __future__ import annotations
import contextlib
import struct
import zlib
import pytest
from library.tools import gate_stills
from library.tools.gate_stills import (
    find_timeline,
    grab_reel_stills,
    still_filename,
)
from tests.resolve_double import FakeProject, FakeTimeline
import sys
import library.steps.step_7_02_verify_reels.step as v702
from library.tools import operations
from library.tools import resolve_lock
import json
from pathlib import Path
from types import SimpleNamespace
from library.tools import reel_build as build
from library.tools import reel_look as look
from library.tools import reel_semantic_visual as sem_vis
import time


def _minimal_png(width: int, height: int) -> bytes:
    """A valid PNG with the given dimensions, stdlib only."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"\x00" * (width * 3 + 1) * height
    chunks = b""
    for kind, data in ((b"IHDR", ihdr),
                       (b"IDAT", zlib.compress(raw)),
                       (b"IEND", b"")):
        chunks += (struct.pack(">I", len(data)) + kind + data
                   + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return b"\x89PNG\r\n\x1a\n" + chunks


def _reel(name, **knobs):
    """A timeline starting at absolute frame 100, running at 24 fps."""
    return FakeTimeline(name, start_frame=100, end_frame=600, frame_rate=24,
                        timecode="00:00:04:04", **knobs)


@pytest.fixture
def no_lease(monkeypatch):
    """The lease as a no-op: these tests pin the grab loop, not the lock."""
    monkeypatch.setattr(gate_stills, "resolve_lease",
                        lambda *args, **kwargs: contextlib.nullcontext())


@pytest.fixture
def grab_png(monkeypatch):
    """`grab_still` as a PNG writer, recording every destination."""
    calls = []

    def fake(timeline, project, destination):
        from pathlib import Path
        calls.append(Path(destination).name)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(_minimal_png(1920, 1080))
        return None

    monkeypatch.setattr(gate_stills, "grab_still", fake)
    return calls


def _project():
    reel = _reel("Reel 21 - my-website")
    other = _reel("Reel 14 - more-content")
    entry = _reel("Pipeline_Edit")
    return FakeProject([entry, reel, other], current=entry), entry, reel


def test_produces_stills_for_named_frames_of_named_reel(
        no_lease, grab_png, tmp_path):
    project, entry, _reel = _project()
    report = grab_reel_stills(
        project, "Reel 21 - my-website", [20, 500 - 1], tmp_path, "reel21")
    assert report["ok"] is True
    assert [s["reel_frame"] for s in report["stills"]] == [20, 499]
    # One discard warm-up after the timeline switch, then one grab per
    # frame - the warm-up is what the first-grab measurement requires.
    assert grab_png == [".warmup_reel21.png",
                        "reel21_f000020.png", "reel21_f000499.png"]
    for still in report["stills"]:
        assert still["timecode_set"] == still["timecode_read"]
        assert still["width"] == 1920 and still["height"] == 1080
        assert still["bytes"] > 0
        assert still["path"].endswith(still_filename("reel21",
                                                     still["reel_frame"]))
    # The entry timeline and its playhead are back.
    assert project.GetCurrentTimeline() is entry
    assert entry.GetCurrentTimecode() == "00:00:04:04"
    assert report["restored"] == {"timeline": "Pipeline_Edit", "ok": True}
    # The warm-up grab is discarded, never banked.
    assert not list(tmp_path.glob(".warmup_*"))


def test_timeline_name_is_exact_never_prefix(no_lease, grab_png, tmp_path):
    short = _reel("Reel 1")
    long = _reel("Reel 14")
    entry = _reel("Pipeline_Edit")
    project = FakeProject([entry, short, long], current=entry)
    report = grab_reel_stills(project, "Reel 1", [0], tmp_path, "reel1")
    assert report["ok"] is True
    assert project.set_calls[0] == "Reel 1"  # made current, never Reel 14
    assert project.GetCurrentTimeline() is entry  # and put back
    assert find_timeline(project, "Reel") is None
    missing = grab_reel_stills(project, "Reel", [0], tmp_path, "reelX")
    assert missing["ok"] is False
    assert "exactly named 'Reel'" in missing["error"]
    assert missing["stills"] == []


def test_an_out_of_range_frame_is_refused_by_name(
        no_lease, grab_png, tmp_path):
    project, _entry, _reel = _project()
    report = grab_reel_stills(
        project, "Reel 21 - my-website", [10, 500, -1], tmp_path, "reel21")
    assert report["ok"] is False
    assert [s["reel_frame"] for s in report["stills"]] == [10]
    assert {f["reel_frame"] for f in report["failed"]} == {500, -1}
    assert all("outside" in f["reason"] for f in report["failed"])
    assert "black still" in report["failed"][0]["reason"]
    assert "500" in report["error"] or "-1" in report["error"]


def test_a_playhead_that_will_not_land_fails_the_still(
        no_lease, grab_png, tmp_path):
    stuck = _reel("Reel 09 - stuck", stuck_playhead=True)
    project = FakeProject([stuck], current=stuck)
    report = grab_reel_stills(project, "Reel 09 - stuck", [50],
                              tmp_path, "reel9")
    assert report["ok"] is False
    assert report["stills"] == []
    assert report["failed"][0]["reel_frame"] == 50
    assert "would not land" in report["failed"][0]["reason"]
    # Nothing grabbed: a still of the wrong frame is never banked.
    assert list(tmp_path.glob("reel9_*.png")) == []


def test_a_rate_less_timeline_refuses_before_anything_moves(
        no_lease, grab_png, tmp_path):
    reel = FakeTimeline("Reel 21 - my-website", start_frame=100,
                        end_frame=600, settings={"timelineFrameRate": ""})
    project = FakeProject([reel], current=reel)
    report = grab_reel_stills(project, "Reel 21 - my-website", [10],
                              tmp_path, "reel21")
    assert report["ok"] is False
    assert "no frame rate" in report["error"]
    assert grab_png == []


# --------------------------------------------------------------------------
# From test_grab_gate_stills.py
#
# The gate-stills entry point refuses what it cannot do.
#
# `grab_gate_stills` (reached as `reel.gate_stills`) grabs named frames
# off one built reel timeline into the project's own gate-stills
# directory. These tests pin its boundaries without grabbing anything
# and without connecting to Resolve: malformed input raises rather than
# being guessed at, and an unavailable Resolve returns a report saying
# so - never an empty success. The refusal test injects a fake scripting
# module so it cannot hang on a live Resolve handshake.

def test_no_frames_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one frame"):
        v702.grab_gate_stills(str(tmp_path), "reel21", "Reel 21", [])


def test_without_resolve_the_refusal_returns_not_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "resolve"))
    monkeypatch.setattr(resolve_lock, "_depth", 0)
    monkeypatch.setattr(resolve_lock, "_mode", None)
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

    calls = []

    class ResolveUnavailable:
        @staticmethod
        def scriptapp(name):
            calls.append(name)
            return None

    # Keep the lease and the real connection wrapper in the path, but
    # make Resolve unavailable at the scripting-module boundary. This
    # avoids a live handshake, which can block indefinitely.
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        ResolveUnavailable)

    report = v702.grab_gate_stills(str(tmp_path), "reel21", "Reel 21", [20])
    assert report["ok"] is False
    assert report["stills"] == []
    assert report["error"]
    assert len(report["failed"]) == 1
    assert report["failed"][0]["reel_frame"] == 20
    # The bank directory exists even so: the refusal is about Resolve,
    # never about where the stills would have gone.
    assert (tmp_path / "pipeline_output" / "steps"
            / "7_02_verify_reels" / "gate_stills").is_dir()
    assert calls == ["Resolve"]


# --------------------------------------------------------------------------
# From test_reel_visual_batch.py
#
# One call reads all three visual asks; one call writes all three answers.
#
# `reel.ask` writes every approved reel's three visual asks, and Batch 5
# measured the answering lane spending ~10 tool calls per reel reading
# the three asks and writing the three answers one at a time. The batch
# helpers beside `write_visual_asks` (`library/tools/reel_build.py`)
# close that gap: `read_visual_asks` reads all three asks in one call,
# `write_visual_answers` submits all three answers in one.
#
# What this file proves, and how
# ------------------------------
# - Semantic and span payloads round-trip unchanged. A motion response
#   records the ask fingerprint beside the original answer; the batch
#   reader strips that private metadata, and the per-ask reader accepts it.
#   The three payloads exercise the answer shapes pass 2 accepts.
# - A partial write is never silently accepted as three answers: a
#   missing key, an extra key, or a None payload RAISES - and raises
#   before anything is written, so a refused write leaves no answer
#   file behind.
# - The plumbing lands where pass 2 already reads: what the batch
#   writer wrote is accepted by the three per-ask readers
#   (`reel_semantic_visual.read_answer`, `read_span_answer`,
#   `reel_look.read_motion_answer`), and an unwritten ask still reads
#   as None on both sides.
#
# What this file does NOT claim
# -----------------------------
# The reel is SYNTHETIC (the Reel 09-shaped stand-ins from
# `test_reel_ask_matches_pass1.py`), and the answers are fixed
# plumbing payloads, not creative work. Nothing here judges what an
# answer SHOULD be - the batch writer carries no default, no template
# and no reading of the payload, and a test asserting otherwise would
# belong in a different file. No Resolve, no renders, no pipeline run.

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


def _project_2(tmp_path):
    root = tmp_path / "proj"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return str(root)


def _write_motion_ask(project, number=9):
    spine = {"structure": [{
        "position": 0,
        "timeline_start": 0.0,
        "timeline_end": 8.0,
        "source_start": 0.0,
        "source_end": 8.0,
        "master_start": 0.0,
        "master_end": 8.0,
        "word_timestamps": [
            {"word": "line", "source_start": 1.0, "source_end": 1.2}]}]}
    look.write_motion_request(
        number, f"Reel {number:02d}", spine,
        [{"timeline_start": 0.0, "timeline_end": 8.0,
          "text": "The line lands."}],
        project,
        call_to_action={"timeline_start": 20.0, "timeline_end": 21.0})


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
            {"target_block_position": 0, "effect_type": "ken_burns",
             "anchor": {"word": "line"},
             "anchor_end": {"word": "line", "edge": "end"},
             "params": {"zoom_start": 1.0, "zoom_end": 1.02},
             "rationale": "the line leans in"}]},
    }


def _response_files(project):
    return {stem: Path(project) / "pipeline_output" / "llm_responses" / stem
            for stem in ("reel_semantic_09.json", "reel_span_09.json",
                         "reel_motion_09.json")}


# ── One call reads all three asks ───────────────────────────────────

def test_unwritten_ask_reads_as_none(tmp_path):
    """No declared look, so no motion ask - and both sides say so."""
    project = _project_2(tmp_path)
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
    project = _project_2(tmp_path)
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


def test_batch_answers_land_where_pass_2_reads(tmp_path):
    """The batch writer is plumbing: pass 2's own readers accept what
    it wrote, unwrapping both answer shapes, and silence stays
    silence - no answer file still reads as unanswered."""
    project = _project_2(tmp_path)
    written = _answers()
    _write_motion_ask(project)
    build.write_visual_answers(project, 9, written)

    response = json.loads(_response_files(project)["reel_motion_09.json"].read_text(
        encoding="utf-8"))
    assert response[look.MOTION_BASIS_KEY] == look.motion_request_basis(
        build.read_visual_asks(project, 9)["reel_motion"])

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


def test_motion_bare_list_shape_survives_private_fingerprint(tmp_path):
    project = _project_2(tmp_path)
    answers = _answers()
    _write_motion_ask(project)
    answers["reel_motion"] = answers["reel_motion"][look.MOTION_PLAN_KEY]

    build.write_visual_answers(project, 9, answers)

    assert build.read_visual_answers(project, 9)["reel_motion"] == \
        answers["reel_motion"]
    assert look.read_motion_answer(project, 9) == answers["reel_motion"]


# --------------------------------------------------------------------------
# From test_reel_ask_matches_pass1.py
#
# `reel.ask` writes what a pass-1 build writes, without the build.
#
# `write_visual_asks` is the one spelling both callers use; this pins it to
# the pre-extraction pass-1 sequence byte for byte (motion raw, semantic and
# span past their cosmetic timestamp).
#
# History: `docs/evidence/reel_ask.md` (test_reel_ask_matches_pass1.py).

def _project_3(tmp_path):
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
            FPS, transcript.get("segments") or [])
        look.write_motion_request(
            moment.number, name, spine,
            transcript.get("segments") or [], project_folder)


# ── The asks match, byte for byte past the cosmetic stamp ─────────────

def test_write_visual_asks_matches_pass1_sequence(tmp_path):
    project = _project_3(tmp_path)
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


# ── The operation a lane calls ────────────────────────────────────────

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
