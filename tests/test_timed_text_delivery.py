"""A declared timed text moment must reach actual pixels.

This is the closing half of the contract `library/tools/timed_text_overlay.py`
held open for four months.  #119 shipped the schema field, the prop
generator and the Remotion composition; `docs/PIPELINE_PLAN.md` recorded
the capability as closed; nothing read it, three declared moments reached
no frame of any render, and every run still reported SUCCESS.

The rule `tests/test_vfx_delivery.py` applies to renderer knobs applies
here: a capability is only real where something reads it, and "a reader
exists" is not the same claim as "the picture changed".  `smart_reframe`
had a reader that printed a tick for months while calling a method
Resolve does not expose.  So this test renders the REAL production path -
`render_timed_text_segments`, the same `npx remotion render` the pipeline
runs, carried afterwards as the overlay codec
(`library/tools/overlay_carriage.py`) - and reads the resulting frames
back with ffmpeg to assert the declared colours are in the declared
halves of the frame at the declared frames, and absent at the frames
where the moment is not running.

It is deliberately a FIXTURE render: 320x568 for 24 frames, under a
second of video.  Nothing here re-renders a project.
"""
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.tools.timed_text_render import render_timed_text_segments
from library.tools.overlay_carriage import (
    OVERLAY_PIXEL_FORMAT,
    OVERLAY_VIDEO_CODEC,
)

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

FIXTURE_WIDTH = 320
FIXTURE_HEIGHT = 568
FIXTURE_FPS = 30

# Two moments whose spans overlap, so they land in ONE segment and one
# render proves clustering, rebasing and compositing at once. Pure
# primaries with the shadow off, so a pixel test can name the colour it
# is looking for; zero fades, so every frame inside a moment's span is
# fully opaque - and because zero fades used to crash the composition
# outright ("inputRange must be strictly monotonically increasing").
RED = (255, 0, 0)
GREEN = (0, 255, 0)

# Timed from the SPINE, which is how a real declaration is written -
# docs/ASSET_LIBRARY_PLAN.md section 5, "no absolute frames, no assumed
# total". Block 1 starts at 1.00s, so ALPHA lands on timeline frame 30.
SPINE = [
    {"position": 0, "block_type": "hook",
     "timeline_start": 0.0, "timeline_end": 1.0},
    {"position": 1, "block_type": "speech",
     "timeline_start": 1.0, "timeline_end": 10.0},
]

DECLARATION = {
    "timed_text_overlay": {
        "font_family": "Helvetica",
        "moments": [
            {
                "text": "ALPHA",
                "color": "#FF0000",
                "font_size": 40,
                "font_weight": 700,
                "block": 1,                 # 1.00s -> timeline frame 30
                "duration_seconds": 0.6,    # 18 frames
                "x": 0.5,
                "y": 0.3,
                "fade_in_frames": 0,
                "fade_out_frames": 0,
                "text_shadow": "none",
            },
            {
                "text": "BETA",
                "color": "#00FF00",
                "font_size": 40,
                "font_weight": 700,
                "block": 1,
                "offset_seconds": 0.2,      # overlaps ALPHA by 12 frames
                "duration_seconds": 0.6,
                "x": 0.5,
                "y": 0.7,
                "fade_in_frames": 0,
                "fade_out_frames": 0,
                "text_shadow": "none",
            },
        ],
    }
}

remotion_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None
    or shutil.which("ffmpeg") is None,
    reason="needs remotion-subtitles/node_modules, npx and ffmpeg",
)


def _extract_frames(mov_path: str, out_dir: str) -> list:
    """Every frame of the segment as RGBA PNGs, alpha preserved."""
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "frame_%03d.png")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", mov_path, "-pix_fmt", "rgba", pattern],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr[-800:]
    return sorted(
        os.path.join(out_dir, n) for n in os.listdir(out_dir)
        if n.endswith(".png"))


def _colour_rows(png_path: str, colour: tuple, tolerance: int = 40) -> set:
    """Which pixel rows carry the given colour at full alpha."""
    from PIL import Image
    image = Image.open(png_path).convert("RGBA")
    width, height = image.size
    pixels = image.load()
    rows = set()
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a < 200:
                continue
            if all(abs(c - t) <= tolerance
                   for c, t in zip((r, g, b), colour)):
                rows.add(y)
                break
    return rows


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """The real render path, at fixture scale."""
    out_dir = tmp_path_factory.mktemp("timed_text")
    segments = render_timed_text_segments(
        DECLARATION,
        REMOTION_DIR,
        str(out_dir / "segments"),
        fps=FIXTURE_FPS,
        width=FIXTURE_WIDTH,
        height=FIXTURE_HEIGHT,
        spine_structure=SPINE,
    )
    frames = _extract_frames(
        segments[0]["overlay_path"], str(out_dir / "frames"))
    return segments, frames


