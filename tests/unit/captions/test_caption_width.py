"""`caption-width`: only the captions that run under a platform's UI move.

The captain, 2026-09-25: the longest caption lines on his accepted reels
ran under the apps' action rails. Narrowing re-renders exactly the cards
whose words wrap wider than the clear width, swaps them in place, and
leaves every card that already fits alone.
"""

import json

import pytest

from library.tools import caption_width as cw
from library.tools import reel_touchup as touchup
from library.tools.tight_box import placement_for_box

FRAME = (1080, 1920)


def _card(tmp_path, name, text, bottom):
    """A 904x480 tight caption card whose card bottom sits at ``bottom``."""
    words = [{"word": w, "startFrame": 0, "endFrame": 10}
             for w in text.split()]
    props = {
        "subtitles": [{"text": text, "startFrame": 0, "endFrame": 10,
                       "emphasisWords": [], "words": words}],
        "fps": 24.0, "width": 904, "height": 480, "durationInFrames": 12,
        "style": {"fontFamily": "Montserrat", "fontSize": 58,
                  "fontWeight": 800, "outlineWidth": 4, "position": "bottom",
                  "captionMaxWidth": 840,
                  "safeArea": {"top": 444, "right": 24, "bottom": 36,
                               "left": 24}},
        "_source_in_frame": 0, "_source_out_frame": 12,
    }
    mov = tmp_path / f"{name}.mov"
    mov.write_bytes(b"")
    (tmp_path / f"{name}_props.json").write_text(json.dumps(props))
    # Place the canvas so its card bottom (canvas bottom less the 36px
    # pad) lands on `bottom`.
    centre_y = bottom + 36 - 240
    placement = placement_for_box(904, 480, 540, centre_y, *FRAME, 1.0)
    return {"source_file": str(mov), "record_in": 0, "duration": 12,
            "transform": {"Pan": placement["pan"],
                          "Tilt": placement["tilt"]}}


def _tracks(clips):
    return [{"type": "video", "index": 4, "name": "Subtitles",
             "clips": clips}]


def test_only_the_cards_wider_than_the_clear_width_are_swapped(tmp_path):
    wide = _card(tmp_path, "wide",
                 "Everybody wants the visibility nobody measures", 1560)
    narrow = _card(tmp_path, "narrow", "yes.", 1560)
    rendered = []

    def render(folder, pairs, gain):
        rendered.extend(pairs)
        return {p["old_mov"]: p["old_mov"][:-4] + "_narrow.mov"
                for p in pairs}

    spec = cw.touch_spec(str(tmp_path), 1, _tracks([wide, narrow]),
                         timeline_label="Reel 01", draw_gain=1.0,
                         render=render)
    # The band the cards draw on is clear of every zone only inside the
    # platforms' rails, so the width is well under the 840 they wrapped at.
    assert spec["max_width"] < 700
    assert [p["old_mov"] for p in rendered] == [wide["source_file"]]
    style = rendered[0]["props"]["style"]
    assert style["captionMaxWidth"] == spec["max_width"]
    # Rendered through the caption step from FULL-frame props.
    assert (rendered[0]["props"]["width"],
            rendered[0]["props"]["height"]) == FRAME
    assert spec["edits"] == [{"op": "swap_pixels", "row": "V4", "item": 0,
                              "media": wide["source_file"][:-4]
                              + "_narrow.mov"}]


def test_captions_inside_a_zone_are_refused_by_name(tmp_path):
    # A card bottom at 1600 sits in TikTok's caption block on a phone
    # with a thin home bar: no centred width clears that, and the answer
    # is to move the row, not to find a width.
    low = _card(tmp_path, "low", "a caption sitting too low", 1600)
    with pytest.raises(cw.CaptionWidthError, match="tiktok text"):
        cw.touch_spec(str(tmp_path), 1, _tracks([low]),
                      timeline_label="Reel 01", draw_gain=1.0,
                      render=lambda *a: pytest.fail("rendered"))


def test_caption_width_swap_preserves_the_placed_caption_source_trim(
        tmp_path):
    card = _card(tmp_path, "wide", "Everybody wants the visibility nobody measures",
                 1560)
    card.update({"record_out": 12, "left_offset": 12,
                 "right_offset": 100})
    generated = cw.touch_spec(
        str(tmp_path), 1, _tracks([card]), timeline_label="Reel 01",
        draw_gain=1.0,
        render=lambda _folder, pairs, _gain: {
            pair["old_mov"]: pair["old_mov"][:-4] + "_narrow.mov"
            for pair in pairs})

    qualification = touchup.qualify(_tracks([card]), generated)

    assert qualification.insertions[0].left_offset == 12
