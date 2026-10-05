"""Pixel-level rendering checks (gap E9): transitions, VFX, graphics.

Every gate here is proved in BOTH directions on ffmpeg fixtures built
under ``tmp_path`` - never on real footage (AGENTS.md 8).  The fixtures'
answers are known before anything is measured: a dissolve built with
``xfade`` blends, a hard cut jumps, a freeze holds, a brightness change
moves pixels, and an overlay composites.

The defect each test names:
- a dissolve that renders as a hard cut (the blend never happened);
- a dissolve that renders as a freeze (the transition drew nothing);
- a Fusion comp that draws nothing (the window matches the source);
- an overlay whose ink is absent from the rendered frame.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from library.tools.render_qa import (
    GraphicsSpan,
    TransitionSpan,
    VfxSpan,
    measure_graphics_render,
    measure_transition_render,
    measure_vfx_render,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe is not available here, so the E9 fixtures "
           "cannot be built or measured.",
)

_W, _H = 320, 240
_FPS = 30


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True,
                   encoding="utf-8")


def _src(path, *, brightness=None):
    """A testsrc clip, optionally brightened - the untreated source."""
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=s={_W}x{_H}:d=2:r={_FPS}"]
    if brightness is not None:
        cmd += ["-vf", f"eq=brightness={brightness}"]
    cmd += ["-pix_fmt", "yuv420p", str(path)]
    _run(cmd)
    return str(path)


def _dissolve(path):
    """A cross-dissolve between red and blue at 1.0s, 0.5s long."""
    _run(["ffmpeg", "-v", "error", "-y",
          "-f", "lavfi", "-i", f"color=c=red:s={_W}x{_H}:d=2:r={_FPS}",
          "-f", "lavfi", "-i", f"color=c=blue:s={_W}x{_H}:d=2:r={_FPS}",
          "-filter_complex",
          f"[0:v][1:v]xfade=transition=fade:duration=0.5:offset=1.0",
          "-pix_fmt", "yuv420p", str(path)])
    return str(path)


def _hard_cut(path):
    """A hard cut between red and blue at 1.0s - no blend."""
    _run(["ffmpeg", "-v", "error", "-y",
          "-f", "lavfi", "-i", f"color=c=red:s={_W}x{_H}:d=1:r={_FPS}",
          "-f", "lavfi", "-i", f"color=c=blue:s={_W}x{_H}:d=1:r={_FPS}",
          "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
          "-pix_fmt", "yuv420p", str(path)])
    return str(path)


def _freeze(path):
    """A single unchanging frame - a freeze, not a transition."""
    _run(["ffmpeg", "-v", "error", "-y",
          "-f", "lavfi", "-i", f"color=c=red:s={_W}x{_H}:d=2:r={_FPS}",
          "-pix_fmt", "yuv420p", str(path)])
    return str(path)


def _overlay_composited(path):
    """A testsrc with a red box composited over it."""
    _run(["ffmpeg", "-v", "error", "-y",
          "-f", "lavfi", "-i", f"testsrc=s={_W}x{_H}:d=2:r={_FPS}",
          "-f", "lavfi", "-i", f"color=c=red:s=80x60:d=2:r={_FPS}",
          "-filter_complex", "[0:v][1:v]overlay=120:90",
          "-pix_fmt", "yuv420p", str(path)])
    return str(path)


# ── Transitions ────────────────────────────────────────────────────────


def test_dissolve_renders_as_intermediate_blend(tmp_path):
    """A dissolve built with xfade passes: the blend is intermediate.

    The defect this prevents: a dissolve that renders as a hard cut -
    the blend never happened, the picture jumps.
    """
    video = _dissolve(tmp_path / "dissolve.mp4")
    spans = [TransitionSpan("cross_dissolve", 1.25, 0.5)]
    res = measure_transition_render(video, spans)
    assert res.value["transitions"][0]["verdict"] == "rendered"
    assert res.value["misrendered_transitions"] == 0


def test_hard_cut_fails_the_transition_check(tmp_path):
    """A hard cut where a dissolve was planned fails: no intermediate blend.

    The defect: the transition comp was written but the render shows a
    hard cut - the blend never reached the exported pixels.
    """
    video = _hard_cut(tmp_path / "cut.mp4")
    spans = [TransitionSpan("cross_dissolve", 1.0, 0.5)]
    res = measure_transition_render(video, spans)
    assert res.value["transitions"][0]["verdict"] == "hard_cut"
    assert res.value["misrendered_transitions"] == 1


def test_freeze_fails_the_transition_check(tmp_path):
    """A freeze where a dissolve was planned fails: the picture never changes.

    The defect: the transition drew nothing - the window holds on one
    frame instead of blending between the two sides.
    """
    video = _freeze(tmp_path / "freeze.mp4")
    spans = [TransitionSpan("cross_dissolve", 1.0, 0.5)]
    res = measure_transition_render(video, spans)
    assert res.value["transitions"][0]["verdict"] == "freeze"
    assert res.value["misrendered_transitions"] == 1


def test_zero_duration_transition_is_skipped(tmp_path):
    """A cut (zero duration) draws nothing and has no blend to verify."""
    video = _hard_cut(tmp_path / "cut.mp4")
    spans = [TransitionSpan("hard_cut", 1.0, 0.0)]
    res = measure_transition_render(video, spans)
    assert res.value["transitions"][0]["verdict"] == "skipped"


# ── VFX ────────────────────────────────────────────────────────────────


def test_vfx_that_changed_the_picture_passes(tmp_path):
    """A brightness change reaches the export: the window differs from source.

    The defect this prevents: a Fusion comp that draws nothing - the
    window matches the untreated source.
    """
    source = _src(tmp_path / "vfx_source.mp4")
    render = _src(tmp_path / "vfx_render.mp4", brightness=0.2)
    spans = [VfxSpan("clip_1", "brightness", 0.5, 1.5, source, 0.5, 1.5)]
    res = measure_vfx_render(render, spans)
    assert res.value["effects"][0]["verdict"] == "rendered"
    assert res.value["drew_nothing"] == 0


def test_vfx_that_drew_nothing_fails(tmp_path):
    """A VFX whose window matches the source fails: the comp drew nothing.

    The defect: the effect was planned and the comp was written, but
    the render at the effect's window is identical to the untreated
    source - the effect never reached the exported pixels.
    """
    source = _src(tmp_path / "vfx_source.mp4")
    render = str(tmp_path / "vfx_render.mp4")
    shutil.copy(source, render)
    spans = [VfxSpan("clip_1", "brightness", 0.5, 1.5, source, 0.5, 1.5)]
    res = measure_vfx_render(render, spans)
    assert res.value["effects"][0]["verdict"] == "drew_nothing"
    assert res.value["drew_nothing"] == 1


def test_vfx_with_missing_source_is_skipped(tmp_path):
    """A VFX whose source footage is absent is skipped, never passed."""
    render = _src(tmp_path / "vfx_render.mp4")
    spans = [VfxSpan("clip_1", "brightness", 0.5, 1.5,
                     str(tmp_path / "absent.mp4"), 0.5, 1.5)]
    res = measure_vfx_render(render, spans)
    assert res.value["effects"][0]["verdict"] == "skipped"


# ── Motion graphics ────────────────────────────────────────────────────


def test_overlay_that_composited_passes(tmp_path):
    """An overlay composited into the render passes: the ink is present.

    The defect this prevents: an overlay whose ink is absent from the
    rendered frame - the asset was placed but never composited.
    """
    source = _src(tmp_path / "gfx_source.mp4")
    render = _overlay_composited(tmp_path / "gfx_render.mp4")
    spans = [GraphicsSpan("bug_1", "motion_graphics_overlay", 0.5, 1.5,
                          source, 0.5, 1.5, str(tmp_path / "bug.png"))]
    res = measure_graphics_render(render, spans)
    assert res.value["overlays"][0]["verdict"] == "composited"
    assert res.value["not_composited"] == 0


def test_overlay_that_failed_to_composite_fails(tmp_path):
    """An overlay absent from the render fails: the window matches the picture.

    The defect: the overlay was placed on the timeline but the render
    at its window is identical to the picture underneath - the ink is
    absent from the exported pixels.
    """
    source = _src(tmp_path / "gfx_source.mp4")
    render = str(tmp_path / "gfx_render.mp4")
    shutil.copy(source, render)
    spans = [GraphicsSpan("bug_1", "motion_graphics_overlay", 0.5, 1.5,
                          source, 0.5, 1.5, str(tmp_path / "bug.png"))]
    res = measure_graphics_render(render, spans)
    assert res.value["overlays"][0]["verdict"] == "not_composited"
    assert res.value["not_composited"] == 1


def test_overlay_with_missing_picture_is_skipped(tmp_path):
    """An overlay whose picture underneath is absent is skipped, never passed."""
    render = _src(tmp_path / "gfx_render.mp4")
    spans = [GraphicsSpan("bug_1", "motion_graphics_overlay", 0.5, 1.5,
                          str(tmp_path / "absent.mp4"), 0.5, 1.5,
                          str(tmp_path / "bug.png"))]
    res = measure_graphics_render(render, spans)
    assert res.value["overlays"][0]["verdict"] == "skipped"


# ── Declaration states ─────────────────────────────────────────────────


def test_none_spans_reports_not_measured(tmp_path):
    """None means nobody established the layer - reported, not passed."""
    video = _src(tmp_path / "video.mp4")
    for fn in (measure_transition_render, measure_vfx_render,
               measure_graphics_render):
        res = fn(video, None)
        assert res.passed
        assert "not measured" in res.detail


def test_empty_spans_reports_nothing_to_measure(tmp_path):
    """[] means declared: no effects of this kind - exact, not absent."""
    video = _src(tmp_path / "video.mp4")
    for fn in (measure_transition_render, measure_vfx_render,
               measure_graphics_render):
        res = fn(video, [])
        assert res.passed
        assert "nothing to measure" in res.detail
