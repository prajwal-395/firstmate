"""The motion-graphics carrying option: full canvas or the drawn union.

`render_one_segment` behind a stubbed `subprocess.run`, so what is
pinned is the unit's own decisions - names, records, fallback - without
paying for Remotion. The default path (tight, since 2026-09-10) is
asserted first; explicit full still renders full canvas.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.post_bridge import (  # noqa: E402
    render_one_segment,
)

SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _el(element, anchor="middle_centre", duration=60):
    return {
        "element": element,
        "anchor": anchor,
        "row": 0,
        "runs": [{"text": "and so my very", "type_role": "supporting"}],
        "color": "#FFDD55",
        "entrance": "fade",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": duration,
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": 0.5,
        "footprint": None,
        "emphasis": None,
    }


def _planned(elements):
    total = max(e["startFrame"] + e["durationFrames"] for e in elements)
    return {
        "index": 0,
        "timeline_start": 1.0,
        "timeline_end": 1.0 + total / 30.0,
        "total_frames": total,
        "element_count": len(elements),
        "elements": sorted({e["element"] for e in elements}),
        "props": {
            "elements": elements,
            "fps": 30,
            "width": 1080,
            "height": 1920,
            "safeArea": dict(SAFE),
            "durationInFrames": total,
        },
    }


class _StubRun:
    """Acts like a successful `npx remotion render`.

    Receives the whole argv (`post_bridge` calls `subprocess.run`
    directly - there is no renderer seam here), so the output path is
    argv[4] and the props path (which carries the canvas the step
    chose) is the value after `--props`.

    `draw` decides what lands there: a real ProRes 4444 clip at that
    canvas by default, or raw bytes where a test wants the render to
    succeed without real pixels.
    """

    def __init__(self, draw=True):
        self.calls = []
        self.draw = draw
        # Patching `post_bridge.subprocess.run` patches the module
        # object itself, so ffmpeg calls made anywhere - the pad, this
        # stub's own drawing - arrive here too. Only `npx` is stubbed.
        self.real_run = subprocess.run

    def __call__(self, *args, **kwargs):
        argv = args[0]
        if not argv or argv[0] != "npx":
            return self.real_run(*args, **kwargs)
        self.calls.append(argv)
        overlay_path = argv[4]
        if not self.draw:
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
        else:
            props = json.load(open(argv[argv.index("--props") + 1],
                                   encoding="utf-8"))
            _draw_clip(overlay_path, props["width"], props["height"],
                       props["durationInFrames"])

        class Done:
            returncode = 0
            stderr = ""
        return Done()


_REAL_RUN = subprocess.run


def _draw_clip(path, width, height, frames):
    """A transparent ProRes 4444 clip with one opaque block in it."""
    block_w, block_h = max(width // 2, 2), max(height // 2, 2)
    _REAL_RUN(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=black@0:s={width}x{height}:d=1:r=30,format=rgba",
         "-f", "lavfi",
         "-i", f"color=white:s={block_w}x{block_h}:d=1:r=30,format=rgba",
         "-filter_complex",
         f"[0][1]overlay={(width - block_w) // 2}:{(height - block_h) // 2},"
         f"format=rgba",
         "-frames:v", str(max(frames, 1)),
         "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", path],
        check=True,
    )
    return path


def _render(monkeypatch, planned, out_dir, draw=True, **kwargs):
    stub = _StubRun(draw=draw)
    monkeypatch.setattr(
        "library.steps.step_4_06_render_motion_graphics.post_bridge.subprocess.run",
        stub,
    )
    return render_one_segment(planned, out_dir, **kwargs), stub


def test_default_path_is_tight(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path))
    name = os.path.basename(out["overlay_path"])
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov", name), name
    assert "_tight" not in name
    assert out["geometry"] == "tight"
    assert out["tight_box"] is not None
    assert os.path.isfile(out["overlay_path"])


def test_explicit_full_path_is_unchanged(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="full")
    name = os.path.basename(out["overlay_path"])
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov", name), name
    assert "_tight" not in name
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert out["placement_label"] == "mg_000"
    assert os.path.isfile(out["overlay_path"])


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_renders_the_union_and_places_it_by_transform(
        tmp_path, monkeypatch):
    """The union is what Remotion DRAWS; the placement is what carries
    it. The record keeps the union's size and its Pan/Tilt, and the
    artefact on disk IS the small canvas - Resolve transforms it into
    place and reads the transform back."""
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="tight")
    name = os.path.basename(out["overlay_path"])
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov", name), name
    assert out["geometry"] == "tight"
    box = out["tight_box"]
    assert box["width"] < 1080 and box["height"] < 1920
    assert "origin" not in box
    placement = box["placement"]
    assert abs(placement["tilt"]) <= 3400
    stem = out["overlay_path"][:-len(".mov")]
    props_on_disk = json.load(open(stem + "_props.json", encoding="utf-8"))
    assert props_on_disk["width"] == box["width"]
    assert props_on_disk["height"] == box["height"]

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         out["overlay_path"]],
        capture_output=True, text=True, encoding="utf-8", check=True)
    assert probe.stdout.strip() == (
        f"{box['width']},{box['height']}"), (
        "the artefact Resolve transforms must BE the tight canvas, "
        "not the delivery frame")


def test_a_predicted_clamp_refusal_is_retried_from_pixels(
        tmp_path, monkeypatch):
    """The prediction is not the verdict: a graphic the predicted
    clamp gate refuses is rebound from its own rendered pixels, and a
    probe whose ink binds reaches the timeline as a verified tight
    crop rather than full canvas."""
    import library.tools.mg_tight_box as mg_tight_box
    from library.tools.tight_box import TightBoxMismatch

    def _refuse(*args, **kwargs):
        raise TightBoxMismatch("exceeds the rail")
    monkeypatch.setattr(mg_tight_box,
                        "tighten_motion_graphics_props_with_reason",
                        _refuse)
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="tight")
    assert out["geometry"] == "tight"
    assert out["tight_fallback"] == ""
    assert out["tight_box"] is not None
    assert out["overlay_path"].endswith("_tight.mov")
    assert out["tight_report"]["before"] == [1080, 1920]
    stem = out["overlay_path"][:-len(".mov")]
    sidecar = json.load(open(stem + "_tightness.json", encoding="utf-8"))
    assert sidecar["outcome"] == "tight"
    # The probe keeps its own account beside it: full canvas, with
    # the predicted refusal named - a reasoned full canvas, never a
    # silent one.
    probe_sidecar = json.load(
        open(stem[:-len("_tight")] + "_tightness.json",
             encoding="utf-8"))
    assert probe_sidecar["outcome"] == "full"
    assert probe_sidecar["reason"] == "placement_unholdable"
    assert probe_sidecar["element"] == "title_lockup"


def test_explicit_full_declares_itself_on_the_artefact(
        tmp_path, monkeypatch):
    """A project that declares full-canvas carrying never asks the
    tighten path - and the artefact still says why it is full canvas,
    so the build-time guard passes it by declaration, not by sight."""
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="full")
    assert out["geometry"] == "full"
    stem = out["overlay_path"][:-len(".mov")]
    sidecar = json.load(open(stem + "_tightness.json", encoding="utf-8"))
    assert sidecar["outcome"] == "full"
    assert sidecar["reason"] == "geometry_full_declared"
    from library.tools.mg_tight_box import check_motion_graphics_files
    errors, census = check_motion_graphics_files(
        [stem + "_props.json"], 1080, 1920)
    assert errors == []
    assert census["full_by_design"] == 1


def test_accents_stay_full_canvas_when_tight_is_asked(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("frame_accents")]),
                     str(tmp_path), overlay_geometry="tight")
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov",
                        os.path.basename(out["overlay_path"]))
    assert "_tight" not in out["overlay_path"]
    # The file IS full canvas, so the record says full: a structural
    # refusal that kept the requested "tight" is how full-canvas files
    # passed as tighten candidates. The refusal names itself, on the
    # record and on the artefact sidecar beside the props.
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "frame_accents_span_by_design" in out["tight_fallback"]
    stem = out["overlay_path"][:-len(".mov")]
    sidecar = json.load(open(stem + "_tightness.json", encoding="utf-8"))
    assert sidecar["outcome"] == "full"
    assert sidecar["reason"] == "frame_accents_span_by_design"
    assert sidecar["element"] == "frame_accents"
