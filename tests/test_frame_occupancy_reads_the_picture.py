"""The occupancy gate fails renders that are correct, three ways.

All three were measured on project 001's shipped master, which is
correctly built - 8 letterboxed A-roll placements at `framing_delivered
0.0` and 5 portrait cutaways at 1.0 - and which `validate` refused:

1. **Caption ink read as picture.** A caption is drawn over the bars as
   well as over the picture, and its ink is neither dark nor flat, so the
   bar walk stopped at it. The bottom bar read 347 rows under a caption
   and 656 without one, on a picture that never changes size, and the
   letterbox group's median occupancy came out 0.4755 against a real
   0.3167 - a spread of 0.1609 inside one declared framing.

2. **Samples attributed to the wrong clip.** `fps=N` maps each INPUT
   frame to an output slot by ROUNDING its timestamp and emits the last
   frame to claim the slot, so under the default `round=near` the sample
   labelled 32.0s carried a frame from up to 0.25s later. On 001 that is
   a full-frame cutaway starting at 32.066667s landing in the sample the
   letterboxed A-roll clip before it owns: the letterbox group's max
   occupancy read 1.0 and its spread 0.6833.

3. **A FULL-WIDTH overlay read as picture.** The fix for (1) masked the
   strips outside the box a CENTRED caption can occupy - `max(insets.left,
   insets.right)` = 120 columns each side of a 1080-wide frame. Every
   element `MotionGraphics/index.tsx` draws is laid out from the safe
   area's own left (90) and right (120) edges, so a progress bar starts
   30 columns inside that strip, and a 16px box shadow puts its glow at
   column 74. Narrowing the strips to the asymmetric insets does not fix
   it - 74 is inside 90 too - which is why that fix was rejected on
   review. Measured on 001 with PR 462's emphasis elements drawn: the
   letterbox group read 31.7% at 0.0s against 100.0% at 55.0s.

The answer is not a fourth guess. The pipeline knows exactly what it
drew: every overlay is a transparent segment on the timeline, and its own
alpha names the pixels it touched - glow, shadow and shapes nobody has
invented yet included. `measure_frame_occupancy` takes those segments and
masks what they drew.
"""

import shutil
import subprocess

import numpy as np
import pytest

from library.tools import render_qa
from library.tools.render_qa import (
    DEFAULT_SAMPLE_FPS,
    OverlaySegment,
    _stream_raw_frames,
    measure_frame_occupancy,
)
from library.tools.safe_area import safe_area_for_frame


# The real delivery frame, because the geometry every overlay is laid out
# from is resolved from the frame SIZE and a 108x192 fixture is no
# delivery format.
W, H = 1080, 1920
PICTURE_TOP, PICTURE_BOTTOM = 656, 1264       # 001's own band: 608 of 1920
INSETS = safe_area_for_frame(W, H)

# What `MotionGraphics/index.tsx` really draws for `progress_bar`: a
# 12px-tall track from `safeArea.left` to `width - safeArea.right`, sitting
# `safeArea.bottom` up from the edge, with a 16px box shadow around the
# fill. The glow is what puts ink at column 74, outside BOTH the 120-column
# strip the old mask kept and the 90-column one the rejected fix proposed.
GLOW = 16
BAR_TOP = H - INSETS.bottom - 12
BAR_BOTTOM = H - INSETS.bottom
BAR_LEFT = INSETS.left - GLOW
BAR_RIGHT = W - INSETS.right + GLOW


def _letterboxed():
    """001's A-roll frame: a picture band in bars."""
    frame = np.zeros((1, H, W), dtype=np.uint8)
    frame[0, PICTURE_TOP:PICTURE_BOTTOM, :] = 180
    return frame


