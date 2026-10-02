"""Finding 28: verify grades on exported pixels, not on the SetCDL return.

Scout B5 (VX1.2): saturation 0.88 plus power/slope reached SetCDL on
every clip, but the export measured a chroma ratio of 0.96-1.03
(expected ~0.88); B1 (CO2.3): +1.3 stops moved luma 43 -> 82 where
~130 was the target. The build reported "Applied" off the write.

Two halves:

- `cdl_readback.compare_cdl`: the build judges the WRITE by reading
  `GetCDL()` back against the four specified terms - a mismatch
  errors naming the clip, an unreadable read-back warns.
- `render_qa.measure_grade_delivery`: 6.02 judges the PIXELS. Each
  graded span's export window is measured against the SOURCE file it
  was cut from, and the direction the CDL demands is checked with a
  dead zone. REPORTED per span, never gated.

The videos here are generated with ffmpeg (a declared dependency;
skipped where it is absent): a saturated source, a desaturated
"graded" export of it, and an ungraded copy.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ffmpeg = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not ffmpeg, reason="ffmpeg absent")


def _gen(path, sat=None, duration=2.0):
    vf = f",eq=saturation={sat}" if sat is not None else ""
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
           f"color=c=0xC04020:s=64x64:d={duration}:r=10",
           "-vf", f"format=yuv444p{vf},format=yuv444p",
           "-frames:v", "20", "-pix_fmt", "yuv444p", str(path)]
    subprocess.run(cmd, check=True, timeout=120)


@pytest.fixture
def trio(tmp_path):
    _gen(tmp_path / "source.mp4")
    _gen(tmp_path / "graded.mp4", sat=0.5)
    _gen(tmp_path / "ungraded.mp4")
    return tmp_path


def _span(label, export, source, sat=0.88, slope=1.0,
          source_start=0.0, source_end=2.0):
    from library.tools.render_qa import GradeSpan
    return GradeSpan(label=label, timeline_start=0.0, timeline_end=2.0,
                     source_path=str(source),
                     cdl={"slope_r": slope, "slope_g": slope,
                          "slope_b": slope, "offset_r": 0.0,
                          "offset_g": 0.0, "offset_b": 0.0,
                          "power_r": 1.0, "power_g": 1.0,
                          "power_b": 1.0, "saturation": sat},
                     source_start=source_start, source_end=source_end)


# ── cdl_readback: the write is judged ────────────────────────────

def test_mismatched_slope_names_the_channel():
    from library.tools import cdl_readback as cdl
    cdl_values = {"slope_r": 1.08, "slope_g": 1.0, "slope_b": 1.0,
                  "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
                  "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
                  "saturation": 1.0}
    actual = {"Slope": "1.0000 1.0000 1.0000",
              "Offset": "0.0000 0.0000 0.0000",
              "Power": "1.0000 1.0000 1.0000",
              "Saturation": "1.0"}
    (problem,) = cdl.compare_cdl(actual, cdl_values)
    assert "Slope[0]" in problem
    # The same read-back agrees once the write took.
    actual["Slope"] = "1.0800 1.0000 1.0000"
    assert cdl.compare_cdl(actual, cdl_values) == []


def test_unreadable_term_is_unverifiable_not_agreement():
    from library.tools import cdl_readback as cdl
    problems = cdl.compare_cdl({}, {"saturation": 0.88})
    assert problems and all("unverifiable" in p for p in problems)


# ── measure_grade_delivery: the pixels are judged ────────────────

@needs_ffmpeg
def test_desaturated_export_delivers_a_desat_demand(trio):
    """The B5 demand (sat 0.88) against pixels that really moved:
    delivered."""
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery(
        str(trio / "graded.mp4"),
        [_span("clip_a", trio / "graded.mp4", trio / "source.mp4")],
        sample_fps=5.0)
    assert result.passed
    (row,) = result.value["spans"]
    assert row["verdict"] == "delivered"
    assert row["chroma_ratio"] < 0.99


@needs_ffmpeg
def test_unmoved_export_contradicts_a_desat_demand(trio):
    """Finding 28's exact shape: sat 0.88 specified, export chroma
    unmoved - contradicts, and the detail says the direction."""
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery(
        str(trio / "ungraded.mp4"),
        [_span("clip_a", trio / "ungraded.mp4", trio / "source.mp4")],
        sample_fps=5.0)
    assert not result.passed
    (row,) = result.value["spans"]
    assert row["verdict"] == "contradicts"
    assert "down" in row["detail"]


@needs_ffmpeg
def test_identity_cdl_demands_nothing_and_a_missing_source_is_unverifiable(
        trio):
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery(
        str(trio / "ungraded.mp4"),
        [_span("clip_a", trio / "ungraded.mp4", trio / "source.mp4",
               sat=1.0)],
        sample_fps=5.0)
    assert result.passed
    assert result.value["spans"][0]["verdict"] == "no demand"
    # A span whose source is gone cannot be judged, and says so.
    result = measure_grade_delivery(
        str(trio / "graded.mp4"),
        [_span("clip_a", trio / "graded.mp4", trio / "gone.mp4")],
        sample_fps=5.0)
    (row,) = result.value["spans"]
    assert row["verdict"] == "unverifiable"


def test_no_spans_is_info_not_failure():
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery("/nonexistent.mp4", [])
    assert result.passed and result.severity == "info"


@needs_ffmpeg
def test_source_reference_is_the_played_range_not_the_file(tmp_path):
    """The proof-timeline lesson: four items playing source 0-5s read
    1.11/2.01/2.23/2.23 against the whole-file median (content
    aliasing), 0.499/0.904/1.0 same-range. A two-tone source proves
    the window: the second-half reference must read dark."""
    from library.tools.render_qa import _median_luma_and_chroma
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
           "color=c=0xFFFFFF:s=64x64:d=1:r=10",
           "-f", "lavfi", "-i", "color=c=0x202020:s=64x64:d=1:r=10",
           "-filter_complex",
           "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv444p",
           "-frames:v", "20", "-pix_fmt", "yuv444p",
           str(tmp_path / "duo.mp4")]
    subprocess.run(cmd, check=True, timeout=120)
    size = (64, 64)
    first, _ = _median_luma_and_chroma(
        str(tmp_path / "duo.mp4"), *size, 10.0,
        start_seconds=0.0, duration_seconds=1.0)
    second, _ = _median_luma_and_chroma(
        str(tmp_path / "duo.mp4"), *size, 10.0,
        start_seconds=1.0, duration_seconds=1.0)
    assert first > 200.0 and second < 60.0


# ── _grade_spans: the manifest join ──────────────────────────────

def test_grade_spans_join_tracks_to_per_clip_cdl():
    from library.steps.step_6_02_validate_output.bridge import _grade_spans
    manifest = {
        "project": {"frame_rate": 30.0},
        "tracks": {
            "V1": {"clips": [
                {"label": "hook", "source_file": "/raw/IMG_A.MOV",
                 "timeline_in": 0.0, "timeline_end_missing": True,
                 "timeline_out": 2.4}]},
            "V2": {"clips": [
                {"label": "broll", "source_file": "/raw/IMG_B.MOV",
                 "timeline_in": 1.0, "timeline_out": 2.0}]}},
        "color_grade": {"per_clip_adjustments": [
            {"source_file": "/raw/IMG_A.MOV",
             "cdl_values": {"saturation": 0.88}},
            {"source_file": "/raw/IMG_B.MOV", "cdl_values": {}}]},
    }
    spans = _grade_spans(manifest)
    assert [(s.label, s.timeline_start, s.timeline_end) for s in spans] == [
        ("hook", 0.0, 2.4), ("broll", 1.0, 2.0)]
    assert spans[0].cdl["saturation"] == 0.88
    assert (spans[0].source_start, spans[0].source_end) == (None, None)
    # Track ranges ride into the span: the reference is the played
    # range, never the whole file.
    manifest["tracks"]["V1"]["clips"][0]["source_in"] = 0.836
    manifest["tracks"]["V1"]["clips"][0]["source_out"] = 3.234
    spans = _grade_spans(manifest)
    assert (spans[0].source_start, spans[0].source_end) == (0.836, 3.234)
    # Without a declared grade there is nothing to join.
    assert _grade_spans({"tracks": {}}) is None

