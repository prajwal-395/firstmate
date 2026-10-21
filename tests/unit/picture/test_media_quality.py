"""The technical leg: measured media quality, kept apart from taste.

These tests name the defects the leg exists to prevent: an unmeasured
indicator that reads as a zero, a partial measurement that reads as a
full one, and a technical comparison that quietly becomes an editorial
recommendation.
"""

import pytest

from library.tools import media_quality
from library.tools import source_quality


def _picture_track(samples, rate_hz=5.0):
    """A picture-quality lane record with `samples` rows at `rate_hz`."""
    return {
        "method": "picture_quality_pixels_v1",
        "sample_rate_hz": rate_hz,
        "sample_short_side_px": 180,
        "measurements": [
            {
                "time_s": round(index / rate_hz, 3),
                "focus_laplacian_variance": 400.0 + index,
                "luma_mean": 110.0 + index,
                "underexposed_pixel_fraction": 0.001,
                "overexposed_pixel_fraction": 0.002,
                "low_gradient_residual_sigma_luma": 0.006,
                "frame_difference_luma_fraction": 0.02 if index else None,
                "motion_blur_proxy": 0.004 if index else None,
            }
            for index in range(samples)
        ],
    }


def _profile(samples=10, rate_hz=5.0, start=0.0, end=None):
    end = end if end is not None else start + samples / rate_hz
    return source_quality.profile_over_span(
        picture_quality=_picture_track(samples, rate_hz),
        start_seconds=start, end_seconds=end)


# ── Absence is a fact, not a zero ───────────────────────────────────


def test_an_indicator_nobody_measured_is_absent_with_its_lane_named():
    """The leg must say WHICH lane is missing.

    A zero would be read as a measured value - a clip with no sharpness
    reading is not a clip with no sharpness.
    """
    profile = source_quality.profile_over_span(start_seconds=0.0,
                                               end_seconds=2.0)
    leg = media_quality.technical_leg(profile)

    sharpness = leg["indicators"]["sharpness"]
    assert sharpness["status"] == "absent"
    assert sharpness["measures"] == []
    assert sharpness["absent_reason"] == "no_picture_measurement_record"
    assert sharpness["confidence"]["level"] == "none"
    assert "value" not in sharpness


def test_an_unmeasured_indicator_never_reaches_the_prompt_as_a_number():
    """The rendered cell must not carry a number it did not measure."""
    profile = source_quality.profile_over_span(start_seconds=0.0,
                                               end_seconds=2.0)
    leg = media_quality.technical_leg(profile)

    cell = media_quality.render_leg_cell(leg)
    assert "sharpness=-" in cell
    assert "400" not in cell


# ── Coverage and confidence are stated ──────────────────────────────


def test_a_fully_measured_indicator_states_its_coverage_and_confidence():
    """A reader must be able to tell a full reading from a partial one."""
    leg = media_quality.technical_leg(_profile(samples=10, rate_hz=5.0))

    sharpness = leg["indicators"]["sharpness"]
    assert sharpness["status"] == "measured"
    assert sharpness["coverage"]["samples"] == 10
    assert sharpness["coverage"]["covered_seconds"] == 2.0
    assert sharpness["coverage"]["covered_fraction"] == 1.0
    assert sharpness["confidence"]["level"] == "full"
    assert sharpness["confidence"]["basis"] == "instrument_reading"


def test_a_measurement_that_covered_half_the_span_is_not_a_full_reading():
    """The half-covered case is the one a reader must not miss."""
    profile = _profile(samples=10, rate_hz=5.0, start=0.0, end=2.0)

    leg = media_quality.technical_leg(profile)
    sharpness = leg["indicators"]["sharpness"]
    assert sharpness["coverage"]["samples"] == 10
    assert sharpness["coverage"]["covered_fraction"] == 1.0
    assert sharpness["confidence"]["level"] == "full"

    # The same samples, asked about a span twice as long: the reading
    # covers half of it, and says so.
    wider = media_quality.technical_leg(
        _profile(samples=10, rate_hz=5.0, start=0.0, end=4.0))
    sharpness = wider["indicators"]["sharpness"]
    assert sharpness["coverage"]["covered_fraction"] == 0.5
    assert sharpness["confidence"]["level"] == "partial"
    assert "0.5" in sharpness["confidence"]["because"]


