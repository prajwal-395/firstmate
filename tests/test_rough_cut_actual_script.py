"""Check 5's reconstruction is script work, and now a script does it.

Step 3.03's handoff asks the model to look up the temporal index per
A-roll range and concatenate the words in timeline order - range
lookup plus string joining over word timings the pipeline already
holds. `build_actual_script` (in step.py, beside the mechanical
checks whose inputs it shares) builds it off the spine's own word
timings instead, so the narrative review judges the measured script
rather than a reconstruction.

Deliberately NOT in `library/tools/timeline_transcript.py`: that
module rebuilds timeline audio from source media and transcribes it
with Whisper for live Resolve timelines that carry no measured words
at all. Concatenating already-measured words is a different mechanism
for a different input; forcing it in there would be the second
mechanism the WP2a brief says to stop at.

Proven in both directions: correct concatenation passes, and every
way the lookup can come up empty is named rather than skipped.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def test_words_outside_the_played_range_are_excluded():
    """'later' at 60s is measured on the block but not played."""
    script = build_actual_script(A_ROLL, SPINE)
    assert "later" not in script["full_text"]


def test_a_range_with_no_measured_words_is_named_not_skipped():
    a_roll = [
        {
            "spine_block_position": 1,
            "block_type": "speech",
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "video_segments": [
                {"clip_id": "clip_001", "video_in": 40.0, "video_out": 42.0},
            ],
        },
    ]
    script = build_actual_script(a_roll, SPINE)
    assert script["blocks"] == []
    assert script["full_text"] == ""
    assert len(script["unvoiced"]) == 1
    entry = script["unvoiced"][0]
    assert entry["spine_block_position"] == 1
    assert entry["source_in"] == 40.0
    assert "reason" in entry and entry["reason"]


def test_a_block_with_no_word_timings_is_undetermined():
    spine = {"structure": [
        {"position": 1, "block_type": "speech", "clip_id": "clip_001",
         "word_timestamps": []},
    ]}
    a_roll = [
        {
            "spine_block_position": 1,
            "block_type": "speech",
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "video_segments": [
                {"clip_id": "clip_001", "video_in": 0.0, "video_out": 2.0},
            ],
        },
    ]
    script = build_actual_script(a_roll, spine)
    assert script["blocks"] == []
    assert len(script["unvoiced"]) == 1
    assert "undetermined" in script["unvoiced"][0]["reason"]


def test_a_segment_with_no_source_range_is_named():
    a_roll = [
        {
            "spine_block_position": 1,
            "block_type": "speech",
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "video_segments": [{"clip_id": "clip_001"}],
        },
    ]
    script = build_actual_script(a_roll, SPINE)
    assert len(script["unvoiced"]) == 1
    assert "no source range" in script["unvoiced"][0]["reason"]


def test_an_unknown_block_position_is_undetermined():
    a_roll = [
        {
            "spine_block_position": 99,
            "block_type": "speech",
            "timeline_start": 0.0,
            "timeline_end": 2.0,
            "video_segments": [
                {"clip_id": "clip_001", "video_in": 0.0, "video_out": 2.0},
            ],
        },
    ]
    script = build_actual_script(a_roll, SPINE)
    assert script["blocks"] == []
    assert len(script["unvoiced"]) == 1
