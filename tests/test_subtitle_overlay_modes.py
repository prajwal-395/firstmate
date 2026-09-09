"""The overlay option: tight geometry, frames container, or both.

`render_one_segment` behind a stub renderer, so what is pinned is the
unit's own decisions - names, records, reuse, refusal - without paying
for Remotion. The default path (full-canvas video) is asserted
unchanged first: the option adds, it does not move.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (
    FAILED,
    RENDERED,
    REUSED,
    _qa_frame_sequence,
    render_one_segment,
)
from library.tools.tight_box import (
    canvas_offset,
    extract_frames,
    ink_union_of_frames,
    tighten_measured,
)

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

# A known ink rectangle on the full delivery frame, in (x0, y0, x1, y1).
RECT = (100, 1450, 800, 1570)
N_FRAMES = 30

SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _props():
    return {
        "_block_position": 1,
        "_timeline_start": 0.0,
        "_timeline_end": 2.0,
        "_source_in_frame": 0,
        "_source_out_frame": 60,
        "_speaker": None,
        "_source_clip_id": "clip_001",
        "_source_start": 10.0,
        "_source_end": 12.0,
        "durationInFrames": 60,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "style": {
            "fontFamily": "Montserrat",
            "fontSize": 58,
            "fontWeight": 800,
            "fontColor": "#FFFFFF",
            "accentColor": "#FBF0B8",
            "outlineColor": "#000000",
            "outlineWidth": 4,
            "position": "bottom",
            "safeArea": dict(SAFE),
            "captionMaxWidth": 840,
        },
        "subtitles": [{
            "text": "and so my very",
            "startFrame": 0,
            "endFrame": 21,
            "emphasisWords": [],
            "words": [
                {"word": "and", "startFrame": 0, "endFrame": 5},
                {"word": "so", "startFrame": 5, "endFrame": 9},
                {"word": "my", "startFrame": 9, "endFrame": 13},
                {"word": "very,", "startFrame": 13, "endFrame": 21},
            ],
            "fitScale": 1.0,
        }],
    }


class _StubRenderer:
    """Acts like the CLI renderer: video writes a file, sequence fills
    a directory with one PNG per rendered frame."""

    def __init__(self, frames=60):
        self.frames = frames
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append((props_path, overlay_path, sequence))
        if sequence:
            os.makedirs(overlay_path, exist_ok=True)
            from PIL import Image
            for i in range(self.frames):
                Image.new("RGBA", (8, 8), (255, 255, 255, 0)).save(
                    os.path.join(overlay_path, f"frame-{i:02d}.png"))
        else:
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
        return True, ""

    def close(self):
        pass


class _ServingRenderer:
    """Serves canned renders: the probe gets the full-canvas file, the
    tight render gets the cropped one. Tells them apart by the probe's
    temp path, which always carries the `probe_` prefix."""

    def __init__(self, probe_src, tight_src):
        self.probe_src = probe_src
        self.tight_src = tight_src
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append((props_path, overlay_path, sequence))
        src = self.probe_src if "probe_" in overlay_path else self.tight_src
        if sequence:
            shutil.copytree(src, overlay_path, dirs_exist_ok=True)
        else:
            shutil.copy(src, overlay_path)
        return True, ""

    def close(self):
        pass


def _full_mov(path, rect=RECT, frames=N_FRAMES):
    """A synthetic full-canvas probe: transparent frame, opaque rect.

    `format=rgba` lives INSIDE each lavfi source: without it the
    transparent base arrives opaque (measured 2026-09-09 - the PNG
    muxer settles on yuv and alpha is lost before the overlay runs).
    """
    x0, y0, x1, y1 = rect
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black@0:s=1080x1920:d=1:r=30,format=rgba",
         "-f", "lavfi",
         "-i", f"color=white:s={x1 - x0}x{y1 - y0}:d=1:r=30,format=rgba",
         "-filter_complex",
         f"[0][1]overlay={x0}:{y0},format=rgba",
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path],
        check=True,
    )
    return path


def _measure_full_mov(full_mov, props):
    """What the step measures off the probe: union, box, origin."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        union = ink_union_of_frames(extract_frames(full_mov, tmp))
    assert union is not None
    return union, tighten_measured(props, union)


def _crop_mov(full_mov, tight_mov, box):
    ox, oy = canvas_offset(box)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", full_mov,
         "-vf", f"crop={box.width}:{box.height}:{ox}:{oy}",
         "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", tight_mov],
        check=True,
    )
    return tight_mov


def _props_frames(props, n):
    props = dict(props)
    props["durationInFrames"] = n
    props["_source_out_frame"] = n
    return props


def _probe_leftovers(out_dir):
    return [n for n in os.listdir(out_dir) if n.startswith("probe_")]


