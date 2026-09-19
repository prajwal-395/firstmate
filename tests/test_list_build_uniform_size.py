"""Sibling list items draw at one glyph size, measured on pixels.

Reels 19/24/25, 2026-09-19: a display-first / supporting-rest plan drew
its first bullet at 56px and the rest at 36px - the renderer sized each
run at its own `type_role`, and the predictor agreed with it, so every
read-backechoed the defect back as declared. The roster now records
that the items of one enumeration are peers sharing one size, and the
composition draws every item at the lead item's size keeping only the
weight contrast.

This renders the real production path - the same `npx remotion render
MotionGraphics` the step runs - and measures glyph sizes off the
frames the way `library/tools/draw_gain_probe.py` does, never by
re-reading a stored value. Reel 25's shipped file is the negative
control: its items measure 33 / 21 / 21px x-height on pixels.
"""

import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

remotion_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None
    or shutil.which("ffmpeg") is None,
    reason="needs remotion-subtitles/node_modules, npx and ffmpeg",
)

#: Reel 25's marked list, verbatim - the ladder the captain saw.
RUNS = [
    {"text": "Niche value", "type_role": "display"},
    {"text": "Answers that matter", "type_role": "supporting"},
    {"text": "Tells a story", "type_role": "supporting"},
]

DURATION = 90
#: Item three is fully revealed at 78 and the exit fade starts at 82,
#: so this frame shows every item at full opacity.
MEASURE_FRAME = 80


def _props():
    return {
        "elements": [
            {
                "element": "list_build",
                "anchor": "top_centre",
                "row": 0,
                "runs": RUNS,
                "color": "#aabbcc",
                "colorBasis": "brand palette role 'text'",
                "entrance": "slide",
                "exit": "fade",
                "timeline_start": 0.0,
                "timeline_end": 3.0,
                "timing_basis": "test",
                "subject": "test",
                "timelineProgressStart": 0.0,
                "timelineProgressEnd": 1.0,
                "startFrame": 0,
                "durationFrames": DURATION,
                "asset": "",
                "footprint": None,
                "emphasis": None,
                "why": "test",
                "data": {},
            }
        ],
        "fps": 30,
        "width": 724,
        "height": 480,
        "safeArea": {"top": 48, "right": 48, "bottom": 188, "left": 48},
        "durationInFrames": DURATION,
    }


def _render(out_dir):
    import json

    props_path = os.path.join(out_dir, "props.json")
    mov_path = os.path.join(out_dir, "mg.mov")
    with open(props_path, "w", encoding="utf-8") as handle:
        json.dump(_props(), handle)
    result = subprocess.run(
        [
            "npx",
            "remotion",
            "render",
            "MotionGraphics",
            mov_path,
            "--props",
            props_path,
            "--codec",
            "prores",
            "--prores-profile",
            "4444",
            "--image-format",
            "png",
            "--transparent",
        ],
        cwd=REMOTION_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return mov_path


def _body_heights(png_path):
    """One body height per text line, in pixels.

    Lit rows group into bands (one per item); within a band each
    connected ink component is one glyph, and the x-height glyphs are
    the numerous short ones - caps, ascenders and descenders are the
    tall minority. The median of the lower half of component heights
    is the size the eye reads, robust to which letters each item
    happens to contain.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    image = np.asarray(Image.open(png_path).convert("RGB")).astype(int)
    lit = image.sum(axis=2) > 300
    rows = lit.any(axis=1)
    bands = []
    start = None
    for y, hit in enumerate(rows):
        if hit and start is None:
            start = y
        elif not hit and start is not None:
            if y - start >= 3:
                bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, len(rows) - 1))
    bodies = []
    for low, high in bands:
        band = lit[low : high + 1, :]
        labels, _ = ndimage.label(band)
        found = ndimage.find_objects(labels)
        heights = sorted(
            (box[0].stop - box[0].start)
            for box in found
            if (labels[box] > 0).sum() >= 15
        )
        assert heights, f"no glyphs in band {low}-{high}"
        bodies.append(heights[len(heights) // 2])
    return bodies


@pytest.fixture(scope="module")
def measured_frame(tmp_path_factory):
    out_dir = str(tmp_path_factory.mktemp("mglist"))
    mov = _render(out_dir)
    png = os.path.join(out_dir, "measure.png")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            mov,
            "-vf",
            f"select=eq(n\\,{MEASURE_FRAME})",
            "-vframes",
            "1",
            png,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr[-800:]
    return png


@remotion_available
def test_sibling_list_items_share_one_glyph_size(measured_frame):
    bodies = _body_heights(measured_frame)
    assert len(bodies) == len(RUNS), (
        f"expected {len(RUNS)} items drawn, measured {len(bodies)} bands"
    )
    assert max(bodies) - min(bodies) <= 1, (
        f"sibling items draw at different sizes: {bodies}"
    )
