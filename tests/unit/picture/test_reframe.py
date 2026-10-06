"""Subject-aware reframing: a crop that follows the subject, not the centre.

The defect this module exists to pin: a social crop was a dead-centre crop
(ffmpeg `crop=iw/2:ih`, or Resolve's centre conform), so a speaker standing
in the left third of a 16:9 master lost the left side of their head in the
9:16 variant. The pipeline held the subject's measured position
(`face_center_x`) and was not using it for the crop.

These tests pin the geometry the crop path computes: the window keeps the
target aspect, follows the subject, stays inside the source, does not churn
for a centred subject, holds still across a detection gap, and is smoothed
so it does not jitter.
"""
import os
import subprocess

import pytest

from library.tools.reframe import (
    CropWindow,
    assemble_face_track,
    crop_window_size,
    reframe_video,
    subject_crop_path,
    write_sendcmd,
)


# ── crop_window_size ───────────────────────────────────────────────────

def test_16_9_to_9_16_is_a_full_height_narrow_window():
    """A 1920x1080 source reframed to 9:16 keeps the full height and crops
    the sides - the window is 607.5x1080, not a square."""
    win_w, win_h = crop_window_size(1920, 1080, 1080, 1920)
    assert win_h == 1080
    assert win_w == pytest.approx(1080 * 1080 / 1920)


def test_16_9_to_1_1_is_a_square():
    win_w, win_h = crop_window_size(1920, 1080, 1080, 1080)
    assert (win_w, win_h) == (1080, 1080)


def test_a_portrait_source_to_landscape_keeps_the_full_width():
    """The dimension the source is too short in becomes the window's full
    extent; a portrait source reframed to 16:9 keeps the full width."""
    win_w, win_h = crop_window_size(1080, 1920, 1920, 1080)
    assert win_w == 1080
    assert win_h == pytest.approx(1080 * 1080 / 1920)


def test_a_degenerate_source_or_target_raises():
    with pytest.raises(ValueError):
        crop_window_size(0, 1080, 1080, 1920)
    with pytest.raises(ValueError):
        crop_window_size(1920, 1080, 0, 1920)


# ── subject_crop_path: the window follows the subject ─────────────────

def _path(centers, source=(1920, 1080), target=(1080, 1920),
          rate=5.0, smooth=0.5):
    return subject_crop_path(source[0], source[1], target[0], target[1],
                             centers, rate, smooth)


def test_a_subject_in_the_left_third_shifts_the_crop_left():
    """A speaker at 25% of the width must move the crop window left, not
    leave it centred - the defect this module exists to prevent."""
    left = _path([0.25] * 10)
    right = _path([0.75] * 10)
    assert left[0].x < right[0].x
    # Both windows keep the target aspect and fit the source.
    for win in left + right:
        assert win.w == pytest.approx(1080 * 1080 / 1920)
        assert win.h == 1080
        assert 0 <= win.x <= 1920 - win.w


def test_a_subject_at_the_edge_is_clamped_inside_the_source():
    """A subject near the frame edge pushes the window to the edge, not
    past it - a crop window outside the source is a black bar."""
    path = _path([0.05] * 10)  # MIN_PLAUSIBLE_CX, near the left edge
    assert path[0].x == 0
    path = _path([0.95] * 10)  # near the right edge
    assert path[0].x == pytest.approx(1920 - path[0].w)


def test_a_centered_subject_does_not_churn():
    """Inside CENTRE_DEADBAND the crop does not move - the same rule
    `subject_framing` applies per clip, so a centred subject does not make
    the window drift."""
    path = _path([0.5] * 10)
    xs = {round(win.x, 3) for win in path}
    assert len(xs) == 1
    assert path[0].x == pytest.approx((1920 - path[0].w) / 2)


def test_a_detection_gap_holds_still_rather_than_jumping_to_centre():
    """A subject who is undetected for a moment does not yank the crop to
    centre. The defect: treating a None as 0.5 teleports the window."""
    path = _path([0.25, 0.25, None, None, 0.25, 0.25])
    # The gap is forward-filled with the last known position, so the window
    # stays where the subject was.
    assert path[2].x == pytest.approx(path[0].x)
    assert path[3].x == pytest.approx(path[0].x)


def test_a_leading_gap_starts_on_the_subject():
    """A subject detected from sample 2 onward: the crop starts on them,
    not at centre and then jumps."""
    path = _path([None, None, 0.3, 0.3, 0.3])
    assert path[0].x == pytest.approx(path[2].x)


def test_no_track_at_all_is_centered():
    """With no usable detection the crop is centred - the honest default
    when the footage cannot support a position."""
    path = _path([])
    assert path[0].x == pytest.approx((1920 - path[0].w) / 2)


def test_an_implausible_detection_is_ignored():
    """A face hard against the edge (outside the plausibility bounds) is a
    background extra, not the subject - the same bounds `subject_framing`
    applies. Aiming at it is how a crop jerks to the edge on a non-subject."""
    path = _path([0.01] * 10)  # below MIN_PLAUSIBLE_CX
    # Treated as no detection -> centered.
    assert path[0].x == pytest.approx((1920 - path[0].w) / 2)


