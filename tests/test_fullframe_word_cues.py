"""Word-paced reveals: animation that lands ON a word, not across a card.

Found 2026-09-08 by the animated-reel lane
(docs/ANIMATED_REEL_CEILING.md): `typewriter`, `mask` and `draw` reveals
were time-based across a card's own span, and no composition took word
timestamps - while the pipeline already HAD them (the caption path reads
them). A routing gap, not a measurement gap.

The shape follows `channel_bug`, not a second mechanism: the declaration
opts in (`word_sync`), the plan measures the cues out of the transcript
at plan time and refuses by name when there is nothing to land on, and
the composition draws them - or refuses nothing, because absent cues
take the frame-clock path exactly as before.

Every gate here is proved in BOTH directions, and the drawing itself is
proved by RENDERING, not by asserting a node exists: the still test
below fails on a composition that ignores the prop, because a source
parse proves existence and only pixels prove drawing.
"""
import copy
import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import full_frame_element as ffe  # noqa: E402

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

renders_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None,
    reason="needs remotion-subtitles/node_modules and npx",
)

FPS = 30
WIDTH = 540
HEIGHT = 960

SPAN = {
    "element": "full_frame_span",
    "placement": "span",
    "background": "#101014",
    "font_family": "Montserrat",
    "entrance": "typewriter",
    "segments": [
        {"runs": [
            {"bind": "range_line",
             "type_role": "display", "colour": "#FFFFFF"}]},
    ],
}

RANGES = [(10.0, 11.0)]


def declare(**overrides):
    entry = copy.deepcopy(SPAN)
    entry.update(overrides)
    return {"full_frame_elements": [entry]}


def _transcript(words):
    timed = [{"word": w, "start": s, "end": e, "timed": True}
             for w, s, e in words]
    return {"segments": [
        {"speaker": "Akshita", "timeline_start": 10.0, "timeline_end": 11.0,
         "text": " ".join(w for w, _, _ in words), "words": timed},
    ]}


def _two_word_transcript():
    return _transcript([("ALPHA", 10.0, 10.1), ("BETA", 10.8, 10.9)])


class _Moment:
    number = 1
    speakers = ("Akshita",)


def _facts(ranges=RANGES, transcript=None):
    return ffe.ReelFacts.from_moment(
        _Moment(), ranges,
        _two_word_transcript() if transcript is None else transcript,
        opening_seconds=3.0)


def _plan(declarations=None, ranges=RANGES, transcript=None):
    declarations = (ffe.declared_elements(declare(word_sync=True))
                    if declarations is None else declarations)
    body = sum(int(round(e * FPS)) - int(round(s * FPS))
               for s, e in ranges)
    return ffe.plan_reel_cards(
        declarations, _facts(ranges, transcript), body, FPS,
        width=WIDTH, height=HEIGHT,
        ranges=list(ranges),
        transcript=_two_word_transcript()
        if transcript is None else transcript)


# ── The declaration ──────────────────────────────────────────────


def test_word_sync_defaults_to_off():
    """Props written before this slot existed render byte-identically."""
    assert ffe.declared_elements(declare())[0]["word_sync"] is False
    card = {
        "element": "full_frame_card",
        "placement": "head",
        "duration_seconds": 2.0,
        "background": "#000000",
        "font_family": "Montserrat",
        "runs": [{"text": "x", "type_role": "display",
                  "colour": "#FFF"}],
    }
    assert ffe.declared_elements(
        {"full_frame_elements": [card]})[0]["word_sync"] is False


@pytest.mark.parametrize("value", ["words", 1, 0, None, [], {}])
def test_a_non_bool_word_sync_is_refused_by_name(value):
    with pytest.raises(ffe.FullFrameDeclarationError, match="word_sync"):
        ffe.declared_elements(declare(word_sync=value))


def test_a_card_may_not_take_word_sync():
    """A card covers its own seconds, not speech seconds.

    The words it quotes are spoken before or after it plays, so no word
    clock runs while it is on screen. The load-bearing refusal: the
    mid-reel card refusal stays untouched because this never became a
    reason to put a card over speech.
    """
    card = {
        "element": "full_frame_card",
        "placement": "head",
        "duration_seconds": 2.0,
        "background": "#000000",
        "font_family": "Montserrat",
        "word_sync": True,
        "runs": [{"bind": "opening_line", "type_role": "display",
                  "colour": "#FFF"}],
    }
    with pytest.raises(ffe.FullFrameDeclarationError, match="own seconds"):
        ffe.declared_elements({"full_frame_elements": [card]})


@pytest.mark.parametrize("entrance", ["cut", "fade", "slide", "blur",
                                      "glitch", "scale", "flip"])
def test_word_sync_beside_an_uncuable_entrance_is_refused(entrance):
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="can pace"):
        ffe.declared_elements(declare(word_sync=True, entrance=entrance))


@pytest.mark.parametrize("entrance", ["typewriter", "mask", "draw"])
def test_word_sync_beside_a_cued_entrance_is_accepted(entrance):
    """The other half of every refusal above."""
    assert ffe.declared_elements(
        declare(word_sync=True, entrance=entrance))[0]["word_sync"] is True


