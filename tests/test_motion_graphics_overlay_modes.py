"""The motion-graphics carrying option: full canvas or the drawn union.

`render_one_segment` behind a stubbed `subprocess.run`, so what is
pinned is the unit's own decisions - names, records, fallback - without
paying for Remotion. The default path (full-canvas video) is asserted
unchanged first: the option adds, it does not move.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.post_bridge import (  # noqa: E402
    render_one_segment,
)

SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _el(element, anchor="bottom_centre", duration=60):
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
    """Acts like a successful `npx remotion render`: writes the file.

    Receives the whole argv (`post_bridge` calls `subprocess.run`
    directly - there is no renderer seam here), so the output path is
    argv[4], after `render <composition>`.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args[0])
        overlay_path = args[0][4]
        with open(overlay_path, "wb") as handle:
            handle.write(b"pixels")

        class Done:
            returncode = 0
            stderr = ""
        return Done()


def _render(monkeypatch, planned, out_dir, **kwargs):
    stub = _StubRun()
    monkeypatch.setattr(
        "library.steps.step_4_06_render_motion_graphics.post_bridge.subprocess.run",
        stub,
    )
    return render_one_segment(planned, out_dir, **kwargs), stub


def test_default_path_is_unchanged(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path))
    assert out["overlay_path"].endswith("mg_000.mov")
    assert "_tight" not in out["overlay_path"]
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert os.path.isfile(out["overlay_path"])


def test_tight_video_renders_beside_not_over(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="tight")
    assert out["overlay_path"].endswith("mg_000_tight.mov")
    assert out["geometry"] == "tight"
    box = out["tight_box"]
    assert box["width"] < 1080 and box["height"] < 1920
    assert box["placement"]["scaling"] == 1
    assert os.path.isfile(out["overlay_path"])
    props_on_disk = json.load(open(
        os.path.join(str(tmp_path), "mg_000_tight_props.json"),
        encoding="utf-8"))
    assert props_on_disk["width"] == box["width"]
    assert props_on_disk["height"] == box["height"]


def test_tight_props_keep_the_planned_elements(tmp_path, monkeypatch):
    planned = _planned([_el("title_lockup")])
    out, _ = _render(monkeypatch, planned, str(tmp_path),
                     overlay_geometry="tight")
    props_on_disk = json.load(open(
        os.path.join(str(tmp_path), "mg_000_tight_props.json"),
        encoding="utf-8"))
    assert props_on_disk["elements"] == planned["props"]["elements"]
    assert (props_on_disk["durationInFrames"]
            == planned["props"]["durationInFrames"])


def test_accents_stay_full_canvas_when_tight_is_asked(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("frame_accents")]),
                     str(tmp_path), overlay_geometry="tight")
    assert out["overlay_path"].endswith("mg_000.mov")
    assert "_tight" not in out["overlay_path"]
    assert out["geometry"] == "tight"
    assert out["tight_box"] is None


def test_unknown_mode_is_refused(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="overlay_geometry"):
        _render(monkeypatch, _planned([_el("title_lockup")]),
                str(tmp_path), overlay_geometry="small")


def test_project_declaration_selects_tight(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.yaml").write_text(
        "name: t\nslug: t\npipeline:\n"
        "  motion_graphics_overlay_geometry: tight\n",
        encoding="utf-8")
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), project_folder=str(root))
    assert out["geometry"] == "tight"
    assert out["tight_box"] is not None