def _caption_ink():
    """A centred caption's alpha, drawn over the bottom bar as 4.05's is."""
    ink = np.zeros((1, H, W), dtype=np.uint8)
    half = INSETS.centered_usable_width // 2
    ink[0, 1500:1560, W // 2 - half:W // 2 + half] = 255
    return ink


def _progress_bar_ink():
    """A full-width progress bar's alpha, glow and all."""
    ink = np.zeros((1, H, W), dtype=np.uint8)
    ink[0, BAR_TOP:BAR_BOTTOM, BAR_LEFT:BAR_RIGHT] = 255
    return ink


def _composite(frame, ink, luma=235):
    """The master as the renderer produces it: overlay ink over picture."""
    lit = frame.copy()
    lit[0][ink[0] > 0] = luma
    return lit


def _measure(frames, spans, overlays=None, ink=None):
    """Run the check over `frames`, serving overlay alpha from `ink`.

    `ink` maps an overlay path to the alpha planes that file yields, so
    `_OverlayInk`, `_overlay_ink_frames`, `_rows_outside_the_ink` and
    `_bar_rows` all run for real and only ffmpeg is stood in for.
    """
    ink = ink or {}

    def stream(video_path, *a, **k):
        if video_path in ink:
            return iter(list(ink[video_path]))
        return iter(frames)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(render_qa, "_probe_video_size", lambda p: (W, H))
        mp.setattr(render_qa, "_stream_raw_frames", stream)
        return measure_frame_occupancy("master.mp4", framing_spans=spans,
                                       overlay_segments=overlays)


ALL_LETTERBOX = [(0.0, 10.0, 0.0)]


def test_a_caption_does_not_inflate_the_picture():
    """The captioned and uncaptioned frames must measure the same."""
    captioned = [i % 2 == 0 for i in range(8)]
    caption = _caption_ink()
    frames = [_composite(_letterboxed(), caption) if on else _letterboxed()
              for on in captioned]
    # One segment per captioned sample, which is 4.05's own shape: a
    # segment per speech block, placed where its caption plays.
    overlays = [OverlaySegment("caption.mov", i * 0.5, i * 0.5 + 0.5, 0.0)
                for i, on in enumerate(captioned) if on]
    result = _measure(frames, ALL_LETTERBOX, overlays,
                      ink={"caption.mov": [caption]})

    assert result.passed, result.detail
    group = result.value["by_declared_framing"]["0"]
    assert group["spread"] == 0.0, result.value
    expected = round((PICTURE_BOTTOM - PICTURE_TOP) / H, 4)
    assert group["median_picture_fraction"] == expected


def test_a_full_width_overlay_does_not_inflate_the_picture():
    """The shape that has defeated this check three times.

    A progress bar spans the safe box left to right and glows past it, so
    it lands in the strips outside a centred caption AND outside the
    asymmetric insets. It is drawn over the bottom LETTERBOX BAR, which is
    what makes the bar walk stop at it and read the picture as reaching
    almost to the bottom of the frame.
    """
    bar = _progress_bar_ink()
    frames = [_composite(_letterboxed(), bar) for _ in range(8)]
    overlays = [OverlaySegment("bar.mov", 0.0, 4.0, 0.0)]

    # The old mask, stated as a measurement rather than asserted: the
    # strips it kept are not free of this overlay's ink.
    strip = max(INSETS.left, INSETS.right)
    assert bar[0][:, np.r_[0:strip, W - strip:W]].any(), (
        "the fixture is not a full-width overlay - it misses the strips")
    assert bar[0][:, np.r_[0:INSETS.left, W - INSETS.right:W]].any(), (
        "the fixture would be fixed by the asymmetric-insets patch that "
        "was rejected on review")

    result = _measure(frames, ALL_LETTERBOX, overlays,
                      ink={"bar.mov": [bar] * 8})

    assert result.passed, result.detail
    group = result.value["by_declared_framing"]["0"]
    assert group["spread"] == 0.0, result.value
    assert group["median_picture_fraction"] == round(
        (PICTURE_BOTTOM - PICTURE_TOP) / H, 4), result.value


def test_a_full_width_overlay_over_a_picture_that_fills():
    """The other half: masked rows at the edge are not read as bar.

    Rows the overlay took are resolved from the side the walk reaches
    next. Below a filling picture there is no bar, so a bar drawn over
    the bottom rows must not shrink the measured picture by its own
    height - which is what counting a masked row as bar would do.
    """
    bar = _progress_bar_ink()
    edge = np.zeros((1, H, W), dtype=np.uint8)
    edge[0, H - 20:H, :] = 255          # ink right at the frame edge
    both = np.maximum(bar, edge)
    filled = np.full((1, H, W), 180, dtype=np.uint8)
    frames = [_composite(filled, both) for _ in range(6)]

    result = _measure(frames, [(0.0, 10.0, 1.0)],
                      [OverlaySegment("bar.mov", 0.0, 3.0, 0.0)],
                      ink={"bar.mov": [both] * 6})

    assert result.passed, result.detail
    assert result.value["by_declared_framing"]["1"][
        "median_picture_fraction"] == 1.0, result.value


def test_the_gate_still_fails_a_picture_that_changes_size():
    """The measurement is repaired; the verdict is not weakened."""
    caption = _caption_ink()
    frames = [_composite(_letterboxed(), caption)] * 4
    full = np.full((1, H, W), 180, dtype=np.uint8)
    result = _measure(frames + [full], ALL_LETTERBOX,
                      [OverlaySegment("caption.mov", 0.0, 2.0, 0.0)],
                      ink={"caption.mov": [caption] * 4})
    assert not result.passed
    assert "changes size within one declared framing" in result.detail


def test_the_gate_still_fails_a_size_change_under_a_full_width_overlay():
    """Masking the ink must not mask the defect the ink is drawn over."""
    bar = _progress_bar_ink()
    letterbox = [_composite(_letterboxed(), bar) for _ in range(4)]
    filled = [_composite(np.full((1, H, W), 180, dtype=np.uint8), bar)
              for _ in range(4)]
    result = _measure(letterbox + filled, ALL_LETTERBOX,
                      [OverlaySegment("bar.mov", 0.0, 4.0, 0.0)],
                      ink={"bar.mov": [bar] * 8})
    assert not result.passed, result.value
    assert "changes size within one declared framing" in result.detail


def test_an_overlay_that_covers_the_frame_is_counted_out_not_read_as_full():
    """No row left to read a geometry off is not a picture that fills."""
    everything = np.full((1, H, W), 255, dtype=np.uint8)
    frames = [_composite(_letterboxed(), everything)] * 2 + [_letterboxed()] * 4
    result = _measure(frames, ALL_LETTERBOX,
                      [OverlaySegment("all.mov", 0.0, 1.0, 0.0)],
                      ink={"all.mov": [everything] * 2})

    assert result.value["overlay_covered_frames_skipped"] == 2, result.value
    assert result.value["frames_sampled"] == 4
    assert "covered edge to edge by an overlay" in result.detail
    assert result.passed, result.detail


def test_no_overlay_geometry_is_reported_and_is_not_a_claim_of_none():
    """Three readings, and an absent declaration is not `[]`."""
    frames = [_letterboxed()] * 4
    said_none = _measure(frames, ALL_LETTERBOX, overlays=[])
    said_nothing = _measure(frames, ALL_LETTERBOX, overlays=None)

    assert said_none.value["overlay_segments"] == 0
    assert "alpha" in said_none.value["overlay_masking"]
    assert "no overlay geometry was supplied" not in said_none.detail

    assert said_nothing.value["overlay_segments"] is None
    assert "no overlay geometry was supplied" in said_nothing.detail
    assert "no overlay geometry was supplied" in (
        said_nothing.value["overlay_masking"])


def test_an_overlay_that_cannot_be_read_is_named_and_not_passed_over():
    """A file whose ink goes unmasked is the defect - it must be said."""
    caption = _caption_ink()
    frames = [_composite(_letterboxed(), caption)] * 4

    def stream(video_path, *a, **k):
        if video_path == "gone.mov":
            raise OSError("no such file")
        return iter(frames)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(render_qa, "_probe_video_size", lambda p: (W, H))
        mp.setattr(render_qa, "_stream_raw_frames", stream)
        result = measure_frame_occupancy(
            "master.mp4", framing_spans=ALL_LETTERBOX,
            overlay_segments=[OverlaySegment("gone.mov", 0.0, 2.0, 0.0)])

    assert result.value["overlay_notes"], result.value
    assert "could not be read" in result.detail


@pytest.mark.skipif(shutil.which("ffmpeg") is None,
                    reason="ffmpeg/ffprobe not available")
def test_a_sample_carries_the_frame_at_its_own_timestamp(tmp_path):
    """The fps filter must not hand back a frame from after the label.

    The clip is black for frames 0-31 and white from frame 32 - a change
    at 1.0667s, which is 001's cut at 32.066667s in miniature. The sample
    labelled 1.0s belongs to the black stretch. Under the default
    `round=near` it carried the frame at ~1.233s and came back white.
    """
    clip = tmp_path / "cut.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s=320x320:r=30:d=2",
         "-vf", "drawbox=x=0:y=0:w=320:h=320:color=white@1:t=fill:"
                "enable='gte(n,32)'",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, capture_output=True)

    means = [float(f[0].mean()) for f in _stream_raw_frames(
        str(clip), "gray", 1, 320, 320, DEFAULT_SAMPLE_FPS)]

    # t = 0.0, 0.5, 1.0 are before frame 32; 1.5 is after it.
    assert means[0] < 40 and means[1] < 40
    assert means[2] < 40, (
        f"the sample labelled 1.0s came back at mean {means[2]:.0f} - it is "
        f"carrying a frame from after the cut at 1.0667s")
    assert means[3] > 200


