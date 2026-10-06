"""The safe-frame check judges a reframe against measured platform UI.

Covers library/tools/safe_reframe.py, on the platforms' real measured
zones (read live, never hardcoded): a subject span touching a
full-height zone fails by name, the vertical bands stay reported as
unverifiable, and a missing subject box reads as unchecked.
"""
from __future__ import annotations

import pytest

from library.tools.platform_safe_zones import (
    PLATFORMS,
    REFERENCE_SIZE,
)
from library.tools.reframe import (
    CropWindow,
    crop_window_size,
)
from library.tools.safe_reframe import (
    check_subject_clear,
    delivered_span,
    full_height_zones,
    full_width_zones,
    platform_zones,
)

SOURCE_W, SOURCE_H = 1920.0, 1080.0


def _window():
    win_w, win_h = crop_window_size(SOURCE_W, SOURCE_H, 1080, 1920)
    return CropWindow(x=(SOURCE_W - win_w) / 2.0, y=0.0, w=win_w, h=win_h)


def _center_x_for(window, delivered_x, face_width=0.05):
    return (delivered_x * window.w / REFERENCE_SIZE[0]
            + window.x) / SOURCE_W


def test_a_face_under_a_full_height_zone_fails_by_name():
    """The defect: a reframe that tracked the subject yet parked it
    behind the app's own chrome. Placing the face span inside a real
    full-height zone fails, naming the zone."""
    zones = [z for z in full_height_zones(platform_zones("tiktok"))
             if z.rect[2] - z.rect[0] > 20]
    assert zones, "tiktok carries no full-height zone to test against"
    zone = zones[0]
    window = _window()
    target_x = (zone.rect[0] + zone.rect[2]) / 2.0
    verdict = check_subject_clear(
        window, _center_x_for(window, target_x), 0.05, SOURCE_W, "tiktok")
    assert verdict.checked is True
    assert verdict.clear is False
    assert [i.zone for i in verdict.intrusions] == [zone.name]
    assert verdict.intrusions[0].overlap_px > 0


def test_a_centred_face_clears_the_rails():
    """A subject the crop holds at delivered centre is clear of every
    full-height zone - the reframe's normal case passes."""
    window = _window()
    verdict = check_subject_clear(window, 0.5, 0.05, SOURCE_W, "tiktok")
    assert verdict.checked is True
    assert verdict.clear is True
    assert verdict.intrusions == ()


def test_the_vertical_bands_stay_unverifiable_not_passed():
    """The subject track carries no vertical term, so full-width bands
    are named as unverifiable wherever they exist - never passed,
    never failed, and never judged by an invented face height. The
    rationale travels on every verdict, even where a platform's
    capture measured no full-width band."""
    window = _window()
    for platform in sorted(PLATFORMS):
        verdict = check_subject_clear(window, 0.5, 0.05, SOURCE_W,
                                      platform)
        assert "horizontal only" in verdict.vertical
        for zone in full_width_zones(platform_zones(platform)):
            assert zone.name in verdict.vertical


def test_no_subject_box_reads_as_unchecked_not_clear():
    """Missing footage support is not a passing crop: unchecked reads
    as unchecked, with clear held at None."""
    window = _window()
    verdict = check_subject_clear(window, None, None, SOURCE_W, "tiktok")
    assert verdict.checked is False
    assert verdict.clear is None
    assert verdict.intrusions == ()


def test_an_unknown_platform_raises_naming_the_known():
    """Checked against the wrong app's chrome is how a subject ships
    behind UI nobody looked for - there is no default app."""
    window = _window()
    with pytest.raises(ValueError) as exc:
        check_subject_clear(window, 0.5, 0.05, SOURCE_W, "snapchat")
    for known in sorted(PLATFORMS):
        assert known in str(exc.value)


def test_a_non_positive_source_width_raises():
    """A mapping with no source to map from is refused, never divided
    through."""
    window = _window()
    with pytest.raises(ValueError):
        check_subject_clear(window, 0.5, 0.05, 0.0, "tiktok")


def test_delivered_span_maps_source_fractions_onto_the_reference():
    """The window maps source fractions onto the 1080-wide reference
    frame the zones are measured on, unclipped."""
    window = _window()
    left, right = delivered_span(window, 0.5, 0.1, SOURCE_W)
    assert left < REFERENCE_SIZE[0] / 2.0 < right
    assert right - left == pytest.approx(0.1 * SOURCE_W * (
        REFERENCE_SIZE[0] / window.w))
