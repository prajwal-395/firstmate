"""The overlay option: the frames container, and the tight geometry.

`render_one_segment` behind stub renderers, so what is pinned is the
unit's own decisions - names, records, reuse, refusal - without paying
for Remotion. Explicit full-canvas video is asserted unchanged: the
option adds, it does not move. The default is tight (since
2026-09-10); the tight render tests below serve canned decodable
renders at the constant canvas because a byte stub cannot feed the
edge guard.
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
    constant_caption_box,
)

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

# A known ink rectangle on the constant canvas, in (x0, y0, x1, y1).
RECT = (302, 200, 602, 280)
N_FRAMES = 30

# The constant canvas for the `_props` style (840px wrap): the number
# the structural bound derives, not a literal restated here.
CONSTANT_W = 904
CONSTANT_H = 480

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
    a directory with one PNG per rendered frame.

    Sequence frames are drawn at the canvas the props promise - a real
    renderer draws what it was asked - so the guard reads frames of
    the size the box derived. They draw nothing, which is what sends
    a tight attempt down the draws-nothing full fallback."""

    def __init__(self, frames=60):
        self.frames = frames
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append((props_path, overlay_path, sequence))
        if sequence:
            try:
                with open(props_path) as handle:
                    asked = json.load(handle)
                size = (int(asked.get("width", 8)),
                        int(asked.get("height", 8)))
            except (OSError, ValueError, TypeError):
                size = (8, 8)
            os.makedirs(overlay_path, exist_ok=True)
            from PIL import Image
            for i in range(self.frames):
                Image.new("RGBA", size, (255, 255, 255, 0)).save(
                    os.path.join(overlay_path, f"frame-{i:02d}.png"))
        else:
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
        return True, ""

    def close(self):
        pass


class _ServingRenderer:
    """Serves one canned render per call, in order.

    The tight path renders its constant canvas natively - exactly one
    engine call per carrying - so there is no probe to tell apart from
    a main render. A test needing two different files (the edge-touch
    fallback) passes two sources; every other test passes one.
    """

    def __init__(self, *sources):
        self.sources = list(sources)
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append((props_path, overlay_path, sequence))
        index = min(len(self.calls) - 1, len(self.sources) - 1)
        src = self.sources[index]
        if sequence:
            shutil.copytree(src, overlay_path, dirs_exist_ok=True)
        else:
            shutil.copy(src, overlay_path)
        return True, ""

    def close(self):
        pass


def _small_mov(path, rect=RECT, size=(CONSTANT_W, CONSTANT_H),
               frames=N_FRAMES):
    """A synthetic constant-canvas render: transparent frame, opaque
    rect. The step's edge guard reads this file's own alpha plane, so
    the canned render has to be decodable, not bytes.

    `format=rgba` lives INSIDE each lavfi source: without it the
    transparent base arrives opaque (measured 2026-09-09 - the PNG
    muxer settles on yuv and alpha is lost before the overlay runs).
    """
    w, h = size
    x0, y0, x1, y1 = rect
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=black@0:s={w}x{h}:d=1:r=30,format=rgba",
         "-f", "lavfi",
         "-i", f"color=white:s={x1 - x0}x{y1 - y0}:d=1:r=30,format=rgba",
         "-filter_complex",
         f"[0][1]overlay={x0}:{y0},format=rgba",
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path],
        check=True,
    )
    return path


def _blank_mov(path, size=(CONSTANT_W, CONSTANT_H), frames=N_FRAMES):
    """A fully transparent constant-canvas render: nothing to bound."""
    w, h = size
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=black@0:s={w}x{h}:d=1:r=30,format=rgba",
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path],
        check=True,
    )
    return path


def _rect_frames(d, size, rect, n):
    """Constant-canvas frames with a known ink rectangle, PIL only."""
    from PIL import Image, ImageDraw
    os.makedirs(d, exist_ok=True)
    x0, y0, x1, y1 = rect
    for i in range(n):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle([x0, y0, x1 - 1, y1 - 1],
                                      fill=(255, 255, 255, 255))
        img.save(os.path.join(d, f"frame-{i:02d}.png"))
    return d


