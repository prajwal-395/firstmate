"""Reference editorial-style measurement: the cut pattern, the rhythm.

The defect class this module exists for: the scene filter the
temporal index uses is luma-weighted and misses cuts between shots of
similar luma - measured on a red->green solid-colour cut it scored the
cut below 0.001 (undetectable) while the saturation jump was 59.  A
style measurement built on that filter would report a shot-length
distribution that is not the reference's.  These tests pin the
behaviour on parsed ffmpeg output and on an encoded video, so the
regression fails here rather than in a run.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import reference_style as RS  # noqa: E402

HAS_FFMPEG = (shutil.which("ffmpeg") is not None
              and shutil.which("ffprobe") is not None)


def _stats_text(*frames):
    """ffmpeg signalstats metadata text, one frame per (luma, sat)."""
    lines = []
    for index, (luma, saturation) in enumerate(frames):
        lines.append(f"frame:{index} pts:{index} pts_time:{index / 10}")
        lines.append(f"lavfi.signalstats.YAVG={luma}")
        lines.append(f"lavfi.signalstats.SATAVG={saturation}")
    return "\n".join(lines) + "\n"


# ── The threshold is derived, never fixed ─────────────────────────

def test_the_cut_threshold_comes_from_the_video_s_own_delta_distribution():
    """A quiet video and a noisy video do not get the same threshold.

    The failure it pins: a fixed threshold either false-positives on
    clean footage (every encoding wobble becomes a cut) or misses
    cuts in noisy ones.  The median within-shot jump is the noise
    floor, so the threshold must move with the footage it measures.
    """
    quiet = RS._cut_threshold([0.1, 0.2, 0.0, 0.1, 0.3], 8.0, 20.0)
    noisy = RS._cut_threshold([3.0, 4.0, 2.5, 3.5, 3.0], 8.0, 20.0)
    assert quiet == 8.0
    assert noisy == 60.0
    assert noisy > quiet


def test_a_noise_free_video_still_gets_a_floor():
    """A median of zero must not make every frame a cut.

    The failure it pins: with no absolute floor, a perfectly clean
    video's threshold collapses to zero and the measurement reports
    a cut at every frame.
    """
    assert RS._cut_threshold([0.0, 0.0, 0.0], 8.0, 20.0) == 8.0


# ── The saturation channel is read, not just luma ─────────────────

def test_a_cut_between_similar_luma_is_caught_by_its_saturation_jump():
    """A red->green cut is invisible to luma alone.

    The failure it pins: reading only YAVG misses every cut between
    shots of similar luma but different colour - the measured case
    scored below 0.001 on the luma-weighted scene filter while the
    saturation jump was 59.  The delta must carry both channels.
    """
    text = _stats_text(
        (100.0, 20.0), (100.0, 20.0), (100.0, 20.0),
        (100.0, 80.0), (100.0, 80.0), (100.0, 80.0),
    )
    deltas = RS._frame_deltas(text)
    assert deltas[2]["luma_jump"] == 0.0
    assert deltas[2]["saturation_jump"] == 60.0


def test_frame_deltas_parse_one_jump_per_frame_pair():
    """The parser reads one YAVG/SATAVG pair per frame, in order.

    The failure it pins: a parser that keyed on line position rather
    than the per-frame metadata would misalign luma and saturation
    and invent jumps that are not in the video.
    """
    text = _stats_text((10.0, 5.0), (12.0, 5.0), (30.0, 40.0))
    deltas = RS._frame_deltas(text)
    assert len(deltas) == 2
    assert deltas[0]["luma_jump"] == 2.0
    assert deltas[0]["saturation_jump"] == 0.0
    assert deltas[1]["luma_jump"] == 18.0
    assert deltas[1]["saturation_jump"] == 35.0


# ── The distribution is a shape, not a mean ────────────────────────

def test_the_distribution_separates_a_uniform_rhythm_from_a_mixed_one():
    """Two pieces can share a mean and not share a style.

    The failure it pins: reporting only the average shot length
    treats a piece cut to a uniform rhythm and one alternating long
    and short shots at the same average as the same style - the
    coefficient of variation is what a reader compares.
    """
    uniform = RS.shot_length_distribution(
        [{"time_seconds": t} for t in (2.0, 4.0, 6.0, 8.0, 10.0)], 12.0)
    mixed = RS.shot_length_distribution(
        [{"time_seconds": t} for t in (0.1, 0.2, 0.3, 0.4, 0.5)], 12.0)
    assert uniform["asl_seconds"] == mixed["asl_seconds"] == 2.0
    assert uniform["coefficient_of_variation"] < 0.1
    assert mixed["coefficient_of_variation"] > 1.0


def test_the_histogram_groups_what_was_measured():
    """Bins are a ruler's marks, not a decision about shot length.

    The failure it pins: a histogram whose bins were derived from the
    data would make every distribution look uniform - the bins are
    fixed so two references can be compared against the same marks.
    """
    distribution = RS.shot_length_distribution(
        [{"time_seconds": t} for t in (0.5, 1.5, 3.0, 5.0)], 10.0)
    counts = [bin_["count"] for bin_ in distribution["histogram"]]
    assert counts == [1, 2, 1, 1, 0]


# ── The transition vocabulary ──────────────────────────────────────

def test_the_vocabulary_counts_gradual_transitions_apart_from_hard_cuts():
    """A dissolve and a cut are different style facts.

    The failure it pins: counting every boundary as a cut reports a
    reference that dissolves between shots as one that hard-cuts,
    and the transition planner would read the wrong vocabulary.
    """
    vocabulary = RS.transition_vocabulary([
        {"transition": "hard_cut"},
        {"transition": "hard_cut"},
        {"transition": "gradual"},
    ])
    assert vocabulary["hard_cuts"] == 2
    assert vocabulary["gradual_transitions"] == 1
    assert vocabulary["gradual_share"] == 0.333


def test_a_cluster_of_small_jumps_is_a_dissolve_not_a_row_of_cuts():
    """A gradual transition is a run of small jumps, not one big one.

    The failure it pins: classifying each fade frame's jump on its
    own would count a five-frame dissolve as five hard cuts - the
    cluster is the unit, and its span decides what it is.
    """
    deltas = [{"frame": f, "luma_jump": 15.0, "saturation_jump": 0.0}
              for f in range(5, 10)]
    clusters = RS._cluster_events(deltas, fps=10.0)
    assert len(clusters) == 1
    classified = RS._classify_clusters(clusters, fps=10.0,
                                       luma_high=20.0, saturation_high=20.0)
    assert len(classified) == 1
    assert classified[0]["type"] == "gradual"


def test_a_single_large_jump_is_a_hard_cut():
    """One frame's jump far above the noise is a cut, full stop.

    The failure it pins: requiring a cluster of events would miss
    every true hard cut, which is exactly one frame of large jump.
    """
    deltas = [{"frame": 5, "luma_jump": 120.0, "saturation_jump": 0.0}]
    clusters = RS._cluster_events(deltas, fps=10.0)
    classified = RS._classify_clusters(clusters, fps=10.0,
                                       luma_high=20.0, saturation_high=20.0)
    assert len(classified) == 1
    assert classified[0]["type"] == "hard_cut"


def test_a_long_run_of_jumps_is_a_camera_move_not_a_transition():
    """A slow pan across a changing scene is not a dissolve.

    The failure it pins: calling every multi-event cluster a gradual
    transition would put camera moves in the transition vocabulary
    and inflate the gradual share.
    """
    deltas = [{"frame": f, "luma_jump": 15.0, "saturation_jump": 0.0}
              for f in range(5, 30)]
    clusters = RS._cluster_events(deltas, fps=10.0)
    classified = RS._classify_clusters(clusters, fps=10.0,
                                       luma_high=20.0, saturation_high=20.0)
    assert classified == []


# ── Pacing ────────────────────────────────────────────────────────

def test_pacing_says_when_the_piece_tightens():
    """The first half beside the second is the cheapest rhythm reading.

    The failure it pins: a single cuts-per-minute figure cannot tell a
    piece that accelerates from one that is uniform - the two halves
    must be measured apart and compared.
    """
    boundaries = [{"time_seconds": t} for t in (3.0, 6.0, 7.0, 7.5)]
    pacing = RS.pacing_profile(boundaries, duration=8.0)
    assert pacing["first_half_asl_seconds"] == 3.0
    assert pacing["second_half_asl_seconds"] == pytest.approx(2 / 3, abs=0.001)
    assert pacing["accelerates"] is True


def test_pacing_says_when_the_piece_is_uniform():
    """A uniform piece must not be reported as accelerating.

    The failure it pins: an acceleration flag that fires on any
    difference would report every piece as tightening.
    """
    boundaries = [{"time_seconds": t} for t in (2.0, 4.0, 6.0)]
    pacing = RS.pacing_profile(boundaries, duration=8.0)
    assert pacing["accelerates"] is False


# ── The encoded video ──────────────────────────────────────────────

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_an_encoded_dissolve_is_gradual_and_a_colour_change_is_a_cut(
        tmp_path):
    """The detector's behaviour on real encoded pixels, end to end.

    This test encodes media because the defect it pins lives between
    the parser and the picture: a cut detector fed only parsed text
    cannot prove the saturation channel fires through ffmpeg's own
    signalstats on an encoded dissolve - the exact case where the
    scene filter scored a real cut below 0.001.  2.5 s at 10 fps, 64
    px: red, a 0.5 s fade to green, then a hard cut to blue.
    """
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=green:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
         "-filter_complex",
         "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=0.5[v01];"
         "[v01][2:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    measured = RS.measure_shot_boundaries(str(video))
    boundaries = measured["boundaries"]
    assert len(boundaries) == 2
    assert boundaries[0]["transition"] == "gradual"
    assert boundaries[0]["time_seconds"] == pytest.approx(0.5, abs=0.15)
    assert boundaries[1]["transition"] == "hard_cut"
    assert boundaries[1]["time_seconds"] == pytest.approx(1.5, abs=0.15)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_measurement_records_the_thresholds_that_found_the_cuts(
        tmp_path):
    """A cut without the threshold that found it is not reviewable.

    The failure it pins: a boundary list with no derivation lets a
    reader see WHAT was cut but never WHY - the noise floor and the
    margin are what a reader checks when a cut looks wrong.
    """
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=green:s=64x64:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    measured = RS.measure_shot_boundaries(str(video))
    assert len(measured["boundaries"]) == 1
    assert measured["boundaries"][0]["transition"] == "hard_cut"
    assert measured["thresholds"]["luma_cut"] >= RS._LUMA_CUT_FLOOR
    assert "median" in measured["thresholds"]["derived_as"]


# ── The graphics half ─────────────────────────────────────────────

def _solid_frame(path, colour):
    """A frame of one solid colour, as PNG."""
    from PIL import Image
    Image.new("RGB", (32, 32), colour).save(path)


def _split_frame(path, left, right):
    """A frame half one colour, half another, as PNG."""
    from PIL import Image
    image = Image.new("RGB", (32, 32))
    for x in range(16):
        for y in range(32):
            image.putpixel((x, y), left)
            image.putpixel((x + 16, y), right)
    image.save(path)


def test_the_palette_reports_each_colour_s_coverage(tmp_path):
    """A two-colour frame yields two dominant colours at about half each.

    The failure it pins: a palette that listed colours without their
    coverage could not say whether a colour dominates the reference or
    is a rare accent - the coverage IS the style fact.  The two
    dominant entries are the frame's own colours; the quantizer's
    edge entries are the boundary between them.
    """
    frame = tmp_path / "frame.png"
    _split_frame(frame, (255, 0, 0), (0, 0, 255))
    palette = RS.measure_palette([str(frame)])
    assert palette["sampled_frames"] == 1
    assert len(palette["colours"]) == 4
    dominant = palette["colours"][:2]
    assert {c["hex"] for c in dominant} == {"#ff0000", "#0000ff"}
    assert all(c["coverage"] == pytest.approx(0.5, abs=0.05)
               for c in dominant)
    assert sum(c["coverage"] for c in dominant) > 0.9


def test_a_uniform_frame_has_one_colour_at_full_coverage(tmp_path):
    """A solid frame is one colour, not a palette.

    The failure it pins: a quantizer forced to return `_PALETTE_COLOURS`
    entries would invent colours the reference does not have.
    """
    frame = tmp_path / "frame.png"
    _solid_frame(frame, (0, 128, 0))
    palette = RS.measure_palette([str(frame)])
    assert len(palette["colours"]) == 1
    assert palette["colours"][0]["coverage"] == 1.0


def test_contrast_separates_a_flat_frame_from_a_contrasty_one(tmp_path):
    """A solid frame and a black-white frame are not the same style.

    The failure it pins: a contrast reading that reported only the mean
    luma would call a mid-grey solid frame and a black-white frame the
    same - the spread is the contrast.
    """
    flat = tmp_path / "flat.png"
    contrasty = tmp_path / "contrasty.png"
    _solid_frame(flat, (128, 128, 128))
    _split_frame(contrasty, (0, 0, 0), (255, 255, 255))
    flat_reading = RS.measure_contrast([str(flat)])
    contrasty_reading = RS.measure_contrast([str(contrasty)])
    assert flat_reading["luma_std"] < 1.0
    assert contrasty_reading["luma_std"] > 100.0
    assert (contrasty_reading["contrast_range"]
            > flat_reading["contrast_range"])


def test_contrast_pools_the_luma_of_every_sampled_frame(tmp_path):
    """The contrast is the reference's, not one frame's.

    The failure it pins: reading only the first frame would report a
    contrast the reference does not have - a dark opening on an
    otherwise bright piece would read as a low-contrast style.
    """
    dark = tmp_path / "dark.png"
    bright = tmp_path / "bright.png"
    _solid_frame(dark, (10, 10, 10))
    _solid_frame(bright, (240, 240, 240))
    reading = RS.measure_contrast([str(dark), str(bright)])
    assert reading["sampled_frames"] == 2
    assert reading["luma_mean"] == 125.0
    assert reading["luma_std"] > 100.0


def test_the_graphics_half_samples_the_midpoint_of_each_shot():
    """One frame per shot, at its middle - the representative frame.

    The failure it pins: sampling at the cut points would capture the
    transition frames (a dissolve's in-between colours), not the
    shots' own colours - the palette would be the transitions'.
    """
    boundaries = [{"time_seconds": 2.0}, {"time_seconds": 6.0}]
    times = RS._reference_frame_times(boundaries, duration=8.0)
    assert times == [1.0, 4.0, 7.0]


def test_a_long_reference_is_sampled_evenly_not_only_at_the_start():
    """The cap spreads across the piece.

    The failure it pins: taking the first N shots would measure only
    the opening's style - a reference whose look changes would be
    reported as its first minutes.
    """
    boundaries = [{"time_seconds": float(t)} for t in range(1, 21)]
    times = RS._reference_frame_times(boundaries, duration=21.0)
    assert len(times) == RS._MAX_GRAPHICS_FRAMES
    assert times[0] < times[-1]
    assert times[-1] > 10.0


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_an_encoded_reference_yields_its_own_palette_and_contrast(
        tmp_path):
    """The graphics half on real encoded pixels, end to end.

    This test encodes media because the palette and the contrast are
    distribution facts about the reference's actual pixels - a
    measurement fed only synthetic pixel arrays cannot prove the
    frames are extracted at the right moments and read back through
    the real ffmpeg/PIL path.  The red/green/blue reference yields
    three palette entries of equal coverage.
    """
    video = tmp_path / "reference.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=green:s=64x64:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
         "-filter_complex",
         "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=0.5[v01];"
         "[v01][2:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(video)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    style = RS.measure_reference_graphical_style(str(video))
    colours = style["palette"]["colours"]
    assert len(colours) == 3
    assert all(c["coverage"] == pytest.approx(1 / 3, abs=0.05)
               for c in colours)
    assert style["contrast"]["luma_std"] > 10.0
