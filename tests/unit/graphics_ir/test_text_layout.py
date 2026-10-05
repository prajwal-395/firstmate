"""Text layout and faces in the IR lowering - pinned by the FFrames review.

Each test names the defect a side-by-side against HyperFrames showed on
PR 1805: captions on two lines in the wrong word order, every face drawn
at the variable font's Thin default, FullFrameCard runs at the wrong
size, and words drifting 1-2px off the browser's because kerning was
not measured.
"""

import pytest

from library.graphics_ir.fonts import advances, static_instance
from library.graphics_ir.layout import measure_text
from library.graphics_ir.lowering import lower_full_frame_card, lower_subtitle_overlay
from library.tools.hyperframes_render import bundled_font_file

BUNDLED = bundled_font_file()
needs_font = pytest.mark.skipif(not BUNDLED.is_file(), reason="bundled Montserrat not in this checkout")


def _caption(words, max_width=760):
    timed = [{"word": w, "startFrame": i * 5, "endFrame": i * 5 + 5} for i, w in enumerate(words)]
    return {
        "fps": 30, "width": 1080, "height": 1920, "durationInFrames": 60,
        "fontFile": str(BUNDLED),
        "style": {"fontFamily": "Montserrat", "fontSize": 64, "fontWeight": 800,
                  "position": "bottom", "captionMaxWidth": max_width,
                  "safeArea": {"top": 0, "right": 0, "bottom": 300, "left": 0}},
        "subtitles": [{"startFrame": 0, "endFrame": 60, "text": " ".join(words), "words": timed}],
    }


@needs_font
def test_caption_words_flow_in_reading_order_inside_the_max_width():
    words = "the quick brown fox jumps over the lazy dog".split()
    scene = lower_subtitle_overlay(_caption(words))
    boxes = [n.bounds for n in scene.nodes if n.id.startswith("subtitle_0_word_")]
    assert len(boxes) == len(words)
    rows = sorted({round(b.y + b.height / 2) for b in boxes})
    assert len(rows) > 1, "nine words at 64px must wrap inside 760px"
    for prev, cur in zip(boxes, boxes[1:]):
        same_row = abs((prev.y + prev.height / 2) - (cur.y + cur.height / 2)) < 1
        if same_row:
            assert cur.x > prev.x + prev.width, "a word must sit right of the one before it"
        else:
            assert cur.y > prev.y, "a wrapped word must sit on a lower row"
    for row in rows:
        on_row = [b for b in boxes if round(b.y + b.height / 2) == row]
        left, right = on_row[0].x, on_row[-1].x + on_row[-1].width
        assert right - left <= 760
        # Rows centre on the card (the trailing 0.24em margin included).
        assert abs((left + right) / 2 - 540) < 0.24 * 64


@needs_font
def test_caption_box_bottom_sits_on_the_safe_area():
    scene = lower_subtitle_overlay(_caption(["hello", "world"]))
    card = next(n for n in scene.nodes if n.id == "subtitle_0")
    assert card.bounds.y + card.bounds.height == pytest.approx(1920 - 300)


@needs_font
def test_static_instance_is_pinned_at_the_declared_weight():
    from io import BytesIO

    from fontTools.ttLib import TTFont

    heavy, heavy_family = static_instance(str(BUNDLED), 800)
    thin, thin_family = static_instance(str(BUNDLED), 100)
    face = TTFont(BytesIO(heavy))
    assert "fvar" not in face
    assert face["OS/2"].usWeightClass == 800
    assert heavy_family != thin_family
    assert measure_text("weight", 64, str(BUNDLED), 800) > measure_text("weight", 64, str(BUNDLED), 100)


@needs_font
def test_measurement_applies_pair_kerning():
    faces = advances(str(BUNDLED), 800)
    pair = faces.width("AV", 100)
    assert pair < faces.width("A", 100) + faces.width("V", 100)


def test_full_frame_card_run_draws_at_its_declared_size_and_role_weight():
    props = {
        "fps": 30, "width": 1080, "height": 1920, "durationInFrames": 30,
        "background": "#000000", "entrance": "cut", "exit": "cut",
        "safeArea": {"top": 0, "right": 0, "bottom": 0, "left": 0},
        "runs": [{"text": "Hi", "type_role": "micro", "colour": "#FFFFFF", "font_size": 80}],
    }
    run = next(n for n in lower_full_frame_card(props).nodes if n.id == "run_0")
    assert run.text.font_size == 80
    assert run.text.font_weight == 600
