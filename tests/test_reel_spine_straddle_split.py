"""A keep exclusion cutting the middle out of one transcript row must split it.

Reel 21, 2026-09-20: lc-0095 strikes the abandoned run-up tail out of the
middle of one transcript segment. Narrowing the row to its kept words
still spanned the hole - 5.36s of source on 2.03s of reel - and step
4.01 refused the block, because no 1x word mapping can cross a hole the
picture jump-cuts over. The spine now splits the row into one block per
contiguous play span, so every block plays straight through.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import dataclass

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
from library.tools.reel_spine import spine_for_reel


@dataclass
class Moment:
    timeline_start: float
    timeline_end: float
    number: int = 1


def _row(text, tl_start, per_word=0.4):
    parts = text.split()
    return {
        "speaker": "guest", "text": text,
        "timeline_start": tl_start,
        "timeline_end": tl_start + len(parts) * per_word,
        "source_file": "cam_a.mov", "resolve_item_id": "cam_a.mov",
        "source_start": 100.0,
        "source_end": 100.0 + len(parts) * per_word,
        "words": [
            {"word": w, "start": tl_start + i * per_word,
             "end": tl_start + (i + 1) * per_word}
            for i, w in enumerate(parts)
        ],
    }


def test_row_straddling_a_keep_exclusion_splits_in_two():
    """The Reel 21 shape: one row, a 1s hole cut from its middle."""
    row = _row("alpha beta gamma delta epsilon zeta eta theta", 10.0)
    ranges = [(10.0, 11.0), (12.0, 13.2)]
    spine = spine_for_reel(Moment(10.0, 13.2), {"segments": [row]},
                           ranges=ranges)
    blocks = spine["structure"]
    assert len(blocks) == 2
    for block in blocks:
        source_span = block["source_end"] - block["source_start"]
        reel_span = block["timeline_end"] - block["timeline_start"]
        assert source_span <= reel_span * 1.5, (
            f"block {block['position']} still spans a hole: "
            f"{source_span:.2f}s of source on {reel_span:.2f}s of reel")
    words = [w["word"] for b in blocks for w in b["word_timestamps"]]
    assert words == ["alpha", "beta", "gamma",
                     "zeta", "eta", "theta"], (
        "both sides survive, the struck middle does not")


def test_split_spine_plans_captions_without_refusal():
    """The refusal point itself: step 4.01 maps every block 1x."""
    row = _row("alpha beta gamma delta epsilon zeta eta theta", 10.0)
    ranges = [(10.0, 11.0), (12.0, 13.2)]
    spine = spine_for_reel(Moment(10.0, 13.2), {"segments": [row]},
                           ranges=ranges)
    plan = generate_subtitles(spine, caption_case="lowercase",
                              project_folder="")
    entries = plan["subtitle_plan"]["subtitle_entries"]
    assert entries, "both surviving sides must still be captioned"
    assert " ".join(e["text"] for e in entries) == \
        "alpha beta gamma zeta eta theta"


def test_overlapping_words_do_not_split():
    """The aligner's own slop is not a cut: an overlapping pair stays put.

    Same-file transcript overlaps are sequential speech, and the caption
    gate already reads them that way. Splitting on them would trade one
    refusal for overlapping blocks.
    """
    row = _row("alpha beta gamma delta", 10.0)
    row["words"][1]["start"] = 10.3  # beta opens inside alpha's span
    spine = spine_for_reel(Moment(10.0, 12.0), {"segments": [row]},
                           ranges=[(10.0, 12.0)])
    assert len(spine["structure"]) == 1
