"""Camera matching: the measured proposal that brings two angles together.

Step 5.01 had no number for a difference in CAST, so when two angles
covered one set under different white balance nothing grounded the CDL
that fixes it. `library/tools/camera_match.py` derives one slope triple
per camera off the shared neutral - reference over measured, per
channel - as a stated proposal the colourist accepts or overrides,
never an engine default.

Each test names what it stops: a default wearing a measurement's name,
a match off content instead of neutral, a silent no-op.
"""
import pytest

from library.tools import camera_match as cm


def _row(clip, camera, neutral, rb, gb, source=""):
    return {
        "clip_id": clip,
        "camera": camera,
        "source_file": source or f"/footage/{camera}.MXF",
        "neutral_rgb": list(neutral),
        "neutral_rb": rb,
        "neutral_gb": gb,
        "neutral_fraction": 0.5,
    }


def test_reference_is_the_least_cast_angle():
    """The match lands on the angle nearest true neutral - camA at
    R/B 1.003, not the first row and not the brightest."""
    rows = [_row("b1", "camB", (44.25, 45.52, 46.42), 0.9532, 0.9806),
            _row("a1", "camA", (53.36, 52.53, 53.18), 1.0033, 0.9877)]
    out = cm.derive_camera_match(rows)
    assert out["reference"]["camera"] == "camA"
    assert len(out["matches"]) == 1
    assert out["matches"][0]["camera"] == "camB"


def test_slope_lands_the_neutral_on_the_reference():
    """The geo-podcast correction, exactly: reference over measured per
    channel, and the predicted after IS the reference balance."""
    rows = [_row("a1", "camA", (53.36, 52.53, 53.18), 1.0033, 0.9877),
            _row("b1", "camB", (44.25, 45.52, 46.42), 0.9532, 0.9806)]
    match = cm.derive_camera_match(rows)["matches"][0]
    assert match["slope"] == [
        pytest.approx(round(53.36 / 44.25, 4)),
        pytest.approx(round(52.53 / 45.52, 4)),
        pytest.approx(round(53.18 / 46.42, 4))]
    assert match["neutral_before"]["rb"] == pytest.approx(0.9532)
    assert match["neutral_after"]["rb"] == pytest.approx(1.0033, abs=1e-3)
    assert match["neutral_after"]["gb"] == pytest.approx(0.9877, abs=1e-3)
    assert match["already_matched"] is False


def test_level_half_is_decomposed_in_stops():
    """The slope carries exposure too; how much of it is level is
    stated beside it rather than left inside the triple."""
    rows = [_row("a1", "camA", (53.36, 52.53, 53.18), 1.0033, 0.9877),
            _row("b1", "camB", (44.25, 45.52, 46.42), 0.9532, 0.9806)]
    match = cm.derive_camera_match(rows)["matches"][0]
    import math
    ref_mean = (53.36 + 52.53 + 53.18) / 3
    other_mean = (44.25 + 45.52 + 46.42) / 3
    assert match["level_stops"] == pytest.approx(
        round(math.log2(ref_mean / other_mean), 3))


def test_row_without_neutral_is_skipped_never_content_matched():
    """A shot with no grey in it cannot be matched off its whole-frame
    mean - it is skipped with the reason."""
    rows = [_row("a1", "camA", (53.0, 52.0, 53.0), 1.0, 0.98),
            {"clip_id": "c1", "camera": "camC",
             "source_file": "/footage/camC.MXF",
             "neutral_rgb": None, "neutral_rb": None, "neutral_gb": None,
             "neutral_fraction": 0.0,
             "colour_unmeasured_because": "no grey in this shot"}]
    out = cm.derive_camera_match(rows)
    assert out["reference"] is None
    assert out["matches"] == []
    assert any(s["clip_id"] == "c1" for s in out["skipped"])
    assert "nothing to match" in out["note"]


def test_single_angle_is_a_stated_absence():
    """One angle is nothing to match: the absence is said, not shipped
    as an identity proposal."""
    out = cm.derive_camera_match(
        [_row("a1", "camA", (53.0, 52.0, 53.0), 1.0, 0.98)])
    assert out["matches"] == []
    assert out["reference"] is None


def test_already_matched_angles_say_so():
    """Two angles already together are reported with a neutral slope -
    'these match' is the measurement's own conclusion, and leaving the
    row out would read as 'not examined'."""
    rows = [_row("a1", "camA", (53.0, 52.0, 53.0), 1.0, 0.98),
            _row("a2", "camA2", (53.1, 52.0, 53.0), 1.0019, 0.9811)]
    out = cm.derive_camera_match(rows)
    assert len(out["matches"]) == 1
    assert out["matches"][0]["already_matched"] is True


def test_groups_fall_back_to_file_stem_and_say_so():
    """Rows with no camera label group by source file stem - and the
    row records that, so a reader knows two files from one body read
    as two groups."""
    rows = [dict(_row("a1", "", (53.0, 52.0, 53.0), 1.0, 0.98,
                      source="/footage/LC4932.MXF")),
            dict(_row("b1", "", (44.0, 45.0, 46.0), 0.9565, 0.9783,
                      source="/footage/LCATL0013.MXF"))]
    out = cm.derive_camera_match(rows)
    assert out["grouped_by"] == "source file stem"
    cameras = {out["reference"]["camera"]}
    cameras |= {m["camera"] for m in out["matches"]}
    assert cameras == {"LC4932", "LCATL0013"}
