"""A mid-sentence transcript row is given back to its sentence.

Both directions: it fires on a fragment whose sentence continues into a
neighbour, and does NOT fire across a cut, a clip or a speaker.

History: `docs/evidence/reel_spine.md` (test_reel_fragment_blocks.py).
"""
from dataclasses import dataclass

import pytest

from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS
from library.tools.reel_spine import spine_for_reel


@dataclass
class Moment:
    timeline_start: float
    timeline_end: float
    cta_start: float = None
    cta_end: float = None
    number: int = 1


def row(speaker, text, tl_start, tl_end, clip, source_start):
    """One transcript row, its words tiling its span evenly."""
    parts = text.split()
    step = (tl_end - tl_start) / len(parts)
    words = [{"word": w,
              "start": round(tl_start + i * step, 4),
              "end": round(tl_start + (i + 1) * step, 4)}
             for i, w in enumerate(parts)]
    return {"speaker": speaker, "text": text,
            "timeline_start": tl_start, "timeline_end": tl_end,
            "source_file": clip, "resolve_item_id": clip,
            "source_start": source_start,
            "source_end": source_start + (tl_end - tl_start),
            "words": words}


def texts(spine):
    return [b["content"]["text"] for b in spine["structure"]]


# ── It fires, and the fragment lands on the right side ──────────────

def test_a_tail_fragment_is_given_back_to_the_sentence_it_ends():
    """Reel 23's own case: "It was definitely going to help" / "them."."""
    transcript = {"segments": [
        row("Akshita", "It was definitely going to help",
            135.616, 136.460, "cam_a", 197.970),
        row("Akshita", "them.", 136.540, 136.721, "cam_a", 198.894),
    ]}
    spine = spine_for_reel(Moment(135.0, 137.5), transcript)

    assert texts(spine) == ["It was definitely going to help them."]
    assert spine["fragment_blocks_merged"] == 1
    assert spine["fragment_blocks_unmerged"] == []
    block = spine["structure"][0]
    assert block["timeline_end"] - block["timeline_start"] \
        >= MIN_CAPTION_DISPLAY_SECONDS
    assert [w["word"] for w in block["word_timestamps"]] == [
        "It", "was", "definitely", "going", "to", "help", "them."]


def test_a_head_fragment_is_given_back_to_the_sentence_it_opens():
    """Reel 23's third card: "that's" heads Craig's next sentence.

    The previous block ENDS a sentence, so the fragment cannot be its
    tail - the transcriber's own full stop is what says so.
    """
    transcript = {"segments": [
        row("Craig", "and that is the whole point.",
            176.0, 179.4, "cam_b", 238.0),
        row("Craig", "that's", 179.830, 179.970, "cam_b", 241.892),
        row("Craig", "why i'm super excited about this",
            180.151, 185.210, "cam_b", 242.213),
    ]}
    spine = spine_for_reel(Moment(175.0, 186.0), transcript)

    assert texts(spine) == ["and that is the whole point.",
                            "that's why i'm super excited about this"]
    assert spine["fragment_blocks_merged"] == 1


def test_the_fragment_joins_the_sentence_the_punctuation_names():
    """Same fragment, both neighbours contiguous - the full stop decides.

    Merging always-backwards would put "For" onto the end of the
    previous sentence, where it stays the block's last card and stays
    short.  The transcriber wrote the full stop; this reads it.
    """
    transcript = {"segments": [
        row("Akshita", "i ran google and chat gpt side by side.",
            10.0, 13.0, "cam_a", 100.0),
        row("Akshita", "For", 13.1, 13.2, "cam_a", 103.1),
        row("Akshita", "google it gave a list from 2023",
            13.3, 16.0, "cam_a", 103.3),
    ]}
    spine = spine_for_reel(Moment(9.0, 17.0), transcript)

    assert texts(spine) == ["i ran google and chat gpt side by side.",
                            "For google it gave a list from 2023"]


def test_a_head_fragment_falls_back_to_the_previous_sentence():
    """Reel 31's case: "Yeah." after "Authority and trust.".

    The previous block ends a sentence, so the punctuation reading says
    the fragment heads the next one - but the next block is the closer
    off another clip, so forward is impossible. Leaving it alone authors
    a 2-frame flash card F7 fails. The previous side plays straight on
    (same speaker, same clip, 101ms), so the fragment closes the
    sentence it follows instead. Forward keeps priority: this only fires
    when the punctuated side cannot take it.
    """
    transcript = {"segments": [
        row("Akshita", "Authority and trust.",
            81.901, 83.42, "cam_a", 4830.045),
        row("Akshita", "Yeah.", 83.521, 83.621, "cam_a", 4831.665),
        row("Akshita", "Let us do the work for you.",
            83.621, 84.7, "cam_b", 3673.531),
    ]}
    spine = spine_for_reel(Moment(81.0, 85.0), transcript)

    assert texts(spine) == ["Authority and trust. Yeah.",
                            "Let us do the work for you."]
    assert spine["fragment_blocks_merged"] == 1
    assert spine["fragment_blocks_unmerged"] == []
    block = spine["structure"][0]
    assert block["timeline_end"] - block["timeline_start"] \
        >= MIN_CAPTION_DISPLAY_SECONDS


# ── It does NOT fire, and says which ones it left ───────────────────

def test_a_fragment_is_left_alone_across_a_cut_and_is_named():
    """The rows abut on the reel but not in the source: a take was cut.

    Merging them would claim one continuous piece of footage that was
    never continuous.
    """
    transcript = {"segments": [
        row("Akshita", "so the thing you have to know is",
            10.0, 12.0, "cam_a", 100.0),
        # 0.08s later on the reel, 3.08s later in the SOURCE.
        row("Akshita", "this.", 12.08, 12.26, "cam_a", 105.08),
    ]}
    spine = spine_for_reel(Moment(9.0, 13.0), transcript)

    assert texts(spine) == ["so the thing you have to know is", "this."]
    assert spine["fragment_blocks_merged"] == 0
    assert spine["fragment_blocks_unmerged"] == ["this."]

    # A head fragment whose previous side is cut too, and whose next block
    # is another clip: nothing continuous can take it, so it stays, named.
    transcript = {"segments": [
        row("Akshita", "Authority and trust.",
            81.901, 83.42, "cam_a", 4830.045),
        # Same reel seconds, but 3 seconds later in the SOURCE: a cut.
        row("Akshita", "Yeah.", 83.521, 83.621, "cam_a", 4834.665),
        row("Akshita", "Let us do the work for you.",
            83.621, 84.7, "cam_b", 3673.531),
    ]}
    spine = spine_for_reel(Moment(81.0, 85.0), transcript)
    assert texts(spine) == ["Authority and trust.", "Yeah.",
                            "Let us do the work for you."]
    assert spine["fragment_blocks_merged"] == 0
    assert spine["fragment_blocks_unmerged"] == ["Yeah."]