# ── Planning ─────────────────────────────────────────────────────


def test_cues_are_frame_exact_and_cumulative():
    planned = _plan()
    cues = planned[0].props["wordCues"]
    assert [c["word"] for c in cues] == ["ALPHA", "BETA"]
    # Segment-local seconds on the plan's own rounding: 10.0-10.1 and
    # 10.8-10.9 of the master read 0.0-0.1 and 0.8-0.9 of the segment.
    assert cues[0]["start"] == pytest.approx(0.0)
    assert cues[0]["end"] == pytest.approx(0.1)
    assert cues[1]["start"] == pytest.approx(0.8)
    assert cues[1]["end"] == pytest.approx(0.9)
    # Cumulative characters over the resolved runs: "ALPHA BETA" shows
    # 5 through the first word and all 10 through the second.
    assert [c["chars"] for c in cues] == [5, 10]


def test_no_cues_without_the_declaration():
    """Absent unless declared: byte-identical props for every plan on disk."""
    planned = _plan(ffe.declared_elements(declare()))
    assert "wordCues" not in planned[0].props


def test_a_range_with_no_timed_words_refuses():
    """No clock to pace off - refused, never silently time-based."""
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["segments"] = [{"runs": [
        {"text": "ALPHA BETA", "type_role": "display",
         "colour": "#FFFFFF"}]}]
    silent = {"segments": [
        {"speaker": "Nobody", "timeline_start": 10.0, "timeline_end": 11.0,
         "text": "no timings here", "words": []},
    ]}
    with pytest.raises(ffe.FullFrameDeclarationError, match="no timed words"):
        _plan(ffe.declared_elements({"full_frame_elements": [entry]}),
              transcript=silent)


def test_an_empty_quotation_refuses_before_the_clock_is_read():
    """Ordering: `range_line` with nothing behind it refuses as it always
    has - word_sync never gets far enough to soften that refusal."""
    silent = {"segments": [
        {"speaker": "Nobody", "timeline_start": 10.0, "timeline_end": 11.0,
         "text": "no timings here", "words": []},
    ]}
    with pytest.raises(ffe.FullFrameDeclarationError, match="nothing there"):
        _plan(transcript=silent)


def test_literal_text_that_is_not_the_range_refuses():
    """A cue boundary must coincide with a word boundary on screen."""
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["segments"] = [{"runs": [
        {"text": "Something else entirely", "type_role": "display",
         "colour": "#FFFFFF"}]}]
    with pytest.raises(ffe.FullFrameDeclarationError, match="exactly"):
        _plan(ffe.declared_elements({"full_frame_elements": [entry]}))


def test_an_eyebrow_beside_the_quotation_refuses():
    """Two texts, one clock: the clock owns the quotation, not the card."""
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["segments"] = [{"runs": [
        {"bind": "speakers", "type_role": "micro", "colour": "#FFAA4D"},
        {"bind": "range_line", "type_role": "display",
         "colour": "#FFFFFF"}]}]
    facts = ffe.ReelFacts(reel_number=1, speakers=("Akshita",), opening=(),
                          opening_window=3.0)
    body = sum(int(round(e * FPS)) - int(round(s * FPS))
               for s, e in RANGES)
    with pytest.raises(ffe.FullFrameDeclarationError, match="exactly"):
        ffe.plan_reel_cards(
            ffe.declared_elements({"full_frame_elements": [entry]}),
            facts, body, FPS, width=WIDTH, height=HEIGHT,
            ranges=list(RANGES), transcript=_two_word_transcript())


def test_an_uppercased_quotation_still_lands():
    """Case is the declaration's choice; the clock reads through it."""
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["segments"] = [{"runs": [
        {"bind": "range_line", "type_role": "display",
         "colour": "#FFFFFF", "uppercase": True}]}]
    planned = _plan(ffe.declared_elements({"full_frame_elements": [entry]}))
    assert planned[0].props["runs"][0]["text"] == "ALPHA BETA"
    assert [c["chars"] for c in planned[0].props["wordCues"]] == [5, 10]


# ── The render proves the drawing ────────────────────────────────

# Word 1 at 0.0-0.1s, word 2 at 0.8-0.9s of a 1.0s segment @30fps.
# Frame 20 (t=0.667s) is past every time-based ramp (typewriter 20,
# mask 8, draw 12) yet before the second word starts: the frame-clock
# still shows everything while the word clock shows "ALPHA" alone.
# Frame 28 is past the second word's start: both clocks show everything.
EARLY_FRAME = 20
LATE_FRAME = 28
GROUND_RGB = (0x10, 0x10, 0x14)


def _ink_count(image, ground=GROUND_RGB, tolerance=30):
    pixels = image.load()
    width, height = image.size
    return sum(
        1 for y in range(height) for x in range(width)
        if any(abs(c - t) > tolerance
               for c, t in zip(pixels[x, y][:3], ground)))