def test_one_sample_is_named_as_one_sample():
    """A single sample must not read as a measurement of the span."""
    leg = media_quality.technical_leg(_profile(samples=1, rate_hz=5.0))

    sharpness = leg["indicators"]["sharpness"]
    assert sharpness["coverage"]["samples"] == 1
    assert sharpness["confidence"]["level"] == "single_sample"


# ── The leg is not a score ──────────────────────────────────────────


def test_the_leg_carries_no_total_and_no_threshold():
    """A total would be a ranking wearing a measurement's clothes."""
    leg = media_quality.technical_leg(_profile())

    assert "score" not in leg
    assert "total" not in leg
    assert "rank" not in leg
    assert "recommend" not in leg
    for indicator in leg["indicators"].values():
        assert "threshold" not in indicator


def test_every_indicator_says_what_it_is_not():
    """A sharpness proxy read as a sharpness verdict is the defect."""
    leg = media_quality.technical_leg(_profile())

    for name in media_quality.INDICATORS:
        note = leg["indicators"][name]["note"]
        assert note, f"{name} carries no note"


# ── A comparison explains, and does not choose ──────────────────────


def _two_legs():
    """A sharp candidate and a soft one, both fully measured."""
    sharp = media_quality.technical_leg(_profile(samples=10, rate_hz=5.0))
    soft_track = _picture_track(10, 5.0)
    for row in soft_track["measurements"]:
        row["focus_laplacian_variance"] = 90.0
    soft = media_quality.technical_leg(source_quality.profile_over_span(
        picture_quality=soft_track, start_seconds=0.0, end_seconds=2.0))
    return sharp, soft


def test_the_comparison_states_the_numbers_of_the_difference():
    """The explanation must carry numbers, or it explains nothing."""
    sharp, soft = _two_legs()
    comparison = media_quality.compare_legs(
        sharp, soft, left_label="clip_a", right_label="clip_b")

    sharpness = next(d for d in comparison["differences"]
                      if d["indicator"] == "sharpness")
    assert sharpness["left"] > sharpness["right"]
    assert sharpness["delta"] > 0
    assert sharpness["unit"] == "variance"
    assert sharpness["left_coverage"]["samples"] == 10
    assert sharpness["right_confidence"]["level"] == "full"


def test_the_comparison_does_not_say_which_candidate_to_use():
    """A technical comparison that recommends is an editorial verdict."""
    sharp, soft = _two_legs()
    comparison = media_quality.compare_legs(sharp, soft)

    rendered = media_quality.render_comparison(comparison).lower()
    assert "recommend" not in rendered
    assert "should use" not in rendered
    assert "better shot" not in rendered
    assert comparison["does_not_conclude"] == (
        "which candidate is the better EDITORIAL choice")
    assert "editorial_selection_unchanged" in comparison


def test_the_technical_reading_never_overrides_the_editorial_choice():
    """The captain's preference stands even when the numbers disagree."""
    sharp, soft = _two_legs()
    # The editor chose the SOFT shot; the numbers prefer the sharp one.
    comparison = media_quality.compare_legs(
        sharp, soft, left_label="clip_a", right_label="clip_b",
        editorial_preference="right")

    assert comparison["editorial_preference"] == "right"
    assert comparison["editorial_selection_unchanged"] is True
    assert comparison["editorial_separation"] in (
        media_quality.render_comparison(comparison))
    # The numbers are still reported: the comparison is not silenced by
    # the preference, it is kept beside it.
    assert any(d["indicator"] == "sharpness"
               for d in comparison["differences"])


def test_a_preference_that_names_no_side_is_refused():
    """An editorial preference that names nothing cannot be recorded."""
    sharp, soft = _two_legs()
    with pytest.raises(media_quality.MediaQualityError):
        media_quality.compare_legs(sharp, soft, editorial_preference="clip_a")


def test_an_indicator_measured_on_one_side_only_is_reported_as_such():
    """A comparison of two different spans must not invent a difference."""
    sharp, soft = _two_legs()
    del soft["indicators"]["sharpness"]["measures"]
    soft["indicators"]["sharpness"]["status"] = "absent"

    comparison = media_quality.compare_legs(
        sharp, soft, left_label="clip_a", right_label="clip_b")
    assert all(d["indicator"] != "sharpness"
               for d in comparison["differences"])
    missing = next(n for n in comparison["not_comparable"]
                   if n["indicator"] == "sharpness")
    assert "clip_b" in missing["reason"]
