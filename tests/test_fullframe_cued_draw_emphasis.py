"""A second word-paced character, and current-word emphasis in colour.

The ceiling report (lane `vep-animation-ceiling-push`) named both as the
highest-payoff unbuilt work: per-word blur-resolve existed only as a
block-level drawing (`draw` driven by the card's overall word progress),
never as its own per-word choreography - and the schema carried no
declared emphasis colour, so emphasis could only have been motion.

The choice of WHICH segment gets WHICH character is declared by the
model, per segment: a span segment may name its own `entrance` (one of
the word-cued characters when `word_sync` is on), falling back to the
span's. There is deliberately no structural rule in the engine - no
parity alternation, no cadence - because that would be the engine
holding taste (AGENTS.md 10.5). The emphasis colour is a declared
`emphasis_colour` on the span; undeclared means no emphasis, never a
fallback colour.

Every gate here is proved in BOTH directions, and the drawings are
proved by RENDERING, not by asserting a node exists: the still tests
below fail on a composition that ignores the prop, because a source
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

ORANGE = "#FFAA4D"

SPAN = {
    "element": "full_frame_span",
    "placement": "span",
    "background": "#101014",
    "font_family": "Montserrat",
    "entrance": "mask",
    "segments": [
        {"runs": [
            {"bind": "range_line",
             "type_role": "display", "colour": "#FFFFFF"}]},
    ],
}

RANGES = [(10.0, 11.0)]
RANGES2 = [(10.0, 11.0), (11.5, 12.5)]


def declare(**overrides):
    entry = copy.deepcopy(SPAN)
    entry.update(overrides)
    return {"full_frame_elements": [entry]}


def _transcript(words):
    timed = [{"word": w, "start": s, "end": e, "timed": True}
             for w, s, e in words]
    return {"segments": [
        {"speaker": "Akshita", "timeline_start": 10.0, "timeline_end": 12.5,
         "text": " ".join(w for w, _, _ in words), "words": timed},
    ]}


def _two_word_transcript():
    return _transcript([("ALPHA", 10.0, 10.1), ("BETA", 10.8, 10.9)])


def _four_word_transcript():
    return _transcript([("ALPHA", 10.0, 10.1), ("BETA", 10.8, 10.9),
                        ("GAMMA", 11.6, 11.7), ("DELTA", 12.3, 12.4)])


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


def _two_segment_declarations(**span_overrides):
    entry = copy.deepcopy(SPAN)
    entry["segments"] = [
        {"runs": [{"bind": "range_line",
                   "type_role": "display", "colour": "#FFFFFF"}]},
        {"runs": [{"bind": "range_line",
                   "type_role": "display", "colour": "#FFFFFF"}]},
    ]
    entry.update(span_overrides)
    return ffe.declared_elements({"full_frame_elements": [entry]})


def _plan2(declarations):
    transcript = _four_word_transcript()
    body = sum(int(round(e * FPS)) - int(round(s * FPS))
               for s, e in RANGES2)
    return ffe.plan_reel_cards(
        declarations, _facts(RANGES2, transcript), body, FPS,
        width=WIDTH, height=HEIGHT,
        ranges=list(RANGES2), transcript=transcript)


# ── Per-segment entrance: the model declares, the engine carries ────


def test_a_segment_without_entrance_inherits_the_span():
    planned = _plan()
    assert planned[0].props["entrance"] == "mask"


def test_a_segment_may_declare_its_own_character():
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["segments"] = [{"runs": [
        {"bind": "range_line",
         "type_role": "display", "colour": "#FFFFFF"}],
        "entrance": "draw"}]
    planned = _plan(ffe.declared_elements(
        {"full_frame_elements": [entry]}))
    assert planned[0].props["entrance"] == "draw"
    assert "wordCues" in planned[0].props


def test_segments_may_differ_and_each_reaches_its_props():
    declarations = _two_segment_declarations(word_sync=True)
    declarations[0]["segments"][1]["entrance"] = "draw"
    # Re-normalise through the declaration gate: a hand-edited dict is
    # not a declaration until it has passed it.
    declarations = ffe.declared_elements(
        {"full_frame_elements": [declarations[0]]})
    planned = _plan2(declarations)
    assert [c.props["entrance"] for c in planned] == ["mask", "draw"]
    assert all("wordCues" in c.props for c in planned)


def test_a_segment_entrance_beside_word_sync_must_be_cueable():
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["segments"] = [{"runs": [
        {"bind": "range_line",
         "type_role": "display", "colour": "#FFFFFF"}],
        "entrance": "slide"}]
    with pytest.raises(ffe.FullFrameDeclarationError, match="can pace"):
        ffe.declared_elements({"full_frame_elements": [entry]})


def test_an_unknown_segment_entrance_is_refused_by_name():
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["segments"] = [{"runs": [
        {"bind": "range_line",
         "type_role": "display", "colour": "#FFFFFF"}],
        "entrance": "spin"}]
    with pytest.raises(ffe.FullFrameDeclarationError, match="spin"):
        ffe.declared_elements({"full_frame_elements": [entry]})


def test_a_non_cued_character_is_fine_without_word_sync():
    entry = copy.deepcopy(SPAN)
    entry["segments"] = [{"runs": [
        {"bind": "range_line",
         "type_role": "display", "colour": "#FFFFFF"}],
        "entrance": "slide"}]
    assert (ffe.declared_elements({"full_frame_elements": [entry]})
            [0]["segments"][0]["entrance"] == "slide")


def test_a_non_string_segment_entrance_is_refused():
    entry = copy.deepcopy(SPAN)
    entry["segments"] = [{"runs": [
        {"bind": "range_line",
         "type_role": "display", "colour": "#FFFFFF"}],
        "entrance": 3}]
    with pytest.raises(ffe.FullFrameDeclarationError, match="entrance"):
        ffe.declared_elements({"full_frame_elements": [entry]})


# ── Emphasis colour: declared, or absent ─────────────────────────────


def test_no_emphasis_colour_means_no_emphasis_key():
    planned = _plan()
    assert "emphasisColour" not in planned[0].props


def test_a_declared_emphasis_colour_reaches_every_segment():
    planned = _plan2(
        _two_segment_declarations(word_sync=True,
                                  emphasis_colour=ORANGE))
    assert [c.props["emphasisColour"] for c in planned] == [ORANGE, ORANGE]


def test_a_non_string_emphasis_colour_is_refused_by_name():
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="emphasis_colour"):
        ffe.declared_elements(declare(word_sync=True,
                                      emphasis_colour=123))


def test_an_empty_emphasis_colour_is_refused_by_name():
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="emphasis_colour"):
        ffe.declared_elements(declare(word_sync=True,
                                      emphasis_colour="  "))


def test_emphasis_without_a_word_clock_is_refused():
    """No clock, no current word: the field would have no reader, and a
    declared output with no reader is refused (AGENTS.md 10.1)."""
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="emphasis_colour"):
        ffe.declared_elements(declare(emphasis_colour=ORANGE))


def test_emphasis_on_a_card_is_refused():
    """A card covers its own seconds, not speech seconds - the same
    reading that refuses `word_sync` there."""
    card = {
        "element": "full_frame_card",
        "placement": "head",
        "duration_seconds": 2.0,
        "background": "#000000",
        "font_family": "Montserrat",
        "emphasis_colour": ORANGE,
        "runs": [{"text": "hello", "type_role": "display",
                  "colour": "#FFF"}],
    }
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="emphasis_colour"):
        ffe.declared_elements({"full_frame_elements": [card]})


def test_emphasis_beside_a_typewriter_segment_is_refused():
    """`typewriter` is a reveal, not a per-word drawing: there is no
    current-word glyph to restyle, so the field would have no reader on
    that segment."""
    entry = copy.deepcopy(SPAN)
    entry["word_sync"] = True
    entry["entrance"] = "typewriter"
    entry["emphasis_colour"] = ORANGE
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="emphasis_colour"):
        ffe.declared_elements({"full_frame_elements": [entry]})


# ── The renders prove the drawings ───────────────────────────────────

# Word 1 at 0.0-0.1s, word 2 at 0.8-0.9s of a 1.0s segment @30fps.
# Frame 20 (t=0.667s) is before the second word starts; frame 26
# (t=0.867s) is inside the second word's own window.
EARLY_FRAME = 20
MID_WORD_FRAME = 26
GROUND_RGB = (0x10, 0x10, 0x14)


def _ink_count(image, ground=GROUND_RGB, tolerance=30):
    pixels = image.load()
    width, height = image.size
    return sum(
        1 for y in range(height) for x in range(width)
        if any(abs(c - t) > tolerance
               for c, t in zip(pixels[x, y][:3], ground)))


def _colour_count(image, rgb, tolerance=60):
    pixels = image.load()
    width, height = image.size
    return sum(
        1 for y in range(height) for x in range(width)
        if all(abs(c - t) <= tolerance
               for c, t in zip(pixels[x, y][:3], rgb)))


def _right_crop_ink(image, fraction=0.6, ground=GROUND_RGB, tolerance=30):
    """Ink in the right-hand crop, where the SECOND word lives."""
    pixels = image.load()
    width, height = image.size
    return sum(
        1 for y in range(height) for x in range(int(width * fraction), width)
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


def _draw_props(tmp_path_extra=None):
    entry = copy.deepcopy(SPAN)
    entry["entrance"] = "draw"
    entry["word_sync"] = True
    planned = _plan(ffe.declared_elements(
        {"full_frame_elements": [entry]}))
    assert planned[0].props["runs"][0]["text"] == "ALPHA BETA"
    return planned[0].props


@renders_available
def test_draw_resolves_words_not_blocks(tmp_path):
    """A cued draw resolves each word out of its own blur - pixels prove it.

    At frame 20 (t=0.667s) "ALPHA" (cued 0.0-0.1s) has settled and "BETA"
    (cued 0.8-0.9s) is unspoken. The old block-level drawing drove one
    blur off the card's overall progress - which at two settled thirds
    shows the whole line half-resolved, including the left halves of
    BETA's glyphs in the frame's right crop. The per-word choreography
    keeps unspoken words at zero opacity inside their own blur, so the
    right crop is empty. Fails on the block drawing; passes only on
    per-word drawing.
    """
    pytest.importorskip("PIL", reason="needs Pillow to measure the stills")
    cued_props = _draw_props()
    early = _still(cued_props, tmp_path, "draw_words_early", EARLY_FRAME)
    assert _ink_count(early) > 200, (
        "the cued draw frame drew no text at all - the first word never "
        "arrived")
    assert _right_crop_ink(early) < 100, (
        f"the cued draw frame drew {_right_crop_ink(early)}px in the "
        f"right crop at frame {EARLY_FRAME} - the unspoken word is "
        f"showing, which is the block-level drawing, not per-word "
        f"choreography")


@renders_available
def test_emphasis_lands_on_the_current_word(tmp_path):
    """At frame 26 BETA is the current word: it draws in the declared
    emphasis colour while ALPHA stays in the run's colour. Fails on a
    composition that ignores `emphasisColour` - without the restyle node
    both stills carry no emphasis-ink and the ratio below collapses.
    """
    pytest.importorskip("PIL", reason="needs Pillow to measure the stills")
    entry = copy.deepcopy(SPAN)
    entry["entrance"] = "mask"
    entry["word_sync"] = True
    entry["emphasis_colour"] = ORANGE
    planned = _plan(ffe.declared_elements(
        {"full_frame_elements": [entry]}))
    cued_props = planned[0].props
    assert cued_props["runs"][0]["text"] == "ALPHA BETA"

    plain_props = dict(cued_props)
    del plain_props["emphasisColour"]

    orange = (0xFF, 0xAA, 0x4D)
    emph = _still(cued_props, tmp_path, "emph_mid", MID_WORD_FRAME)
    plain = _still(plain_props, tmp_path, "plain_mid", MID_WORD_FRAME)
    emph_orange = _colour_count(emph, orange)
    plain_orange = _colour_count(plain, orange)
    assert emph_orange > 200, (
        f"the emphasised frame drew only {emph_orange}px in the "
        f"emphasis colour at frame {MID_WORD_FRAME} - the current word "
        f"never took it")
    assert emph_orange > 3 * plain_orange, (
        f"the emphasised frame drew {emph_orange}px in the emphasis "
        f"colour against {plain_orange}px without the field - emphasis "
        f"did not land")


@renders_available
def test_no_emphasis_between_words(tmp_path):
    """At frame 20 no word owns the clock (ALPHA ended 0.1s, BETA starts
    0.8s): nothing draws in the emphasis colour even when it is
    declared. Emphasis marks the CURRENT word, not the last one."""
    pytest.importorskip("PIL", reason="needs Pillow to measure the stills")
    entry = copy.deepcopy(SPAN)
    entry["entrance"] = "mask"
    entry["word_sync"] = True
    entry["emphasis_colour"] = ORANGE
    planned = _plan(ffe.declared_elements(
        {"full_frame_elements": [entry]}))
    still = _still(planned[0].props, tmp_path, "emph_gap", EARLY_FRAME)
    assert _colour_count(still, (0xFF, 0xAA, 0x4D)) < 100, (
        "the gap frame drew emphasis ink with no word on the clock - "
        "emphasis must mark the current word only")
