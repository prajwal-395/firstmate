"""The check that catches a wrong-but-stored position.

PR 927 read placements back and reported "held exactly" - fidelity of
storage, never correctness of intent. These tests pin the two halves
that would have caught Reel 09: the value half fails a clamped store
against the intent it disobeys, and the pixel half fails ink that
renders away from intent. Every test builds its fixtures under
`tmp_path` or in memory; nothing reaches Resolve or a real project.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.overlay_verify import (  # noqa: E402
    canvas_origin,
    verify_pixels,
    verify_values,
)
from library.tools.tight_box import placement_for_box  # noqa: E402

FULL = (1080, 1920)

#: What the Reel 09 caption computation asked for - and what Resolve
#: past its silent clamp actually holds on the small-canvas era.
COMPUTED = {"scaling": 1, "pan": 0.0, "tilt": -7929.0}
CLAMPED = {"scaling": 1, "pan": 0.0, "tilt": -7680.0}


#: The tight canvas the Reel 09 captions were placed on.
CANVAS = (840, 480)


def _clip(label, stored, kind="caption", segment_id="sub_x"):
    return {"label": label, "kind": kind, "segment_id": segment_id,
            "stored": stored, "canvas_wh": CANVAS}


def test_matching_store_passes_values():
    report = verify_values(
        [_clip("cap", dict(COMPUTED))], {},
        computed={("caption", "sub_x"): dict(COMPUTED)}, full_wh=(1080, 1920))
    assert report["passed"] and report["checked"] == 1


def test_clamped_store_fails_values_with_both_numbers():
    report = verify_values(
        [_clip("cap", dict(CLAMPED))], {},
        computed={("caption", "sub_x"): dict(COMPUTED)}, full_wh=(1080, 1920))
    assert not report["passed"]
    finding = report["findings"][0]
    assert finding["stored"]["tilt"] == -7680.0
    assert finding["expected"]["tilt"] == -7929.0
    assert finding["gap"]["tilt"] == -7680.0 - -7929.0


def test_identity_store_on_tight_clip_fails_values():
    report = verify_values(
        [_clip("cap", {"scaling": 0, "pan": 0.0, "tilt": 0.0})], {},
        computed={("caption", "sub_x"): dict(COMPUTED)}, full_wh=(1080, 1920))
    assert not report["passed"]


def test_declared_intent_is_the_expectation():
    # The pin names the PLACE Tilt -1700 reaches on this canvas.
    intent = {"caption": {"canvas_centre": [540.0, 1385.0], "scaling": 1}}
    computed = {("caption", "sub_x"): {"scaling": 1, "pan": 0.0,
                                       "tilt": -1744.0}}
    report = verify_values(
        [_clip("cap", {"scaling": 1, "pan": 0.0, "tilt": -1700.0})],
        intent, computed=computed, full_wh=(1080, 1920))
    assert report["passed"]
    assert report["findings"] == []


def test_computed_store_fails_against_declared_intent():
    intent = {"caption": {"canvas_centre": [540.0, 1385.0], "scaling": 1}}
    computed = {("caption", "sub_x"): {"scaling": 1, "pan": 0.0,
                                       "tilt": -1744.0}}
    report = verify_values(
        [_clip("cap", {"scaling": 1, "pan": 0.0, "tilt": -1744.0})],
        intent, computed=computed, full_wh=(1080, 1920))
    assert not report["passed"]
    assert report["findings"][0]["provenance"] == "declared"


def test_clip_with_no_expectation_is_skipped_not_passed():
    report = verify_values([_clip("mystery", dict(COMPUTED),
                                  kind="unknown", segment_id="zzz")],
                           {}, computed={}, full_wh=(1080, 1920))
    assert report["passed"] is True
    assert report["checked"] == 0
    assert len(report["skipped"]) == 1


def test_origin_inverts_placement():
    for canvas_wh, centre in (((840, 480), (540.0, 1396.0)),
                              ((296, 480), (148.0, 960.0)),
                              ((442, 480), (859.0, 960.4))):
        placement = placement_for_box(canvas_wh[0], canvas_wh[1],
                                      centre[0], centre[1],
                                      FULL[0], FULL[1])
        ox, oy = canvas_origin(placement, canvas_wh, FULL)
        assert (ox, oy) == (round(centre[0] - canvas_wh[0] / 2.0),
                            round(centre[1] - canvas_wh[1] / 2.0))


def _asset_frame(path, canvas_wh=(200, 120), ink=(40, 30, 160, 90)):
    from PIL import Image

    image = Image.new("RGBA", canvas_wh, (0, 0, 0, 0))
    pixels = image.load()
    for y in range(ink[1], ink[3]):
        for x in range(ink[0], ink[2]):
            pixels[x, y] = (255, 255, 255, 255)
    image.save(path)


def test_matching_store_passes_pixels(tmp_path):
    frame = str(tmp_path / "asset.png")
    _asset_frame(frame)
    placement = placement_for_box(200, 120, 540.0, 960.0, *FULL)
    clip = _clip("cap", dict(placement))
    clip.update({"asset_frame": frame, "canvas_wh": (200, 120)})
    key = ("caption", "sub_x")
    report = verify_pixels([clip], {}, computed={key: dict(placement)}, full_wh=(1080, 1920))
    assert report["passed"] and report["checked"] == 1


def test_shifted_store_fails_pixels_with_gap(tmp_path):
    frame = str(tmp_path / "asset.png")
    _asset_frame(frame)
    expected = placement_for_box(200, 120, 540.0, 960.0, *FULL)
    stored = placement_for_box(200, 120, 540.0, 971.0, *FULL)
    clip = _clip("cap", dict(stored))
    clip.update({"asset_frame": frame, "canvas_wh": (200, 120)})
    key = ("caption", "sub_x")
    report = verify_pixels([clip], {}, computed={key: dict(expected)}, full_wh=(1080, 1920))
    assert not report["passed"]
    assert report["findings"][0]["gap_px"] == 11.0


def test_blank_asset_frame_is_skipped(tmp_path):
    from PIL import Image

    frame = str(tmp_path / "blank.png")
    Image.new("RGBA", (200, 120), (0, 0, 0, 0)).save(frame)
    placement = placement_for_box(200, 120, 540.0, 960.0, *FULL)
    clip = _clip("cap", dict(placement))
    clip.update({"asset_frame": frame, "canvas_wh": (200, 120)})
    key = ("caption", "sub_x")
    report = verify_pixels([clip], {}, computed={key: dict(placement)}, full_wh=(1080, 1920))
    assert report["checked"] == 0
    assert len(report["skipped"]) == 1