def test_the_crop_is_smoothed_so_it_does_not_jitter():
    """A noisy track must not make the window jump sample to sample. The
    face track is a 5 Hz cascade with false positives; a crop that tracks
    every sample jitters."""
    noisy = [0.3, 0.7, 0.3, 0.7, 0.3, 0.7, 0.3, 0.7]
    path = _path(noisy, smooth=1.0)
    jumps = [abs(path[i + 1].x - path[i].x) for i in range(len(path) - 1)]
    # The smoothed path's largest single-sample jump is far smaller than
    # the raw track's 0.4-swing (which would be ~0.4 * source_width of
    # window movement per sample).
    assert max(jumps) < 0.4 * 1920


def test_the_target_aspect_is_exact():
    """Every window has the target aspect exactly - a window that drifts
    from the aspect is a stretched or squashed picture."""
    path = _path([0.2, 0.4, 0.6, 0.8] * 3)
    for win in path:
        assert win.w / win.h == pytest.approx(1080 / 1920)


# ── assemble_face_track ───────────────────────────────────────────────

def test_a_single_clip_maps_onto_the_master_clock():
    """A clip at timeline 0 with source start 0: the master track is the
    clip's own face track."""
    track = assemble_face_track(
        [(0.0, 0.0, {"face_center_x": [0.2, 0.4, 0.6, 0.8]})],
        sample_rate_hz=2.0, duration_seconds=2.0)
    assert track == [0.2, 0.4, 0.6, 0.8]


def test_a_second_clip_takes_over_where_the_first_ends():
    """Two clips laid end to end: each owns its timeline range, so the
    master track follows whichever clip is playing."""
    track = assemble_face_track([
        (0.0, 0.0, {"face_center_x": [0.2, 0.4]}),
        (1.0, 0.0, {"face_center_x": [0.7, 0.9]}),
    ], sample_rate_hz=2.0, duration_seconds=2.0)
    assert track == [0.2, 0.4, 0.7, 0.9]


def test_a_clip_with_a_source_offset_reads_the_right_track_index():
    """A clip whose used range starts at source second 1.0: at its
    timeline start the track is read at index source_start*rate, not 0."""
    track = assemble_face_track([
        (0.0, 1.0, {"face_center_x": [0.0, 0.0, 0.7, 0.9]}),
    ], sample_rate_hz=2.0, duration_seconds=2.0)
    # At t=0 the playing source second is 1.0 -> idx 2 -> 0.7.
    assert track[0] == 0.7
    assert track[1] == 0.9


def test_an_undetected_sample_is_none_not_a_fabricated_centre():
    track = assemble_face_track(
        [(0.0, 0.0, {"face_center_x": [0.3, None, 0.5]})],
        sample_rate_hz=1.0, duration_seconds=3.0)
    assert track[0] == 0.3
    assert track[1] is None
    assert track[2] == 0.5


# ── write_sendcmd ─────────────────────────────────────────────────────

def test_sendcmd_writes_one_interval_per_window():
    """The sendcmd file holds each window constant across its interval and
    updates at the boundary - the format ffmpeg's `sendcmd=f=` reads."""
    path = [CropWindow(x=10, y=0, w=100, h=100),
            CropWindow(x=20, y=0, w=100, h=100)]
    out = write_sendcmd(path, "/tmp/_reframe_test_cmds.txt", fps=2.0)
    with open(out, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("0.000000 0.500000 crop w=100:h=100:x=10:y=0;")
    assert lines[1].startswith("0.500000 1.000000 crop w=100:h=100:x=20:y=0;")


# ── reframe_video: the crop reaches the pixels ────────────────────────

def test_reframe_video_produces_a_file_at_the_target_size(tmp_path):
    """The crop path reaches the pixels: ffmpeg runs the sendcmd file and
    produces a reframed file at the target dimensions.

    This is the one expensive test in the module - it encodes a 2-frame
    fixture - and its reason is exactly that: the geometry above is proven
    pure, and this proves the sendcmd mechanism actually moves the crop in
    a real encode, which is the half a unit test of the geometry cannot
    see.
    """
    src = str(tmp_path / "src.mp4")
    # A 16:9 fixture: a moving pattern so a moving crop yields a
    # measurably different still.
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i",
        "testsrc=duration=1:size=320x240:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", src,
    ], capture_output=True, check=True)
    # Reframe to a 1:1 square following a subject that moves right.
    centers = [0.3] * 5 + [0.7] * 5
    path = subject_crop_path(320, 240, 240, 240, centers,
                             sample_rate_hz=10.0, smooth_seconds=0.0)
    out = str(tmp_path / "out.mp4")
    result = reframe_video(src, out, path, 240, 240, fps=10.0)
    assert os.path.exists(result["output_path"])
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
        capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "240,240"


def test_reframe_video_refuses_a_missing_input(tmp_path):
    with pytest.raises(RuntimeError):
        reframe_video(str(tmp_path / "nope.mp4"), str(tmp_path / "o.mp4"),
                      [CropWindow(0, 0, 100, 100)], 100, 100, fps=10.0)
