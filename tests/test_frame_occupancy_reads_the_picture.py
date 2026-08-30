"""The occupancy gate fails renders that are correct, two ways.

Both were measured on project 001's shipped master, which is correctly
built - 8 letterboxed A-roll placements at `framing_delivered 0.0` and 5
portrait cutaways at 1.0 - and which `validate` refused:

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

Measured after the fix, same render, same manifest: framing 0 spread
0.0000 over 81 samples, framing 1 spread 0.0135 over 33, `passed: True`.
"""

import shutil
import subprocess

import numpy as np
import pytest

from library.tools import render_qa
from library.tools.render_qa import (
    DEFAULT_SAMPLE_FPS,
    _stream_raw_frames,
    measure_frame_occupancy,
)
from library.tools.safe_area import safe_area_for_frame


# The real delivery frame, because the masking is resolved from the frame
# SIZE and a 108x192 fixture is no delivery format.
W, H = 1080, 1920
PICTURE_TOP, PICTURE_BOTTOM = 656, 1264       # 001's own band: 608 of 1920


def _letterboxed(caption: bool):
    """001's A-roll frame: a picture band in bars, optionally captioned."""
    frame = np.zeros((1, H, W), dtype=np.uint8)
    frame[0, PICTURE_TOP:PICTURE_BOTTOM, :] = 180
    if caption:
        # Centred, inside the band a centred box can occupy, drawn over
        # the bottom bar exactly as the subtitle overlay does.
        insets = safe_area_for_frame(W, H)
        half = insets.centered_usable_width // 2
        frame[0, 1500:1560, W // 2 - half:W // 2 + half] = 240
    return frame


def _measure(frames, spans):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(render_qa, "_probe_video_size", lambda p: (W, H))
        mp.setattr(render_qa, "_stream_raw_frames",
                   lambda *a, **k: iter(frames))
        return measure_frame_occupancy("master.mp4", framing_spans=spans)


ALL_LETTERBOX = [(0.0, 10.0, 0.0)]


def test_a_caption_does_not_inflate_the_picture():
    """The captioned and uncaptioned frames must measure the same."""
    frames = [_letterboxed(caption=(i % 2 == 0)) for i in range(8)]
    result = _measure(frames, ALL_LETTERBOX)

    assert result.passed, result.detail
    group = result.value["by_declared_framing"]["0"]
    assert group["spread"] == 0.0, result.value
    expected = round((PICTURE_BOTTOM - PICTURE_TOP) / H, 4)
    assert group["median_picture_fraction"] == expected
    assert tuple(result.value["picture_band_first_frame"]) == (
        PICTURE_TOP, PICTURE_BOTTOM - 1)


def test_the_masking_is_recorded_on_the_result():
    result = _measure([_letterboxed(caption=True)] * 4, ALL_LETTERBOX)
    insets = safe_area_for_frame(W, H)
    assert result.value["overlay_free_columns_each_side"] == max(
        insets.left, insets.right)
    assert "centred overlay" in result.value["overlay_masking"]


def test_a_frame_size_no_format_describes_says_so_rather_than_guessing():
    """The absence is admitted, and the check still runs full width."""
    small_w, small_h = 108, 192
    frames = []
    for _ in range(4):
        f = np.zeros((1, small_h, small_w), dtype=np.uint8)
        f[0, 60:130, :] = 180
        frames.append(f)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(render_qa, "_probe_video_size", lambda p: (small_w, small_h))
        mp.setattr(render_qa, "_stream_raw_frames",
                   lambda *a, **k: iter(frames))
        result = measure_frame_occupancy("master.mp4",
                                         framing_spans=[(0.0, 10.0, 0.0)])
    assert result.value["overlay_free_columns_each_side"] == 0
    assert "no safe area describes" in result.value["overlay_masking"]
    assert "no safe area describes" in result.detail


def test_the_gate_still_fails_a_picture_that_changes_size():
    """The measurement is repaired; the verdict is not weakened."""
    frames = [_letterboxed(caption=True)] * 4
    full = np.full((1, H, W), 180, dtype=np.uint8)
    result = _measure(frames + [full], ALL_LETTERBOX)
    assert not result.passed
    assert "changes size within one declared framing" in result.detail


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
