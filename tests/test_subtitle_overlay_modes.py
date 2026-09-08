"""The overlay option: tight geometry, frames container, or both.

`render_one_segment` behind a stub renderer, so what is pinned is the
unit's own decisions - names, records, reuse, refusal - without paying
for Remotion. The default path (full-canvas video) is asserted
unchanged first: the option adds, it does not move.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (  # noqa: E402
    FAILED,
    RENDERED,
    REUSED,
    _qa_frame_sequence,
    render_one_segment,
)

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


def test_default_path_is_unchanged(tmp_path):
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=_StubRenderer())
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith(".mov")
    assert "_tight" not in out["overlay_path"]
    assert out["geometry"] == "full"
    assert out["container"] == "video"
    assert out["tight_box"] is None
    assert out["frames"] is None


def test_tight_video_renders_beside_not_over(tmp_path):
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=_StubRenderer(),
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith("_tight.mov")
    assert out["geometry"] == "tight"
    assert out["container"] == "video"
    assert out["frames"] is None
    box = out["tight_box"]
    assert box["width"] < 1080 and box["height"] < 1920
    assert box["placement"]["scaling"] == 1
    props_on_disk = json.load(open(
        os.path.join(str(tmp_path),
                     os.path.basename(out["overlay_path"]).replace(
                         "_tight.mov", "_tight_props.json"))))
    assert props_on_disk["width"] == box["width"]
    assert props_on_disk["height"] == box["height"]


def test_tight_frames_records_dir_and_placement(tmp_path):
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=_StubRenderer(),
                             overlay_geometry="tight",
                             overlay_container="frames")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"] == ""
    assert out["geometry"] == "tight"
    assert out["container"] == "frames"
    assert out["tight_box"]["placement"]["scaling"] == 1
    frames = out["frames"]
    assert frames["count"] == 60
    assert os.path.isdir(frames["dir"])
    assert frames["dir"].endswith("_tight_frames")
    assert (len([n for n in os.listdir(frames["dir"])
                 if n.endswith(".png")]) == 60)


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
    # reuse tests do.
    remotion = os.path.join(PROJECT_ROOT, "remotion-subtitles")
    stub = _StubRenderer()
    first = render_one_segment(
        _props(), str(tmp_path), "tl", remotion_dir=remotion,
        renderer=stub, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert first["provenance"] == RENDERED
    second = render_one_segment(
        _props(), str(tmp_path), "tl", remotion_dir=remotion,
        renderer=stub, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert second["provenance"] == REUSED
    assert len(stub.calls) == 1


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
    ink = [(x, y) for x in range(0, 40) for y in range(35, 45)]
    d = _frame_dir(tmp_path, "clip.frames", [ink, ink])
    with pytest.raises(RuntimeError, match="edge"):
        _qa_frame_sequence({"frames": {"dir": d, "count": 2}})
