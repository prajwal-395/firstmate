"""`build_actual_script` concatenates the spine's measured words per played
A-roll range in timeline order; every empty lookup is named.

History: docs/evidence/rough_cut_review.md.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_3_03_review_rough_cut.step import (  # noqa: E402
    build_actual_script,
)


def _words(*items):
    """(word, start, end) triples into spine word records."""
    return [
        {"word": w, "source_start": s, "source_end": e}
        for w, s, e in items
    ]


SPINE = {
    "structure": [
        {
            "position": 1,
            "block_type": "speech",
            "clip_id": "clip_001",
            "word_timestamps": _words(
                ("and", 17.6, 17.9),
                ("i", 18.0, 18.1),
                ("have", 18.2, 18.5),
                ("an", 18.6, 18.7),
                ("announcement", 18.8, 19.5),
                ("later", 60.0, 60.5),
            ),
        },
        {
            "position": 2,
            "block_type": "speech",
            "clip_id": "clip_002",
            "word_timestamps": _words(
                ("second", 3.0, 3.4),
                ("thing", 3.5, 3.9),
            ),
        },
    ],
}

A_ROLL = [
    {
        "spine_block_position": 2,
        "block_type": "speech",
        "timeline_start": 4.0,
        "timeline_end": 6.0,
        "video_segments": [
            {"clip_id": "clip_002", "video_in": 3.0, "video_out": 4.0},
        ],
    },
    {
        "spine_block_position": 1,
        "block_type": "speech",
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "video_segments": [
            {"clip_id": "clip_001", "video_in": 17.6, "video_out": 19.6},
        ],
    },
]


def test_words_are_concatenated_in_timeline_order():
    """Assignments arrive out of order; the script follows the
    timeline, and only the words inside each played range."""
    script = build_actual_script(A_ROLL, SPINE)
    assert [b["spine_block_position"] for b in script["blocks"]] == [1, 2]
    assert script["blocks"][0]["text"] == "and i have an announcement"
    assert script["blocks"][1]["text"] == "second thing"
    assert script["full_text"] == (
        "and i have an announcement second thing")
    assert script["unvoiced"] == []


def test_an_empty_lookup_is_named_not_skipped():
    """A played range with no measured words, and a segment with no
    source range, each land in `unvoiced` with a reason."""
    no_words = [{
        "spine_block_position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 2.0,
        "video_segments": [
            {"clip_id": "clip_001", "video_in": 40.0, "video_out": 42.0},
        ],
    }]
    script = build_actual_script(no_words, SPINE)
    assert script["blocks"] == []
    assert script["full_text"] == ""
    assert len(script["unvoiced"]) == 1
    entry = script["unvoiced"][0]
    assert entry["spine_block_position"] == 1
    assert entry["source_in"] == 40.0
    assert "reason" in entry and entry["reason"]
    no_range = [{
        "spine_block_position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 2.0,
        "video_segments": [{"clip_id": "clip_001"}],
    }]
    script = build_actual_script(no_range, SPINE)
    assert len(script["unvoiced"]) == 1
    assert "no source range" in script["unvoiced"][0]["reason"]