def _small_frames_setup(tmp_path, name, n=5, rect=RECT):
    """A canned constant-canvas sequence, box included.

    The frames ARE the artefact the step guards - the step renders
    its constant canvas natively and reads the PNGs' own alpha plane
    - so the expectation is derived the way the step derives it, from
    the props, never measured off the pixels.
    """
    props = _props_frames(_props(), n)
    tight_canned = _rect_frames(os.path.join(str(tmp_path), f"{name}-tight"),
                                (CONSTANT_W, CONSTANT_H), rect, n)
    box = constant_caption_box(props)
    assert box is not None
    return props, tight_canned, box


def _props_frames(props, n):
    props = dict(props)
    props["durationInFrames"] = n
    props["_source_out_frame"] = n
    return props


def _probe_leftovers(out_dir):
    return [n for n in os.listdir(out_dir) if n.startswith("probe_")]


def test_explicit_full_path_is_unchanged(tmp_path):
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=_StubRenderer(),
                             overlay_geometry="full")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith(".mov")
    assert "_tight" not in os.path.basename(out["overlay_path"])
    assert out["geometry"] == "full"
    assert out["container"] == "video"
    assert out["tight_box"] is None
    assert out["tight_fallback"] == ""
    assert out["frames"] is None




def test_incomplete_sequence_is_a_failure_not_a_render(tmp_path):
    stub = _StubRenderer(frames=3)
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_container="frames")
    assert out["provenance"] == FAILED
    assert "sequence" in out["failure"]


def test_tight_video_renders_natively_at_the_constant_canvas(tmp_path):
    """The tight output is RENDERED at the constant canvas - no probe,
    no crop, no second render: the predictor sizes nothing that
    reaches a timeline, and the probe reaches nothing either. Tight
    and full carryings never share a filename: the content digest
    carries the geometry."""
    props = _props_frames(_props(), N_FRAMES)
    box = constant_caption_box(props)
    assert box is not None
    tight_mov = _small_mov(os.path.join(str(tmp_path), "tight.mov"))
    stub = _ServingRenderer(tight_mov)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith(".mov")
    assert out["geometry"] == "tight"
    assert out["container"] == "video"
    assert out["frames"] is None
    box_out = out["tight_box"]
    assert box_out["width"] == CONSTANT_W == box.width
    assert box_out["height"] == CONSTANT_H == box.height
    assert box_out["width"] < 1080 and box_out["height"] < 1920
    assert box_out["placement"] == box.placement
    assert box_out["placement"]["scaling"] == 1
    # Arithmetic, not correspondence: the centred canvas sits centred.
    assert box_out["placement"]["pan"] == 0
    props_file = out["overlay_path"][:-len(".mov")] + "_props.json"
    with open(props_file) as handle:
        props_on_disk = json.load(handle)
    assert props_on_disk["width"] == CONSTANT_W
    assert props_on_disk["height"] == CONSTANT_H
    sidecar_file = out["overlay_path"][:-len(".mov")] + "_box.json"
    with open(sidecar_file) as handle:
        sidecar = json.load(handle)
    assert sidecar["placement"] == box_out["placement"]
    assert sidecar["edge_guard"]["touches_edge"] is False
    # THE guard: exactly one engine render. A second render call is
    # the probe this path exists to delete.
    assert stub.calls and len(stub.calls) == 1
    assert _probe_leftovers(str(tmp_path)) == []

    from library.tools.overlay_carriage import (
        OVERLAY_VIDEO_CODEC,
        probe_overlay,
    )

    # The codec survives the rewrite: Remotion writes ProRes, and the
    # transcode the crop used to pay for free now runs here.
    assert probe_overlay(out["overlay_path"])["codec_name"] == \
        OVERLAY_VIDEO_CODEC


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_video_edge_touch_falls_back_to_full_canvas(tmp_path):
    """Ink on the canvas edge is clipped pixels no repositioning can
    recover, so the card falls back to full canvas - re-rendered,
    because there is no probe to copy any more - and the run still
    says which card lost its tight carriage and why.

    Forced here with a canned render whose ink touches the edge (a
    native render that fits never does); the fallback file is carried
    into the overlay codec like every other artefact.
    """
    props = _props_frames(_props(), N_FRAMES)
    edge_mov = _small_mov(os.path.join(str(tmp_path), "edge.mov"),
                          rect=(302, 200, CONSTANT_W, 280))
    full_mov = _small_mov(os.path.join(str(tmp_path), "full.mov"),
                          rect=RECT, size=(1080, 1920))
    stub = _ServingRenderer(edge_mov, full_mov)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "canvas edge" in out["tight_fallback"]
    assert out["overlay_path"].endswith(".mov")
    assert os.path.isfile(out["overlay_path"])

    from library.tools.overlay_carriage import (
        OVERLAY_VIDEO_CODEC,
        probe_overlay,
    )

    assert probe_overlay(out["overlay_path"])["codec_name"] == \
        OVERLAY_VIDEO_CODEC
    # Two renders: the tight one that touched the edge, and its full
    # replacement. No probe anywhere.
    assert stub.calls and len(stub.calls) == 2
    assert _probe_leftovers(str(tmp_path)) == []


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_render_drawing_nothing_records_full_geometry(tmp_path):
    """A card with nothing to bound is full canvas IN THE RECORD too.

    The file was always full canvas; the entry used to keep saying
    "tight" with no box, pinning a full-canvas file as a tight one in
    the only record a staging render leaves."""
    props = _props_frames(_props(), N_FRAMES)
    blank = _blank_mov(os.path.join(str(tmp_path), "blank.mov"))
    full_mov = _small_mov(os.path.join(str(tmp_path), "full.mov"),
                          rect=RECT, size=(1080, 1920))
    stub = _ServingRenderer(blank, full_mov)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "draws nothing" in out["tight_fallback"]
    assert out["overlay_path"].endswith(".mov")
    assert os.path.isfile(out["overlay_path"])
    assert _probe_leftovers(str(tmp_path)) == []


