"""4.06 dispatches one render, and the log names the engine that drew it.

The production bug (census G7): with FFrames selected,
`render_one_segment` ran the FFrames render, fell through into the
Remotion render over the same `frames_dir`, and shipped Remotion
pixels while logging "engine: FFrames" - every MotionGraphics card
paid both renders and the FFrames lowering was dead code.

The guard (census S3): FFrames draws four arms
(`MG_FFRAMES_COVERED_ARMS`). A card naming any other arm, or a
`channel_bug` whose image has no readable geometry, is routed to the
existing Remotion render explicitly, with the reason on stderr - never
a blank, never a silent skip.

Each test below stubs only the two renderer entry points and counts
calls: covered arms render once through FFrames; uncovered arms fall
back once through Remotion with the reason; nothing renders twice.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import library.steps.step_4_06_render_motion_graphics.post_bridge as pb
from library.tools.graphics_renderer import USER_SETTING_KEY

SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _title_el(**kw):
    element = {
        "element": "title_lockup",
        "anchor": "top_left",
        "row": 0,
        "runs": [{"text": "DAY 001", "type_role": "display"}],
        "color": "#F5F5F0",
        "entrance": "cut",
        "exit": "cut",
        "startFrame": 0,
        "durationFrames": 12,
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": 0.5,
        "footprint": 1.0,
        "emphasis": None,
    }
    element.update(kw)
    return element


def _uncovered_el():
    return {
        "element": "list_build",
        "anchor": "centre",
        "row": 0,
        "runs": [{"text": "one", "type_role": "supporting"}],
        "color": "#FFDD55",
        "entrance": "cut",
        "exit": "cut",
        "startFrame": 0,
        "durationFrames": 12,
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": 0.5,
        "footprint": None,
        "emphasis": None,
    }


def _bug_el(asset):
    return {
        "element": "channel_bug",
        "anchor": "top_right",
        "row": 0,
        "runs": [],
        "color": "#FFFFFF",
        "entrance": "cut",
        "exit": "cut",
        "startFrame": 0,
        "durationFrames": 12,
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": 0.5,
        "footprint": 0.15,
        "emphasis": None,
        "asset": asset,
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


def _identical_frames(frames_dir, count, pattern):
    """Identical opaque frames, so the segment is kept as one still."""
    from PIL import Image

    os.makedirs(frames_dir, exist_ok=True)
    frame = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
    for index in range(1, count + 1):
        frame.save(os.path.join(frames_dir, pattern % index))


class _Calls:
    def __init__(self):
        self.fframes = []
        self.remotion = []


def _stub_engines(monkeypatch):
    """Count renders at the two engine entry points, drawing canned ink."""
    calls = _Calls()

    def fake_fframes(
        composition, props, frames_dir, work_dir, project_folder="", repo_root=None
    ):
        calls.fframes.append(composition)
        _identical_frames(frames_dir, int(props["durationInFrames"]), "frame_%06d.png")
        return {
            "composition": composition,
            "frames_dir": frames_dir,
            "frames": [],
            "frame_count": 0,
            "engine": "fframes",
        }

    def fake_remotion(props_path, dest_path, remotion, name):
        calls.remotion.append(name)
        with open(props_path, encoding="utf-8") as handle:
            props = json.load(handle)
        _identical_frames(dest_path, int(props["durationInFrames"]), "frame-%06d.png")
        return True

    monkeypatch.setenv(USER_SETTING_KEY, "fframes")
    monkeypatch.setattr(
        "library.tools.fframes_render.render_one_card_frames", fake_fframes
    )
    monkeypatch.setattr(pb, "_render_motion_graphics_file", fake_remotion)
    return calls


def _render(monkeypatch, tmp_path, planned):
    calls = _stub_engines(monkeypatch)
    out = pb.render_one_segment(
        planned, str(tmp_path), overlay_geometry="full", project_folder=""
    )
    assert out is not None, "the render refused a drawable card"
    assert os.path.isfile(out["overlay_path"])
    return out, calls


def test_covered_arm_renders_once_through_fframes(tmp_path, monkeypatch, capsys):
    out, calls = _render(monkeypatch, tmp_path, _planned([_title_el()]))
    assert len(calls.fframes) == 1
    assert calls.remotion == []
    err = capsys.readouterr().err
    assert "engine: FFrames" in err
    assert "engine: Remotion" not in err
    assert out["media_type"] == "still"


def test_uncovered_arm_falls_back_with_the_reason(tmp_path, monkeypatch, capsys):
    out, calls = _render(monkeypatch, tmp_path, _planned([_uncovered_el()]))
    assert calls.fframes == []
    assert len(calls.remotion) == 1
    err = capsys.readouterr().err
    assert "engine: Remotion" in err
    assert "list_build" in err
    assert "no FFrames lowering" in err
    assert out["media_type"] == "still"


def test_channel_bug_renders_through_fframes_with_bounds(tmp_path, monkeypatch, capsys):
    from PIL import Image

    asset = tmp_path / "bug.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(asset)
    _, calls = _render(monkeypatch, tmp_path, _planned([_bug_el(str(asset))]))
    assert len(calls.fframes) == 1
    assert calls.remotion == []
    assert "engine: FFrames" in capsys.readouterr().err

    from library.graphics_ir import lowering

    scene = lowering.lower_motion_graphics(
        {
            "fps": 30,
            "width": 1080,
            "height": 1920,
            "durationInFrames": 12,
            "safeArea": dict(SAFE),
            "elements": [_bug_el(str(asset))],
        }
    )
    from library.graphics_ir.model import NodeType

    images = [node for node in scene.nodes if node.type == NodeType.IMAGE]
    assert len(images) == 1
    bounds = images[0].bounds
    assert bounds is not None, "channel_bug must carry bounds (census G3)"
    usable = 1080 - SAFE["left"] - SAFE["right"]
    assert bounds.width == round(usable * 0.15)
    assert bounds.height == bounds.width / 2.0
    assert bounds.x == 1080 - SAFE["right"] - bounds.width
    assert bounds.y == SAFE["top"]


def test_channel_bug_without_readable_asset_falls_back(tmp_path, monkeypatch, capsys):
    out, calls = _render(
        monkeypatch, tmp_path, _planned([_bug_el(str(tmp_path / "no_such_bug.png"))])
    )
    assert calls.fframes == []
    assert len(calls.remotion) == 1
    err = capsys.readouterr().err
    assert "engine: Remotion" in err
    assert out["media_type"] == "still"
