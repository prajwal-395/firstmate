"""How a project chooses to carry its caption overlays, if it chooses.

Two independent axes, defaulting to tight since 2026-09-10 (the
captain's reversal of full-frame-by-default):

- GEOMETRY: `tight` renders only the drawn bounds
  (`library/tools/tight_box.py`) and places the small clip at an
  offset in Resolve, so it stays movable after the fact. `full`
  renders at the delivery frame (1080x1920) and needs no transform.
- CONTAINER: `video` stitches frames into one ProRes 4444 mov.
  `frames` keeps the PNG sequence on disk and hands it to Resolve
  directly - no intermediate video is rendered first.

A project declares either, both, or neither under `pipeline:` in its
project.yaml:

    pipeline:
      subtitle_overlay_geometry: tight
      subtitle_overlay_container: frames

Declaring nothing is the tight path. An unknown value
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












def test_unknown_values_raise(tmp_path):
    project = _project_with({"subtitle_overlay_geometry": "small"},
                            tmp_path)
    with pytest.raises(ValueError, match="subtitle_overlay_geometry"):
        resolve_overlay_geometry(project)
    project = _project_with({"subtitle_overlay_container": "gif"},
                            tmp_path)
    with pytest.raises(ValueError, match="subtitle_overlay_container"):
        resolve_overlay_container(project)