def test_tight_frames_records_dir_and_placement(tmp_path):
    props, tight_canned, box = _small_frames_setup(tmp_path, "frames")
    stub = _ServingRenderer(tight_canned)
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
    assert frames["dir"].endswith("_frames")
    assert (len([n for n in os.listdir(frames["dir"])
                 if n.endswith(".png")]) == 5)
    assert _probe_leftovers(str(tmp_path)) == []


def test_reuse_skips_identical_tight_frames(tmp_path):
    # The reuse key fingerprints the renderer tree; a directory
    # without one yields no key and reuse is refused - so this test
    # renders against the real composition tree, like the caption
    # reuse tests do. Placement on the second call is restored from
    # the box sidecar: no render runs at all.
    remotion = os.path.join(PROJECT_ROOT, "remotion-subtitles")
    props, tight_canned, _ = _small_frames_setup(tmp_path, "reuse")
    stub = _ServingRenderer(tight_canned)
    first = render_one_segment(
        props, str(tmp_path), "tl", remotion_dir=remotion,
        renderer=stub, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert first["provenance"] == RENDERED
    assert len(stub.calls) == 1
    fresh = _ServingRenderer(tight_canned)
    second = render_one_segment(
        props, str(tmp_path), "tl", remotion_dir=remotion,
        renderer=fresh, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert second["provenance"] == REUSED
    assert fresh.calls == []
    assert (second["tight_box"]["placement"]
            == first["tight_box"]["placement"])


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


def test_frame_qa_refuses_a_blank_sequence(tmp_path):
    d = _frame_dir(tmp_path, "blank.frames", [[], []])
    with pytest.raises(RuntimeError, match="draws nothing"):
        _qa_frame_sequence({"frames": {"dir": d, "count": 2}})


def test_frame_qa_refuses_edge_clipped_ink(tmp_path):
    ink = [(x, y) for x in range(40) for y in range(35, 45)]
    d = _frame_dir(tmp_path, "clip.frames", [ink, ink])
    with pytest.raises(RuntimeError, match="edge"):
        _qa_frame_sequence({"frames": {"dir": d, "count": 2}})
