"""The `layer` axis: above the picture or behind the segmented subject.

A title the walker passes in front of is `layer: behind_subject` on a
`title_lockup` entry. Absent reads as above - the absence of
compositing, not a choice of it. Unknown refuses by name.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import motion_graphics_plan as mgp

FPS = 30
DURATION = 12.0


def entry(**kw):
    base = {
        "element": "title_lockup",
        "start_seconds": 0.5,
        "duration_seconds": 2.0,
        "anchor": "centre",
        "copy": {"display": "A NAME"},
        "color": "#F5F5F0",
    }
    base.update(kw)
    return base


def resolve(plan):
    return mgp.resolve_plan(plan, timeline_duration=DURATION, fps=FPS)


def test_an_entry_naming_no_layer_draws_above():
    resolved = resolve([entry()])
    assert resolved.moments[0]["layer"] == "above"


def test_behind_subject_is_resolved_and_kept():
    resolved = resolve([entry(layer="behind_subject")])
    assert resolved.basis == mgp.ELEMENTS_PLANNED
    assert not resolved.dropped
    assert resolved.moments[0]["layer"] == "behind_subject"


def test_an_unknown_layer_is_dropped_by_name():
    resolved = resolve([entry(layer="underneath")])
    assert resolved.basis == mgp.EVERY_ENTRY_DROPPED
    assert resolved.dropped[0].reason == "unknown_layer"


def test_behind_moments_become_their_own_full_canvas_segments():
    """`generate_motion_props` never clusters a behind moment with an
    above one: one moment, one full-canvas segment carrying the layer,
    so compile routes it to the matte path instead of a row."""
    from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (
        generate_motion_props,
    )
    spine = {"structure": [{"timeline_end": DURATION}],
             "frame_rate": FPS}
    plan = [entry(layer="behind_subject"),
            entry(anchor="top_left", start_seconds=5.0)]
    segments, resolved = generate_motion_props(
        plan, spine, fps=FPS, width=1080, height=1920)
    assert len(resolved.moments) == 2
    assert len(segments) == 2
    behind = [s for s in segments if s.get("layer") == "behind_subject"]
    assert len(behind) == 1
    assert behind[0]["props"]["width"] == 1080
    assert behind[0]["props"]["height"] == 1920
    assert behind[0]["props"]["elements"][0]["startFrame"] == 0
    above = [s for s in segments if "layer" not in s]
    assert len(above) == 1


def test_a_behind_mov_becomes_a_numbered_png_sequence(tmp_path):
    """The Fusion Loader resolves a numbered PNG sequence where a
    qtrle .mov does not resolve at all, so a behind title is
    sequenced after its render - and a short sequence refuses."""
    import subprocess
    from library.steps.step_4_06_render_motion_graphics import (
        post_bridge as pb,
    )
    mov = tmp_path / "mg_test.mov"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=size=64x64:rate=30:duration=0.2",
         "-c:v", "qtrle", "-pix_fmt", "argb", str(mov)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert proc.returncode == 0, proc.stderr
    rendered = {"segment_id": "t1", "overlay_path": str(mov),
                "total_frames": 6, "layer": "behind_subject"}
    out = pb._sequence_behind_segment(rendered)
    assert out["overlay_path"].endswith("_behind_00000.png")
    assert out["sequence"]["frame_count"] == 6
    assert out["format"] == "PNG image sequence (RGBA)"
    assert out["overlay_path"] != str(mov)

    short = {"segment_id": "t2", "overlay_path": str(mov),
             "total_frames": 600, "layer": "behind_subject"}
    with pytest.raises(pb.MotionGraphicsRenderRefused):
        pb._sequence_behind_segment(short)
