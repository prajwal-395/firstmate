"""A sub-frame abutment is not a hole in the timeline.

A hole must be a frame wide in BOTH clocks: a sub-frame abutment passes, a
real hole (even one frame) still fails.
History: `docs/evidence/coverage_gap_rounding.md`.
"""
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "compile_manifest_step",
    REPO_ROOT / "library" / "steps" / "step_5_04_compile_manifest" / "step.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_video_coverage_gaps = _mod._video_coverage_gaps


def manifest(clips, duration, fps=30.0):
    return {
        "project": {"frame_rate": fps, "duration_seconds": duration},
        "tracks": {"V1": {"clips": [
            {"timeline_in": a, "timeline_out": b} for a, b in clips
        ]}},
    }


def test_the_001_rounding_artefact_is_not_a_gap():
    """43.646 -> 43.650 rounds to frames 1309 and 1310."""
    gaps = _video_coverage_gaps(
        manifest([(0.0, 43.646), (43.650, 54.768)], duration=54.768))
    assert gaps == [], f"sub-frame abutment reported as a hole: {gaps}"


def test_a_real_hole_still_fails():
    """The 6.4s of black this assertion was written for."""
    gaps = _video_coverage_gaps(
        manifest([(0.0, 4.3), (10.7, 20.0)], duration=20.0))
    assert len(gaps) == 1
    start, end = gaps[0]
    assert abs((end - start) - 6.4) < 1e-6

    # The tolerance is one frame, and one frame of real black counts.
    one_frame = 1.0 / 30.0
    gaps = _video_coverage_gaps(
        manifest([(0.0, 10.0), (10.0 + one_frame, 20.0)], duration=20.0))
    assert len(gaps) == 1, "a genuine one-frame hole must still be caught"

    # The outro case: 1.5s of nothing after the last clip.
    gaps = _video_coverage_gaps(
        manifest([(0.0, 54.768)], duration=56.270))
    assert len(gaps) == 1
    start, end = gaps[0]
    assert abs(start - 54.768) < 1e-6
    assert abs(end - 56.270) < 1e-6


# ── The same rounding class, in the manifest validator ──────────────
#
# `in=8.38 < prev_out=8.382000000000001` failed the build on project 001.
# Two milliseconds is a sixteenth of a frame; the renderer cannot show an
# overlap that small. A real collision is frames or seconds wide, and
# must still fail.

from library.tools.manifest_validator import _validate_structure  # noqa: E402


def _manifest_with(clips, fps=30.0):
    return {
        "project": {"frame_rate": fps, "duration_seconds": 60.0},
        "tracks": {"V1": {"clips": [
            {"timeline_in": a, "timeline_out": b, "label": f"c{i}"}
            for i, (a, b) in enumerate(clips)
        ]}},
    }


def _overlap_errors(clips, fps=30.0):
    return [e for e in _validate_structure(_manifest_with(clips, fps))
            if "overlaps the previous clip" in e]


def test_a_multi_frame_overlap_still_fails():
    """Three frames at 30fps - small, but visible and wrong."""
    assert len(_overlap_errors([(0.0, 10.1), (10.0, 18.0)])) == 1


# --------------------------------------------------------------------------
# From test_clip_edge_dust_absorption.py
#
# A word-timed cut edge one frame off a master-clip edge must not strand a nub.
#
# Measured 2026-09-20 on Reel 08: LC4932/LCATL0013 start 614.697, the
# mechanical take cut starts 614.720. `placements` rounds both to frames
# and keeps the clip's 1-frame head - a picture+audio item the F7 floor
# refuses. The range-level remnant rule cannot see it (the RANGE is 26s),
# so `absorb_wordless_clip_edge_dust` shrinks the range to the clip
# boundary where the dust between them is wordless: same policy as the
# range rule (silence moves, speech refuses), at clip granularity.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from types import SimpleNamespace

from library.tools.reel_build import (
    absorb_wordless_clip_edge_dust,
    placements,
)

FPS = 24000 / 1001
FLOOR_FRAMES = 12


def _clip(start, end):
    return SimpleNamespace(timeline_start=start, timeline_end=end,
                           source_in=100.0, track_index=1, speaker="host")


def _transcript(words):
    """Timed words (start, end, text) as one transcript segment EACH.

    One row per word: neighbouring tellings never share a row, the
    way a cut's two sides never do.
    """
    return {"segments": [{
        "speaker": "host", "text": w,
        "timeline_start": s, "timeline_end": e,
        "words": [{"word": w, "start": s, "end": e, "timed": True}],
    } for s, e, w in words]}


def _subfloor(ranges, clips):
    return [p for p in placements(ranges, clips, FPS)
            if 0 < round((p["source_out"] - p["source_in"]) * FPS)
            < FLOOR_FRAMES]


def test_wordless_dust_snaps_to_the_clip_edge_both_ways():
    """The Reel 08 shape: clip starts 1 frame before the cut edge."""
    clips = [_clip(600.0, 614.697), _clip(614.697, 620.0)]
    transcript = _transcript([(612.0, 613.0, "kept"),
                              (615.0, 616.0, "struck")])
    out = absorb_wordless_clip_edge_dust([(600.0, 614.72)], clips,
                                         transcript, FPS)
    assert out == [(600.0, 614.697)]
    assert _subfloor(out, clips) == []

    # Mirror: clip ends just past the range start.
    clips = [_clip(600.0, 616.53), _clip(616.53, 622.0)]
    transcript = _transcript([(615.0, 616.0, "struck"),
                              (617.0, 618.0, "kept")])
    out = absorb_wordless_clip_edge_dust([(616.48, 622.0)], clips,
                                         transcript, FPS)
    assert out == [(616.53, 622.0)]
    assert _subfloor(out, clips) == []


def test_dust_carrying_speech_timed_or_not_is_left_for_the_gate():
    """Shrinking over a timed word would delete speech: refuse to snap."""
    clips = [_clip(600.0, 614.697), _clip(614.697, 620.0)]
    transcript = _transcript([(612.0, 613.0, "kept"),
                              (614.70, 614.90, "overlap")])
    out = absorb_wordless_clip_edge_dust([(600.0, 614.72)], clips,
                                         transcript, FPS)
    assert out == [(600.0, 614.72)]
    assert len(_subfloor(out, clips)) == 1

    # An untimed Mm-hmm is audible speech the timed scan cannot see.
    transcript = {"segments": [{
        "speaker": "host", "text": "Mm-hmm.",
        "timeline_start": 613.64, "timeline_end": 614.71,
        "words": [],
    }]}
    out = absorb_wordless_clip_edge_dust([(600.0, 614.72)], clips,
                                         transcript, FPS)
    assert out == [(600.0, 614.72)]
