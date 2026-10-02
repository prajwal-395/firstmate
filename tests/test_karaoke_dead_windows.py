"""Collapsed karaoke windows: every planned word is drawn, the sweep skips what it cannot cross.

Reel 05, timeline frame 241 (captain's note: "the subtitles are messed up
here"): seven collapsed highlight windows across three caption cards. The aligner
stamped six transcript words onto one 0.02s instant (segment 87,
353.89-353.91) plus an overlapping '10-man', step 4.01 grouped them without
validating, and step 4.05's card-bounding clamp (`max`/`min` in
`generate_remotion_props`) collapses each to startFrame == endFrame. A window
with no width can never satisfy the accent phase, so the sweep never moves
over those words.

The 2026-09-19 contract pinned here SKIPPED those words - omitted them from
the props with a loud record ("honest absence"). The captain's 2026-09-21
rewrite ("not all the words are being included, specifically the numbers")
is that contract failing in the field: the renderer draws `words`, never
the card `text`, so the omitted words - "10-man", "50-person", "hundred" -
vanished from the captions entirely while the gates stayed green (coverage
forgave them off the undrawn `text` field). An absence the viewer reads as
missing numbers is not honest.

The contract pinned here, both halves:

1. Every planned word is DRAWN - kept in the props with its clamped
   (possibly collapsed) window, never omitted and never re-timed. Kept
   words keep their measured frames exactly.
2. The sweep skips a collapsed window rather than accenting it - the word
   renders in the base colour at full size and never takes the accent,
   because no frame satisfies the sweep phase. No timing is invented.
3. The clamp says what it left unswept and why - naming the word, the
   card and the reason - instead of turning a window into nothing
   silently.

Numbers below are frozen off the real artefacts: transcript segment 87
(`pipeline_output/scratch/timeline_transcript/transcript.json`,
349.95-358.63) and the three placed props
(`sub_craig_7b1a6b77-..._633316-641996_{834c5d8d,7c1f3937,bf315839}`).
Times are shifted onto small card bounds preserving the measured shape
(six identical stamps, one overlap); the frames quoted are what the
builder really emits at 24000/1001. No test reaches a real project.
"""

import pathlib
import re

from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
    generate_subtitle_props_per_block,
)

FPS = 24000 / 1001

STYLE = {
    "fontFamily": "Montserrat",
    "fontSize": 58,
    "fontWeight": 800,
    "position": "bottom",
    "safeArea": {"top": 120, "right": 90, "bottom": 324, "left": 90},
    "captionMaxWidth": 900,
}


def _word(word, start, end):
    return {"word": word, "start": start, "end": end}


def _entry(block, index, tl_start, tl_end, words, text=None):
    return {
        "id": f"sub_{block}_{index + 1:03d}",
        "card_index": index,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "text": text if text is not None else " ".join(w[0] for w in words),
        "emphasis_words": [],
        "spine_block_position": block,
        "speaker": "Craig",
        "word_count": len(words),
        "words": [_word(w, s, e) for w, s, e in words],
    }


def _segment_87_plan():
    """The three Reel 05 cards, measured shape preserved.

    Card 1 ends on '10-man', whose 0.02s stamp overlaps its neighbours.
    Card 2 opens on the card the pile-up lands in. Card 3 holds four of
    the six identically-stamped words plus the healthy words around them.
    """
    pile = [("50-person", 5.89, 5.91), ("shop,", 5.89, 5.91),
            ("maybe", 5.89, 5.91), ("you're", 5.89, 5.91),
            ("a", 5.89, 5.91), ("hundred", 5.89, 5.91)]
    return {
        "subtitle_entries": [
            _entry(1, 1, 9.24, 11.13, [
                ("of", 9.24, 9.31), ("a", 9.31, 9.37),
                ("business,", 9.37, 9.94), ("you're", 9.94, 10.12),
                ("a", 10.12, 11.06), ("10-man", 11.11, 11.13),
            ]),
            _entry(1, 2, 11.13, 11.93, [
                ("shop,", 11.13, 11.39), ("you're", 11.39, 11.51),
                ("a", 11.51, 11.87),
            ] + pile[:2]),
            _entry(1, 3, 11.93, 14.06, pile[2:] + [
                ("person", 11.87, 12.40), ("shop,", 12.40, 14.06),
            ]),
        ],
        "style": STYLE,
    }


def _rendered_words(props):
    out = []
    for p in props:
        for sub in p["subtitles"]:
            for w in sub["words"]:
                out.append((w["word"], w["startFrame"], w["endFrame"]))
    return out


# ── Half 2: every planned word is drawn, the collapse is named ────

