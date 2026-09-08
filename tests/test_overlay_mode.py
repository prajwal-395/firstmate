"""How a project chooses to carry its caption overlays, if it chooses.

Two independent axes, both defaulting to today:

- GEOMETRY: `full` renders at the delivery frame (1080x1920), exactly
  as every segment has always rendered. `tight` renders only the drawn
  bounds (`library/tools/tight_box.py`) and places the small clip at an
  offset in Resolve, so it stays movable after the fact.
- CONTAINER: `video` stitches frames into one ProRes 4444 mov.
  `frames` keeps the PNG sequence on disk and hands it to Resolve
  directly - no intermediate video is rendered first.

A project declares either, both, or neither under `pipeline:` in its
project.yaml:

    pipeline:
      subtitle_overlay_geometry: tight
      subtitle_overlay_container: frames

Declaring nothing is today's path, byte for byte. An unknown value
raises rather than falling back, because a silently ignored declaration
is a choice the project thinks it made and did not.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest

from library.tools.overlay_mode import (  # noqa: E402
    CONTAINERS,
    GEOMETRIES,
    resolve_motion_graphics_geometry,
    resolve_overlay_container,
    resolve_overlay_geometry,
)


def _project_with(pipeline: dict, tmp_path) -> str:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    lines = ["name: t", "slug: t", "pipeline:"]
    for key, value in pipeline.items():
        lines.append(f"  {key}: {value}")
    (root / "project.yaml").write_text("\n".join(lines) + "\n",
                                       encoding="utf-8")
    return str(root)


def test_defaults_are_todays_path(tmp_path):
    project = _project_with({}, tmp_path)
    assert resolve_overlay_geometry(project) == "full"
    assert resolve_overlay_container(project) == "video"


def test_no_project_folder_is_todays_path():
    assert resolve_overlay_geometry("") == "full"
    assert resolve_overlay_geometry(None) == "full"
    assert resolve_overlay_container("") == "video"
    assert resolve_overlay_container(None) == "video"


def test_declared_values_are_honoured(tmp_path):
    project = _project_with({"subtitle_overlay_geometry": "tight",
                             "subtitle_overlay_container": "frames"},
                            tmp_path)
    assert resolve_overlay_geometry(project) == "tight"
    assert resolve_overlay_container(project) == "frames"


def test_unknown_values_raise(tmp_path):
    project = _project_with({"subtitle_overlay_geometry": "small"},
                            tmp_path)
    with pytest.raises(ValueError, match="subtitle_overlay_geometry"):
        resolve_overlay_geometry(project)
    project = _project_with({"subtitle_overlay_container": "gif"},
                            tmp_path)
    with pytest.raises(ValueError, match="subtitle_overlay_container"):
        resolve_overlay_container(project)


def test_vocabularies_are_complete():
    assert set(GEOMETRIES) == {"full", "tight"}
    assert set(CONTAINERS) == {"video", "frames"}


def test_motion_graphics_geometry_defaults_to_today(tmp_path):
    project = _project_with({}, tmp_path)
    assert resolve_motion_graphics_geometry(project) == "full"
    assert resolve_motion_graphics_geometry("") == "full"
    assert resolve_motion_graphics_geometry(None) == "full"


def test_motion_graphics_geometry_is_its_own_key(tmp_path):
    project = _project_with({"motion_graphics_overlay_geometry": "tight"},
                            tmp_path)
    assert resolve_motion_graphics_geometry(project) == "tight"
    # The caption key does not move it: the two are separate choices.
    project = _project_with({"subtitle_overlay_geometry": "tight"},
                            tmp_path)
    assert resolve_motion_graphics_geometry(project) == "full"


def test_motion_graphics_geometry_unknown_raises(tmp_path):
    project = _project_with({"motion_graphics_overlay_geometry": "small"},
                            tmp_path)
    with pytest.raises(ValueError,
                       match="motion_graphics_overlay_geometry"):
        resolve_motion_graphics_geometry(project)
