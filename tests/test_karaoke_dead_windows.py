"""Dead karaoke windows: a zero-width highlight sweep must be skipped loudly.

Reel 05, timeline frame 241 (captain's note: "the subtitles are messed up
here"): seven empty karaoke windows across three caption cards. The aligner
stamped six transcript words onto one 0.02s instant (segment 87,
353.89-353.91) plus an overlapping '10-man', step 4.01 grouped them without
validating, and step 4.05's card-bounding clamp (`max`/`min` in
`generate_remotion_props`) quietly collapsed each to startFrame ==
endFrame. A window with no width can never satisfy `frame >= startFrame
&& frame < endFrame`, so the accent sweep never moves over those words.

The contract pinned here, both halves named by the lane that found it:

1. The sweep skips a zero-width window rather than rendering a dead one -
   the word is absent (honest absence), never drawn permanently unspoken.
2. The clamp says what it skipped and why - naming the word, the card and
   the reason - instead of turning a window into nothing silently.

Neither half invents or adjusts a timing: skipped words are omitted, kept
words keep their measured frames exactly.

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


# ── Half 2: the clamp refuses loudly ─────────────────────────────

class TestPileUpWindowsAreSkippedAndNamed:
    def test_seven_dead_windows_become_absences(self):
        props = generate_subtitle_props_per_block(
            _segment_87_plan(), fps=FPS, width=904, height=480)
        rendered = [w for w, _, _ in _rendered_words(props)]
        for dead in ("10-man", "50-person", "maybe", "hundred"):
            assert dead not in rendered, (
                f"{dead!r} renders with no time on screen - a dead sweep")
        for word, start, end in _rendered_words(props):
            assert end > start, (
                f"{word!r} renders {start} -> {end}: a window with no "
                f"width the sweep can never cross")

    def test_healthy_words_keep_their_measured_frames(self):
        props = generate_subtitle_props_per_block(
            _segment_87_plan(), fps=FPS, width=904, height=480)
        rendered = {(w, s, e) for w, s, e in _rendered_words(props)}
        # 'business,' spans the card interior: clamping never touches it,
        # so its frames are pure measurement, unchanged by the skip.
        render_start = 9.24 - 0.5
        expect = (round((9.37 - render_start) * FPS),
                  round((9.94 - render_start) * FPS))
        assert ("business,", *expect) in rendered

    def test_the_skip_names_every_word_and_why(self, capsys):
        generate_subtitle_props_per_block(
            _segment_87_plan(), fps=FPS, width=904, height=480)
        err = capsys.readouterr().err
        for dead in ("10-man", "50-person", "maybe", "hundred",
                     "you're", "shop,", "a"):
            assert dead in err, (
                f"skipped {dead!r} is never named - the clamp is silent "
                f"about the window it turned into nothing")
        assert "zero-width" in err or "no width" in err or "empty" in err


class TestInvertedOverhangIsSkippedAndNamed:
    def test_word_ending_before_its_card_begins_is_an_honest_absence(
            self, capsys):
        # Reel 12's 'twenty' shape: the word ends before its own card
        # begins, so the card bound inverts it (startFrame 12 endFrame 6
        # in the real props). It must not render, and the record must
        # name it.
        plan = {
            "subtitle_entries": [_entry(2, 0, 11.43, 12.51, [
                ("twenty", 10.93, 11.16), ("percent.", 11.43, 11.64),
            ])],
            "style": STYLE,
        }
        props = generate_subtitle_props_per_block(
            plan, fps=FPS, width=904, height=480)
        rendered = [w for w, _, _ in _rendered_words(props)]
        assert "twenty" not in rendered
        assert "percent." in rendered
        err = capsys.readouterr().err
        assert "twenty" in err


class TestHealthyCardsStaySilent:
    def test_no_skip_no_record(self, capsys):
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


# ── Half 1: the sweep skips a zero-width window ───────────────────
#
# No JS runner exists in this repo, so the renderer side is covered
# structurally - the same shape as test_subtitle_emphasis.py: the guard
# that must be at the call site is asserted in source.

REPO = pathlib.Path(__file__).resolve().parent.parent
WORD = (REPO / "remotion-subtitles/src/compositions/SubtitleOverlay"
        / "AnimatedWord.tsx")


class TestRendererSkipsEmptyWindows:
    def test_zero_width_words_draw_nothing(self):
        source = WORD.read_text()
        guard = re.search(
            r"if\s*\(\s*endFrame\s*<=\s*startFrame\s*\)\s*\{?"
            r"\s*return\s+null",
            source)
        assert guard, (
            "AnimatedWord draws zero-width highlight windows instead of "
            "skipping them: a word with endFrame <= startFrame can never "
            "satisfy the accent phase, so it sits permanently unspoken "
            "while the sweep travels past it (Reel 05 frame 241)")
