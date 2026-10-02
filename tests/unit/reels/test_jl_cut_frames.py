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

REPO = Path(__file__).resolve().parents[3]
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


def test_a_stated_offset_places_the_audio_cut_on_the_frame():
    """TR3.3's 20 frames before the picture cut, MX3.3's 30 frames
    after it, and MX3.3's stated second as a 30-frame offset - each to
    the frame, the method naming the stated unit."""
    outgoing, incoming = _blocks()
    cases = [
        ("j_cut", {"lead_frames": 20}, 280, 20 / FPS, "20 frames"),
        ("l_cut", {"lag_frames": 30}, 330, 30 / FPS, "30 frames"),
        ("l_cut", {"lag_seconds": 1.0}, 330, 1.0, "1.000s"),
    ]
    for kind, offset, frame, seconds, method in cases:
        hit = resolve_audio_cut(
            kind=kind, entry={"type": kind, **offset},
            outgoing=outgoing, incoming=incoming,
            boundary_frame=300, frame_rate=FPS)
        assert hit["audio_cut_frame"] == frame, offset
        assert hit["picture_cut_frame"] == 300
        assert hit["lead_seconds"] == pytest.approx(seconds, abs=1e-3)
        assert method in hit["method"]


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
