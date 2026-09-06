"""A mid-sentence transcript row is given back to its sentence.

The defect, on Reel 23 of the captain's field test: three caption cards
under half a second - ``them.`` 0.181s, ``comes in.`` 0.422s and
``that's`` 0.140s.  None was a grouping fault.  Each was a WHOLE spine
block, because the reel spine makes one block per transcript row, and
each of those rows was one half of a sentence WhisperX split in two:

    135.616-136.460  "It was definitely going to help"
    136.540-136.721  "them."

Same speaker, same clip, an 80ms pause and nothing removed.  Step 4.01
groups WITHIN a block and clamps every card to it, so the second row's
block is 0.181s and its only card is 0.181s at every partition.  The
block is the ceiling on the card, so the block is where it is fixed.

These tests prove BOTH directions, because a rule that only ever fires
is as useless as one that never does:

* it fires on a fragment whose sentence continues into a neighbour, and
  the short card is gone;
* it does NOT fire across a cut, across a clip, across a speaker, or on
  a block long enough to carry a card - and a spine with no fragment in
  it comes back unchanged, block for block.
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


def test_a_fragment_is_left_alone_when_the_neighbour_is_another_speaker():
    transcript = {"segments": [
        row("Akshita", "and that is exactly the problem.",
            10.0, 13.0, "cam_a", 100.0),
        row("Craig", "yeah.", 13.1, 13.22, "cam_b", 200.0),
        row("Akshita", "so we went and looked at the data",
            13.4, 16.0, "cam_a", 103.4),
    ]}
    spine = spine_for_reel(Moment(9.0, 17.0), transcript)

    assert "yeah." in texts(spine)
    assert spine["fragment_blocks_merged"] == 0
    assert spine["fragment_blocks_unmerged"] == ["yeah."]


def test_a_fragment_is_left_alone_when_the_neighbour_is_another_clip():
    transcript = {"segments": [
        row("Akshita", "and that is exactly the problem.",
            10.0, 13.0, "cam_a", 100.0),
        row("Akshita", "so", 13.1, 13.3, "cam_b", 300.0),
        row("Akshita", "we went and looked at the data",
            13.4, 16.0, "cam_b", 300.3),
    ]}
    spine = spine_for_reel(Moment(9.0, 17.0), transcript)

    # It heads the next sentence and cam_b carries both, so it merges
    # forward - onto the clip it is actually on, never onto cam_a.
    assert texts(spine) == ["and that is exactly the problem.",
                            "so we went and looked at the data"]
    assert all(b["clip_id"] == "cam_b" or b["content"]["text"].endswith(
        "problem.") for b in spine["structure"])


# ── It does not fire on correct output ──────────────────────────────

def test_a_spine_with_no_fragment_comes_back_untouched():
    """The gate's other direction: nothing to fix, nothing changed.

    Every block here is comfortably above the floor, so the pass must
    return the same blocks, in the same order, with the same spans.
    """
    transcript = {"segments": [
        row("Akshita", "search did not change the question did",
            10.0, 13.0, "cam_a", 100.0),
        row("Craig", "and whoever ai understands best gets the answer",
            13.2, 16.4, "cam_b", 200.0),
        row("Akshita", "which is the whole game now",
            16.6, 19.0, "cam_a", 106.6),
    ]}
    spine = spine_for_reel(Moment(9.0, 20.0), transcript)

    assert spine["fragment_blocks_merged"] == 0
    assert spine["fragment_blocks_unmerged"] == []
    assert texts(spine) == [
        "search did not change the question did",
        "and whoever ai understands best gets the answer",
        "which is the whole game now"]
    assert [b["position"] for b in spine["structure"]] == [0, 1, 2]


def test_a_block_exactly_on_the_floor_is_not_a_fragment():
    """The floor is the gate's floor, and it is not re-derived here."""
    transcript = {"segments": [
        row("Akshita", "so here is the thing about that",
            10.0, 13.0, "cam_a", 100.0),
        row("Akshita", "exactly.", 13.1,
            13.1 + MIN_CAPTION_DISPLAY_SECONDS, "cam_a", 103.1),
    ]}
    spine = spine_for_reel(Moment(9.0, 14.5), transcript)

    assert spine["fragment_blocks_merged"] == 0
    assert texts(spine) == ["so here is the thing about that", "exactly."]


def test_two_fragments_in_a_row_are_both_given_back():
    """"yada," / "yada." - the episode really contains this."""
    transcript = {"segments": [
        row("Craig", "five star reviews and specializes in",
            10.0, 12.0, "cam_b", 200.0),
        row("Craig", "yada,", 12.05, 12.14, "cam_b", 202.05),
        row("Craig", "yada.", 12.2, 12.49, "cam_b", 202.2),
    ]}
    spine = spine_for_reel(Moment(9.0, 13.0), transcript)

    assert texts(spine) == [
        "five star reviews and specializes in yada, yada."]
    assert spine["fragment_blocks_merged"] == 2


def test_a_merged_block_still_satisfies_the_spine_contract():
    """`spine_for_reel` validates before it returns; this pins that the
    merge cannot be what breaks it."""
    transcript = {"segments": [
        row("Akshita", "it was definitely going to help",
            10.0, 12.0, "cam_a", 100.0),
        row("Akshita", "them.", 12.08, 12.26, "cam_a", 102.08),
    ]}
    spine = spine_for_reel(Moment(9.0, 13.0), transcript)
    block = spine["structure"][0]

    # Source and reel spans agree with the words that are now in it.
    assert block["source_start"] == pytest.approx(100.0)
    assert block["source_end"] == pytest.approx(102.26)
    # The reel opens at 9.0, so master 10.0 is reel second 1.0.
    assert block["timeline_start"] == pytest.approx(1.0, abs=1e-6)
    assert block["timeline_end"] == pytest.approx(3.26, abs=1e-6)
    words = block["word_timestamps"]
    assert words[0]["source_start"] == pytest.approx(100.0, abs=1e-3)
    assert words[-1]["source_end"] == pytest.approx(102.26, abs=1e-3)
    assert all(a["source_end"] <= b["source_start"] + 1e-6
               for a, b in zip(words, words[1:]))