def test_default_path_is_unchanged(tmp_path):
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=_StubRenderer())
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith(".mov")
    assert "_tight" not in os.path.basename(out["overlay_path"])
    assert out["geometry"] == "full"
    assert out["container"] == "video"
    assert out["tight_box"] is None
    assert out["frames"] is None


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_video_renders_beside_not_over(tmp_path):
    """The box is measured off a decoded probe and the tight output is
    verified against it before it is kept - the predictor sizes
    nothing that reaches a timeline."""
    props = _props_frames(_props(), N_FRAMES)
    full_mov = _full_mov(os.path.join(str(tmp_path), "full.mov"))
    _, box = _measure_full_mov(full_mov, props)
    tight_mov = _crop_mov(full_mov,
                          os.path.join(str(tmp_path), "tight.mov"), box)
    stub = _ServingRenderer(full_mov, tight_mov)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith("_tight.mov")
    assert out["geometry"] == "tight"
    assert out["container"] == "video"
    assert out["frames"] is None
    box_out = out["tight_box"]
    assert box_out["width"] == box.width
    assert box_out["height"] == box.height
    assert box_out["width"] < 1080 and box_out["height"] < 1920
    assert box_out["placement"]["scaling"] == 1
    props_file = os.path.join(
        str(tmp_path),
        os.path.basename(out["overlay_path"]).replace(
            "_tight.mov", "_tight_props.json"))
    with open(props_file) as handle:
        props_on_disk = json.load(handle)
    assert props_on_disk["width"] == box.width
    assert props_on_disk["height"] == box.height
    sidecar_file = os.path.join(
        str(tmp_path),
        os.path.basename(out["overlay_path"]).replace(
            "_tight.mov", "_tight_box.json"))
    with open(sidecar_file) as handle:
        sidecar = json.load(handle)
    assert sidecar["placement"] == box_out["placement"]
    assert stub.calls and len(stub.calls) == 2
    assert _probe_leftovers(str(tmp_path)) == []


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_video_mismatch_falls_back_to_full_canvas(tmp_path):
    """A tight output that is not the probe crop is carried full canvas.

    The gate still holds - the mistightened file is discarded and never
    reaches a timeline - but the segment is not failed: the same props
    drawn on the full canvas are the same pixels on screen, so the card
    is re-rendered full with the reason recorded on it (ratified in
    project.yaml: carried full canvas instead, by name, gate not
    widened)."""
    props = _props_frames(_props(), N_FRAMES)
    full_mov = _full_mov(os.path.join(str(tmp_path), "full.mov"))
    _, box = _measure_full_mov(full_mov, props)
    ox, oy = canvas_offset(box)
    shifted = os.path.join(str(tmp_path), "shifted.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", full_mov,
         "-vf", f"crop={box.width}:{box.height}:{ox + 40}:{oy}",
         "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", shifted],
        check=True,
    )
    stub = _ServingRenderer(full_mov, shifted)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "probe crop" in out["tight_fallback"]
    assert out["overlay_path"].endswith(".mov")
    assert "_tight" not in os.path.basename(out["overlay_path"])
    assert os.path.isfile(out["overlay_path"])
    assert not os.path.exists(
        out["overlay_path"].replace(".mov", "_tight.mov"))
    assert _probe_leftovers(str(tmp_path)) == []