def _still(props: dict, tmp_path, name: str, frame: int):
    from PIL import Image
    props_path = tmp_path / f"{name}.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    out_path = tmp_path / f"{name}.png"
    result = subprocess.run(
        ["npx", "remotion", "still", "FullFrameCard", str(out_path),
         f"--frame={frame}", f"--props={props_path}",
         "--image-format=png"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return Image.open(out_path).convert("RGB")


@renders_available
@pytest.mark.parametrize("entrance", ["typewriter", "mask", "draw"])
def test_the_reveal_lands_on_the_word(tmp_path, entrance):
    """The cue-driven frame differs from the time-driven one, on purpose.

    The props come out of `_plan_span`, not out of a hand-built dict -
    so this pins the declaration-to-pixels path, and fails on a plan
    that drops the cues or a composition that ignores them: without the
    cue-driven node both stills render the full line and the early
    assertion below fails.
    """
    pytest.importorskip("PIL", reason="needs Pillow to measure the stills")
    entry = copy.deepcopy(SPAN)
    entry["entrance"] = entrance
    entry["word_sync"] = True
    planned = _plan(ffe.declared_elements({"full_frame_elements": [entry]}))
    assert len(planned) == 1
    cued_props = planned[0].props
    assert cued_props["runs"][0]["text"] == "ALPHA BETA"

    timed_props = dict(cued_props)
    del timed_props["wordCues"]

    early_cued = _still(cued_props, tmp_path, f"{entrance}_cued_early",
                        EARLY_FRAME)
    early_timed = _still(timed_props, tmp_path, f"{entrance}_timed_early",
                         EARLY_FRAME)
    late_cued = _still(cued_props, tmp_path, f"{entrance}_cued_late",
                       LATE_FRAME)
    late_timed = _still(timed_props, tmp_path, f"{entrance}_timed_late",
                        LATE_FRAME)

    assert early_cued.size == early_timed.size == (WIDTH, HEIGHT)
    timed_ink = _ink_count(early_timed)
    assert timed_ink > 500, (
        f"{entrance}: the time-based control drew no text at frame "
        f"{EARLY_FRAME} - the control frame is empty")
    cued_ink = _ink_count(early_cued)
    # Only "ALPHA" of "ALPHA BETA" is on screen: roughly half the ink.
    # The bound is generous - what it must beat is equality, which is
    # what a composition ignoring `wordCues` renders.
    assert cued_ink < 0.65 * timed_ink, (
        f"{entrance}: the word-cued frame drew {cued_ink}px against "
        f"{timed_ink}px time-based at frame {EARLY_FRAME} - the reveal "
        f"did not wait for the word")
    assert cued_ink > 200, (
        f"{entrance}: the word-cued frame drew only {cued_ink}px - the "
        f"first word never arrived")

    # Past the second word's start both clocks show the whole line: the
    # cues pace the reveal, they do not hide text.
    assert _ink_count(late_timed) > 500
    late_ratio = _ink_count(late_cued) / _ink_count(late_timed)
    assert 0.9 < late_ratio < 1.1, (
        f"{entrance}: past the last word the cued frame has {late_ratio:.2f}x "
        f"the timed frame's ink - the cues must converge, not hide")


def _right_crop_ink(image, fraction=0.6, ground=GROUND_RGB, tolerance=30):
    """Ink in the right-hand crop, where the SECOND word lives."""
    pixels = image.load()
    width, height = image.size
    return sum(
        1 for y in range(height) for x in range(int(width * fraction), width)
        if any(abs(c - t) > tolerance
               for c, t in zip(pixels[x, y][:3], ground)))


@renders_available
def test_mask_rises_words_not_blocks(tmp_path):
    """A cued mask rises each word out of its own mask - pixels prove it.

    At frame 20 (t=0.667s) "ALPHA" (cued 0.0-0.1s) has settled and "BETA"
    (cued 0.8-0.9s) is unspoken. The old block-level wipe drew the whole
    laid-out line clipped to the left half - which leaves the left halves
    of BETA's glyphs in the frame's right crop. The per-word rise hides
    unspoken words inside their own masks, so the right crop is empty.
    Fails on the block wipe; passes only on per-word drawing.
    """
    pytest.importorskip("PIL", reason="needs Pillow to measure the stills")
    entry = copy.deepcopy(SPAN)
    entry["entrance"] = "mask"
    entry["word_sync"] = True
    planned = _plan(ffe.declared_elements({"full_frame_elements": [entry]}))
    cued_props = planned[0].props
    assert cued_props["runs"][0]["text"] == "ALPHA BETA"

    early_cued = _still(cued_props, tmp_path, "mask_words_early",
                        EARLY_FRAME)
    assert _ink_count(early_cued) > 200, (
        "the cued mask frame drew no text at all - the first word never "
        "arrived")
    assert _right_crop_ink(early_cued) < 100, (
        f"the cued mask frame drew {_right_crop_ink(early_cued)}px in the "
        f"right crop at frame {EARLY_FRAME} - the unspoken word is "
        f"showing, which is the block-level wipe, not per-word masks")