class TestPileUpWindowsAreDrawnAndNamed:
    def test_every_planned_word_is_drawn_and_the_collapse_named(
            self, capsys):
        """Reel 05 frame 241 (the numbers): no planned word is omitted;
        pile-up windows arrive collapsed (drawn unswept), never widened
        into invented timing; healthy words keep their measured frames;
        and every unswept word is named."""
        props = generate_subtitle_props_per_block(
            _segment_87_plan(), fps=FPS, width=904, height=480)
        rendered_words = _rendered_words(props)
        planned = [w["word"] for e in _segment_87_plan()["subtitle_entries"]
                   for w in e["words"]]
        assert [w for w, _, _ in rendered_words] == planned, (
            "a planned word never reaches the renderer")

        pile = {"10-man", "50-person", "shop,", "maybe", "you're", "a",
                "hundred"}
        collapsed = set()
        for word, start, end in rendered_words:
            if end <= start:
                assert word in pile, (
                    f"{word!r} renders {start} -> {end}: a collapsed "
                    f"window outside the measured pile-up")
                collapsed.add(word)
        assert {"10-man", "50-person", "maybe", "hundred"} <= collapsed

        # 'business,' spans the card interior: pure measurement.
        render_start = 9.24 - 0.5
        expect = (round((9.37 - render_start) * FPS),
                  round((9.94 - render_start) * FPS))
        assert ("business,", *expect) in set(rendered_words)

        err = capsys.readouterr().err
        for unswept in ("10-man", "50-person", "maybe", "hundred",
                        "you're", "shop,", "a"):
            assert unswept in err, f"unswept {unswept!r} is never named"
        assert "unswept" in err


class TestInvertedOverhangIsDrawnAndNamed:
    def test_word_ending_before_its_card_begins_is_drawn_unswept(
            self, capsys):
        # Reel 12's 'twenty' shape: the word ends before its own card
        # begins, so the card bound inverts it (startFrame 12 endFrame 6
        # in the real props). It must still be drawn (the card text
        # carries it), unswept, and the record must name it.
        plan = {
            "subtitle_entries": [_entry(2, 0, 11.43, 12.51, [
                ("twenty", 10.93, 11.16), ("percent.", 11.43, 11.64),
            ])],
            "style": STYLE,
        }
        props = generate_subtitle_props_per_block(
            plan, fps=FPS, width=904, height=480)
        rendered = {w: (s, e) for w, s, e in _rendered_words(props)}
        assert "twenty" in rendered
        assert "percent." in rendered
        start, end = rendered["twenty"]
        assert end <= start, (
            "the overhang must arrive collapsed (drawn unswept), never "
            "widened into invented timing")
        err = capsys.readouterr().err
        assert "twenty" in err


class TestHealthyCardsStaySilent:
    def test_no_unswept_no_record(self, capsys):
        plan = {
            "subtitle_entries": [_entry(0, 0, 0.0, 1.2, [
                ("what", 0.0, 0.3), ("kind", 0.35, 0.6),
                ("of", 0.65, 0.8), ("content", 0.85, 1.2),
            ])],
            "style": STYLE,
        }
        props = generate_subtitle_props_per_block(
            plan, fps=FPS, width=1080, height=1920)
        assert [w for w, _, _ in _rendered_words(props)] == [
            "what", "kind", "of", "content"]
        assert capsys.readouterr().err == ""


# ── Half 1: the sweep skips a collapsed window ────────────────────
#
# No JS runner exists in this repo, so the renderer side is covered
# structurally - the same shape as test_subtitle_emphasis.py: the guard
# that must be at the call site is asserted in source.

REPO = pathlib.Path(__file__).resolve().parent.parent
WORD = (REPO / "remotion-subtitles/src/compositions/SubtitleOverlay"
        / "AnimatedWord.tsx")


class TestRendererSkipsEmptyWindows:
    def test_collapsed_windows_draw_without_accent(self):
        source = WORD.read_text()
        # The accent phase must require a window with width: a
        # collapsed window never takes the accent, while the word
        # itself is still drawn (no `return null` for it - that
        # deleted Reel 05's numbers from the captions).
        guard = re.search(
            r"endFrame\s*>\s*startFrame\s*&&\s*frame\s* >= \s*startFrame"
            .replace(" ", r"\s*"),
            source)
        assert guard, (
            "AnimatedWord accents collapsed highlight windows or "
            "refuses to draw them: a word with endFrame <= startFrame "
            "must render in the base colour with no accent sweep "
            "(Reel 05 frame 241)")
        assert not re.search(
            r"if\s*\(\s*endFrame\s*<=\s*startFrame\s*\)\s*\{?"
            r"\s*return\s+null",
            source), (
            "AnimatedWord drops collapsed-window words instead of "
            "drawing them unswept: the renderer draws `words`, never "
            "the card `text`, so a null here deletes the word from "
            "the captions (Reel 05 frame 241: the numbers)")