def _blank_mov(path, frames=N_FRAMES):
    """A fully transparent full-canvas probe: nothing to bound."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black@0:s=1080x1920:d=1:r=30,format=rgba",
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path],
        check=True,
    )
    return path


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_probe_drawing_nothing_records_full_geometry(tmp_path):
    """A card with nothing to bound is full canvas IN THE RECORD too.

    The file was always full canvas; the entry used to keep saying
    "tight" with no box, pinning a full-canvas file as a tight one in
    the only record a staging render leaves."""
    props = _props_frames(_props(), N_FRAMES)
    blank = _blank_mov(os.path.join(str(tmp_path), "blank.mov"))
    stub = _ServingRenderer(blank, blank)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "draws nothing" in out["tight_fallback"]
    assert out["overlay_path"].endswith(".mov")
    assert "_tight" not in os.path.basename(out["overlay_path"])
    assert os.path.isfile(out["overlay_path"])
    assert _probe_leftovers(str(tmp_path)) == []


def _rect_frames(d, size, rect, n):
    """Full-canvas probe frames with a known ink rectangle, PIL only."""
    from PIL import Image, ImageDraw
    os.makedirs(d, exist_ok=True)
    x0, y0, x1, y1 = rect
    for i in range(n):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle([x0, y0, x1 - 1, y1 - 1],
                                      fill=(255, 255, 255, 255))
        img.save(os.path.join(d, f"frame-{i:02d}.png"))
    return d


def _crop_frames(src_dir, dest_dir, box):
    """The exact tight counterpart of a probe sequence, PIL only."""
    from PIL import Image
    os.makedirs(dest_dir, exist_ok=True)
    ox, oy = canvas_offset(box)
    names = sorted(n for n in os.listdir(src_dir) if n.endswith(".png"))
    for name in names:
        with Image.open(os.path.join(src_dir, name)) as im:
            im.crop((ox, oy, ox + box.width, oy + box.height)).save(
                os.path.join(dest_dir, name))
    return dest_dir, names


def _measured_frames_setup(tmp_path, name, n=5):
    """A probe sequence plus its exact tight crop, box included."""
    props = _props_frames(_props(), n)
    probe_canned = _rect_frames(os.path.join(str(tmp_path), f"{name}-probe"),
                                (1080, 1920), RECT, n)
    paths = sorted(os.path.join(probe_canned, f)
                   for f in os.listdir(probe_canned))
    box = tighten_measured(props, ink_union_of_frames(paths))
    tight_canned, _ = _crop_frames(
        probe_canned, os.path.join(str(tmp_path), f"{name}-tight"), box)
    return props, probe_canned, tight_canned, box


def test_tight_frames_records_dir_and_placement(tmp_path):
    props, probe_canned, tight_canned, box = _measured_frames_setup(
        tmp_path, "frames")
    stub = _ServingRenderer(probe_canned, tight_canned)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight",
                             overlay_container="frames")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"] == ""
    assert out["geometry"] == "tight"
    assert out["container"] == "frames"
    assert out["tight_box"]["width"] == box.width
    assert out["tight_box"]["placement"]["scaling"] == 1
    frames = out["frames"]
    assert frames["count"] == 5
    assert os.path.isdir(frames["dir"])
    assert frames["dir"].endswith("_tight_frames")
    assert (len([n for n in os.listdir(frames["dir"])
                 if n.endswith(".png")]) == 5)
    assert _probe_leftovers(str(tmp_path)) == []


def test_full_frames_keeps_full_geometry(tmp_path):
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=_StubRenderer(),
                             overlay_container="frames")
    assert out["provenance"] == RENDERED
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert out["frames"]["count"] == 60
    assert out["frames"]["dir"].endswith("_frames")
    assert not out["frames"]["dir"].endswith("_tight_frames")


def test_incomplete_sequence_is_a_failure_not_a_render(tmp_path):
    stub = _StubRenderer(frames=3)
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_container="frames")
    assert out["provenance"] == FAILED
    assert "sequence" in out["failure"]


def test_unknown_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="overlay_geometry"):
        render_one_segment(_props(), str(tmp_path), "tl",
                           remotion_dir="/none",
                           renderer=_StubRenderer(),
                           overlay_geometry="small")
    with pytest.raises(ValueError, match="overlay_container"):
        render_one_segment(_props(), str(tmp_path), "tl",
                           remotion_dir="/none",
                           renderer=_StubRenderer(),
                           overlay_container="gif")


def test_reuse_skips_identical_tight_frames(tmp_path):
    # The reuse key fingerprints the renderer tree; a directory
    # without one yields no key and reuse is refused - so this test
    # renders against the real composition tree, like the caption
    # reuse tests do. Placement on the second call is restored from
    # the box sidecar: no probe render runs.
    remotion = os.path.join(PROJECT_ROOT, "remotion-subtitles")
    props, probe_canned, tight_canned, _ = _measured_frames_setup(
        tmp_path, "reuse")
    stub = _ServingRenderer(probe_canned, tight_canned)
    first = render_one_segment(
        props, str(tmp_path), "tl", remotion_dir=remotion,
        renderer=stub, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert first["provenance"] == RENDERED
    assert len(stub.calls) == 2
    fresh = _ServingRenderer(probe_canned, tight_canned)
    second = render_one_segment(
        props, str(tmp_path), "tl", remotion_dir=remotion,
        renderer=fresh, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert second["provenance"] == REUSED
    assert fresh.calls == []
    assert (second["tight_box"]["placement"]
            == first["tight_box"]["placement"])


def _frame_dir(tmp_path, name, draw):
    from PIL import Image
    d = os.path.join(str(tmp_path), name)
    os.makedirs(d, exist_ok=True)
    for i, pixels in enumerate(draw):
        img = Image.new("RGBA", (100, 60), (0, 0, 0, 0))
        for x, y in pixels:
            img.putpixel((x, y), (255, 255, 255, 255))
        img.save(os.path.join(d, f"frame-{i:02d}.png"))
    return d


def test_frame_qa_passes_drawn_frames(tmp_path):
    ink = [(x, y) for x in range(30, 70) for y in range(35, 45)]
    d = _frame_dir(tmp_path, "ok.frames", [ink, ink])
    _qa_frame_sequence({"frames": {"dir": d, "count": 2}})


def test_frame_qa_refuses_a_blank_sequence(tmp_path):
    d = _frame_dir(tmp_path, "blank.frames", [[], []])
    with pytest.raises(RuntimeError, match="draws nothing"):
        _qa_frame_sequence({"frames": {"dir": d, "count": 2}})


def test_frame_qa_refuses_edge_clipped_ink(tmp_path):
    ink = [(x, y) for x in range(40) for y in range(35, 45)]
    d = _frame_dir(tmp_path, "clip.frames", [ink, ink])
    with pytest.raises(RuntimeError, match="edge"):
        _qa_frame_sequence({"frames": {"dir": d, "count": 2}})
