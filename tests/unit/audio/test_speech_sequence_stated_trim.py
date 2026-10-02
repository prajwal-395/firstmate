"""Rung 7 (finding 33, C3.1): a stated trim is honored to the frame.

On the scout's B7 run (C3.1 "trim 4f off the head of shot 2") the
passage's source_start 9.699 -> 9.833 was re-anchored BACK to the word
by 2.02's aligner - by design the bounds are a lookup hint (AGENTS.md
6), and no field said "this bound is a stated trim, keep it". The
requester's number never reached the timeline.

The fix: `trim_head_frames` / `trim_tail_frames` (frames when the
request states frames) and `trim_head_seconds` / `trim_tail_seconds`
(seconds when it states seconds) apply AFTER word alignment, to the
aligned span. The words stay as measured; only the played bounds move.
Both forms on one edge must agree past half a frame, frames with no
timebase refuse, and a trim eating the passage refuses - a deletion,
not a trim.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import library.steps.step_2_02_speech_sequence.post_bridge as pb

FPS = 30.0

# One clip: words "take the shot now" at 9.6-12.6s, one word every 0.6s
# with 0.5s of voice each (the shape of a spoken line, not a fixture).
WORDS = [
    {"word": "take", "start": 9.6, "end": 10.1},
    {"word": "the", "start": 10.2, "end": 10.7},
    {"word": "shot", "start": 10.8, "end": 11.3},
    {"word": "now", "start": 11.4, "end": 11.9},
]


@pytest.fixture
def index_dir(tmp_path):
    d = tmp_path / "temporal_index"
    d.mkdir()
    (d / "clip_001.json").write_text(json.dumps({
        "speech_regions": [{
            "start": 9.6, "end": 11.9,
            "text": "take the shot now",
            "words": WORDS,
        }],
    }), encoding="utf-8")
    return str(d)


def _passage(**over):
    base = {"clip_id": "clip_001", "source_start": 9.6,
            "source_end": 11.9, "text": "take the shot now"}
    base.update(over)
    return {"body_sequence": [base]}


def test_a_stated_trim_moves_only_the_played_bound(index_dir):
    """C3.1's shape: 4 frames off the head lands at 9.733, not the
    word's 9.6; seconds trim the tail; frames and seconds that agree on
    one edge ship the frames. The words stay as measured."""
    rows = [
        (dict(trim_head_frames=4), 9.6 + 4 / FPS, 11.9),
        (dict(trim_tail_seconds=0.5), 9.6, 11.4),
        (dict(trim_head_frames=4, trim_head_seconds=4 / FPS),
         9.6 + 4 / FPS, 11.9),
    ]
    for trim, start, end in rows:
        out = pb.enrich_speech_sequence(
            _passage(**trim), index_dir, frame_rate=FPS)
        (passage,) = out["body_sequence"]
        assert passage["source_start"] == pytest.approx(start), trim
        assert passage["source_end"] == pytest.approx(end), trim
        assert passage["word_timestamps"][0]["source_start"] == (
            pytest.approx(9.6)), trim
    out = pb.enrich_speech_sequence(
        _passage(trim_head_frames=4), index_dir, frame_rate=FPS)
    (report,) = out["alignment_report"]
    assert report["stated_trim"]["trim_head_seconds"] == pytest.approx(
        4 / FPS, abs=1e-3)


def test_frames_without_a_timebase_refuse(index_dir):
    """Frames have no grid without project_fps: refuse, never guess."""
    with pytest.raises(pb.PassageAlignmentError):
        pb.enrich_speech_sequence(
            _passage(trim_head_frames=4), index_dir, frame_rate=None)