@remotion_available
def test_the_declaration_renders_one_placeable_segment(rendered):
    """Overlapping moments cluster; the segment knows where it goes."""
    segments, _ = rendered
    assert len(segments) == 1
    segment = segments[0]
    assert segment["moment_count"] == 2
    # ALPHA at frame 30 through BETA's last frame at 53 = 24 frames.
    assert segment["total_frames"] == 24
    assert segment["timeline_start"] == 1.0
    assert segment["timeline_end"] == 1.8
    assert os.path.getsize(segment["overlay_path"]) > 0


@remotion_available
def test_every_declared_frame_is_in_the_rendered_file(rendered):
    segments, frames = rendered
    assert len(frames) == segments[0]["total_frames"] == 24


@remotion_available
def test_both_moments_are_in_the_picture_where_they_overlap(rendered):
    """Segment-local frame 10: ALPHA (0-17) and BETA (6-23) both running."""
    _, frames = rendered
    frame = frames[10]
    red_rows = _colour_rows(frame, RED)
    green_rows = _colour_rows(frame, GREEN)
    assert red_rows, f"no red ALPHA pixels in {frame}"
    assert green_rows, f"no green BETA pixels in {frame}"
    # Declared y=0.3 and y=0.7 of a 568px frame, so they are in the
    # halves they were declared in and not swapped.
    assert max(red_rows) < FIXTURE_HEIGHT / 2, (
        f"ALPHA declared at y=0.3 rendered at rows {min(red_rows)}-"
        f"{max(red_rows)} of {FIXTURE_HEIGHT}")
    assert min(green_rows) > FIXTURE_HEIGHT / 2, (
        f"BETA declared at y=0.7 rendered at rows {min(green_rows)}-"
        f"{max(green_rows)} of {FIXTURE_HEIGHT}")


@remotion_available
def test_a_moment_is_absent_before_it_starts(rendered):
    """Segment-local frame 3: ALPHA running, BETA has not started."""
    _, frames = rendered
    frame = frames[3]
    assert _colour_rows(frame, RED), f"no red ALPHA pixels in {frame}"
    assert not _colour_rows(frame, GREEN), (
        f"BETA starts at segment frame 6 but is already in {frame}")


@remotion_available
def test_a_moment_is_absent_after_it_ends(rendered):
    """Segment-local frame 20: ALPHA ended at 18, BETA runs to 23."""
    _, frames = rendered
    frame = frames[20]
    assert not _colour_rows(frame, RED), (
        f"ALPHA ends at segment frame 18 but is still in {frame}")
    assert _colour_rows(frame, GREEN), f"no green BETA pixels in {frame}"


@remotion_available
def test_the_segment_is_carried_as_the_overlay_codec(rendered):
    """The file on disk is what step 4.06 stamps it as.

    `_timed_text_output` reports these segments as `OVERLAY_FORMAT_NAME`,
    so a file left as the Remotion ProRes render would be an artefact
    encoded the old way and stamped the new way - the one combination
    the carriage exists to forbid.
    """
    segments, _ = rendered
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt",
         "-of", "csv=p=0", segments[0]["overlay_path"]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert probe.returncode == 0, probe.stderr[-800:]
    codec, pix_fmt = probe.stdout.strip().split(",")[:2]
    assert codec == OVERLAY_VIDEO_CODEC, (
        f"timed text segment is {codec}, not the overlay codec "
        f"{OVERLAY_VIDEO_CODEC}")
    assert pix_fmt == OVERLAY_PIXEL_FORMAT, (
        f"timed text segment is {pix_fmt}, not the overlay pixel format "
        f"{OVERLAY_PIXEL_FORMAT}")


@remotion_available
def test_the_overlay_is_transparent_where_there_is_no_text(rendered):
    """It composites over the picture; an opaque background would hide it."""
    from PIL import Image
    _, frames = rendered
    image = Image.open(frames[10]).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    for x, y in corners:
        assert pixels[x, y][3] == 0, (
            f"corner ({x},{y}) has alpha {pixels[x, y][3]}; the segment "
            f"would paint over the video underneath it")