@pytest.mark.skipif(shutil.which("ffmpeg") is None,
                    reason="ffmpeg/ffprobe not available")
def test_the_alpha_of_a_real_transparent_overlay_is_what_is_masked(tmp_path):
    """End to end through ffmpeg: a real ProRes 4444 overlay, a real master.

    The synthetic tests above stand ffmpeg in, so this is the half they
    cannot cover - that `format=rgba,alphaextract` really recovers the
    footprint of a transparent segment, at the sample the master is read
    at, from the codec the overlay steps write.
    """
    # `alphamerge` and not `color=...@0.0`: lavfi's colour source emits
    # yuv420p, so an alpha asked for on it is gone before the encoder sees
    # it and the whole frame comes back opaque.
    box = (f"drawbox=x={BAR_LEFT}:y={BAR_TOP}:w={BAR_RIGHT - BAR_LEFT}:"
           f"h={BAR_BOTTOM - BAR_TOP}:color=white:t=fill")
    overlay = tmp_path / "bar.mov"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=white:s={W}x{H}:r=30:d=2",
         "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=30:d=2,{box}",
         "-filter_complex", "[0][1]alphamerge",
         "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
         str(overlay)],
        check=True, capture_output=True)

    master = tmp_path / "master.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=30:d=2",
         "-vf", f"drawbox=x=0:y={PICTURE_TOP}:w={W}:"
                f"h={PICTURE_BOTTOM - PICTURE_TOP}:color=gray:t=fill,{box}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(master)],
        check=True, capture_output=True)

    spans = [(0.0, 2.0, 0.0)]
    unmasked = measure_frame_occupancy(str(master), framing_spans=spans)
    masked = measure_frame_occupancy(
        str(master), framing_spans=spans,
        overlay_segments=[OverlaySegment(str(overlay), 0.0, 2.0, 0.0)])

    real = (PICTURE_BOTTOM - PICTURE_TOP) / H
    # Told nothing, the bar walk stops at the overlay and the picture
    # reads as running most of the way to the bottom of the frame.
    assert unmasked.value["median_picture_fraction"] > real + 0.1, (
        unmasked.value)
    # Handed the overlay, it reads the band that is really there.
    assert masked.value["median_picture_fraction"] == pytest.approx(
        real, abs=0.01), masked.value
    assert masked.value["samples_carrying_overlay_ink"] > 0
    assert masked.passed, masked.detail
