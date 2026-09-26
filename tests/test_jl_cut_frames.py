"""Rung 7 (K1, TR3.3/MX3.3): a J/L lead stated in frames survives to the frame.

The execution-frontier scout found J/L offsets expressible only in
seconds (`lead_seconds` / `lag_seconds`), so a request like TR3.3's
"bring its audio in 20 frames before the picture cut" had no plan
spelling in the requester's units - E3's rule (numbers when stated)
failed at the vocabulary. `lead_frames` / `lag_frames` carry the
stated count; seconds and frames disagreeing past half a frame refuse,
exactly like a stated offset disagreeing with an anchor.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.jl_cut import resolve_audio_cut

FPS = 30.0


def _blocks():
    # A breathing join: outgoing speech ends at 8.0, a 2 s pause, the
    # boundary at 10.0, incoming speech starts at 10.5.
    outgoing = {
        "position": 1, "timeline_start": 0.0, "timeline_end": 10.0,
        "word_timestamps": [
            {"word": "done", "source_start": 7.0, "source_end": 8.0},
        ],
    }
    incoming = {
        "position": 2, "timeline_start": 10.0, "timeline_end": 20.0,
        "word_timestamps": [
            {"word": "next", "source_start": 10.5, "source_end": 11.0},
        ],
    }
    return outgoing, incoming


def test_lead_frames_places_the_audio_cut_on_the_frame():
    """TR3.3's shape: 20 frames before the picture cut, to the frame."""
    outgoing, incoming = _blocks()
    hit = resolve_audio_cut(
        kind="j_cut", entry={"type": "j_cut", "lead_frames": 20},
        outgoing=outgoing, incoming=incoming,
        boundary_frame=300, frame_rate=FPS)
    assert hit["audio_cut_frame"] == 280
    assert hit["picture_cut_frame"] == 300
    assert hit["lead_seconds"] == pytest.approx(20 / FPS, abs=1e-3)
    assert "20 frames" in hit["method"]


def test_lag_frames_places_the_audio_cut_on_the_frame():
    """MX3.3's shape in frames: 30 frames after the picture cut."""
    outgoing, incoming = _blocks()
    hit = resolve_audio_cut(
        kind="l_cut", entry={"type": "l_cut", "lag_frames": 30},
        outgoing=outgoing, incoming=incoming,
        boundary_frame=300, frame_rate=FPS)
    assert hit["audio_cut_frame"] == 330
    assert hit["lead_seconds"] == pytest.approx(30 / FPS, abs=1e-3)
    assert "30 frames" in hit["method"]


def test_one_second_l_cut_places_audio_thirty_frames_late():
    """MX3.3's stated second survives as a 30-frame timeline offset."""
    outgoing, incoming = _blocks()
    hit = resolve_audio_cut(
        kind="l_cut", entry={"type": "l_cut", "lag_seconds": 1.0},
        outgoing=outgoing, incoming=incoming,
        boundary_frame=300, frame_rate=FPS)
    assert hit["audio_cut_frame"] == 330
    assert hit["picture_cut_frame"] == 300
    assert "1.000s" in hit["method"]


def test_seconds_and_frames_agreeing_ship_the_frames():
    """Both stated and agreeing: the frame-true value wins, said so."""
    outgoing, incoming = _blocks()
    hit = resolve_audio_cut(
        kind="j_cut",
        entry={"type": "j_cut", "lead_seconds": 0.667,
               "lead_frames": 20},
        outgoing=outgoing, incoming=incoming,
        boundary_frame=300, frame_rate=FPS)
    assert hit["audio_cut_frame"] == 280
    assert "agrees" in hit["method"]
