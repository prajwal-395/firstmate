"""The native renderer must not ship a card it cannot draw as declared.

The defect this names, measured on the geo-podcast project's own 86
shipped motion-graphics cards (`tests/fixtures/native_render_parity/`):
the FFrames gate answered "FFrames can draw" for all ten lower thirds,
and the native renderer drew a card without the brand asset two of them
declared - and, for every lower third, without the construction the
plan declared. 26 of the 86 cards would have shipped a different
picture, and nothing said so.

The gate now fails closed, in two halves:

  * an element that declares an asset and lowers to no image node is
    REFUSED by name - the IR refuses it, so the omission cannot reach
    a render;
  * an arm the IR does not draw as the plan declared is not claimed at
    all, and the card goes to the renderer that does draw it.

The props in the fixture are the project's own cards, copied verbatim:
this is the population the defect was measured on, not a synthetic one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import library.steps.step_4_06_render_motion_graphics.post_bridge as pb
from library.graphics_ir import lowering

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "native_render_parity"


def _props(name: str) -> dict:
    return json.loads((FIXTURE / "props" / f"{name}.json").read_text(
        encoding="utf-8"))


def _cards() -> list[dict]:
    return json.loads((FIXTURE / "cards.json").read_text(encoding="utf-8"))[
        "cards"]


def _lower_third_cards() -> list[dict]:
    return [c for c in _cards() if "lower_third" in c["arms"]]


def test_a_declared_asset_with_no_image_node_is_refused_by_name():
    """The IR refuses the omission instead of shipping the card without it.

    The plan declared the brand asset; the lowering drew no image node
    for it.  That card would ship WITHOUT the asset the plan declared,
    and the omission is silent in the picture - so the lowering refuses
    it and names the asset.
    """
    props = _props("mg_geo-podcast_56e3dc30_tight")

    with pytest.raises(lowering.AssetNotDrawn) as refusal:
        lowering.lower_motion_graphics(props)

    assert "Lucie_Logo_Icon_2023.svg" in str(refusal.value)
    assert "lower_third" in str(refusal.value)


def test_every_asset_bearing_card_of_the_project_falls_back_to_remotion():
    """The gate sends every card whose asset the IR cannot draw to Remotion.

    The population declares an asset on three cards. Two of them are
    lower thirds, whose asset the IR does not draw at all; the third is
    a channel bug, whose asset the IR draws, and that card is still
    claimed by the native renderer.
    """
    declared = [c for c in _cards() if c["assets"]]
    assert declared, "the population declares a brand asset somewhere"

    for card in declared:
        if card["asset_drawn"]:
            continue
        props = _props(card["card"])
        reason = pb._fframes_fallback_reason(props, "")
        assert reason is not None, (
            f"{card['card']} declares {card['assets']} and the gate "
            f"claimed the native renderer can draw it")
        assert "Remotion" in reason


def test_the_lower_third_arm_is_not_claimed_until_the_ir_draws_it():
    """The IR lowers a lower third to plain text; the plan declares more.

    The composition draws one of three things for that arm - the staged
    rule construction, the AEP comp25 construction, or the attribution
    block - and the IR draws plain text runs for all three.  So the arm
    is not claimed by the native renderer, and the card goes to the
    renderer that draws what the plan declared.
    """
    props = _props("mg_geo-podcast_3107ff3d_tight")
    assert not props["elements"][0].get("asset"), (
        "this card declares no asset: the refusal must be the arm's")

    reason = pb._fframes_fallback_reason(props, "")

    assert reason is not None
    assert "lower_third" in reason
    assert "Remotion" in reason


def test_a_card_whose_asset_is_drawn_is_still_claimed(tmp_path):
    """The refusal is the omission's, not the arm's: a drawn asset ships.

    A `channel_bug` whose asset the IR CAN draw is claimed by the
    native renderer, exactly as before - the gate must not refuse
    everything that names a file.
    """
    from PIL import Image

    bug = tmp_path / "bug.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(bug)
    props = {
        "fps": 30, "width": 1080, "height": 1920, "durationInFrames": 12,
        "safeArea": {"top": 120, "right": 120, "bottom": 320, "left": 90},
        "elements": [{
            "element": "channel_bug", "anchor": "top_right", "row": 0,
            "runs": [], "color": "#FFFFFF", "entrance": "cut", "exit": "cut",
            "startFrame": 0, "durationFrames": 12, "footprint": 0.15,
            "asset": str(bug),
        }],
    }

    assert pb._fframes_fallback_reason(props, "") is None


def test_the_recorded_population_pins_the_parity_floor():
    """The measured population, and the fact that nothing regressed.

    The record is the measurement: 86 cards, the count within ink IoU
    0.90 before and after, and the lower thirds.  A card that regressed
    would break the floor this lane exists to hold.
    """
    fixture = json.loads((FIXTURE / "cards.json").read_text(encoding="utf-8"))
    counts = fixture["counts"]
    cards = fixture["cards"]

    assert counts["cards"] == len(cards) == 86
    assert counts["regressed"] == 0
    assert counts["within_0.90_after"] >= counts["within_0.90_before"]
    assert counts["within_0.90_after"] == sum(
        1 for c in cards if c["ink_iou"] >= 0.90)
    assert counts["within_0.90_before"] == sum(
        1 for c in cards if c["ink_iou_before"] >= 0.90)
    # Every card whose declared asset the IR cannot draw is drawn by
    # Remotion, and every lower third is: the native renderer draws
    # neither.
    for card in cards:
        if card["assets"] and not card["asset_drawn"]:
            assert card["engine"] == "remotion", card["card"]
        if "lower_third" in card["arms"]:
            assert card["engine"] == "remotion", card["card"]
    # The lower thirds that declare a brand asset keep it.
    with_asset = [c for c in cards
                  if c["assets"] and "lower_third" in c["arms"]]
    assert with_asset
    assert counts["lower_thirds_keeping_their_brand_asset_after"] == len(
        with_asset)


def test_the_lower_thirds_of_the_population_are_the_ones_that_moved():
    """The fix moved the lower thirds, and moved them the right way.

    Before the fix every lower third was drawn by the native renderer
    and every one of them differed from the artefact the project
    shipped; after it they are drawn by Remotion and they match.
    """
    cards = _cards()
    lower = [c for c in cards if "lower_third" in c["arms"]]
    assert len(lower) == 10

    for card in lower:
        assert card["engine"] == "remotion"
        assert card["ink_iou"] > card["ink_iou_before"], card["card"]
    assert sum(1 for c in lower if c["ink_iou"] >= 0.90) == 8
