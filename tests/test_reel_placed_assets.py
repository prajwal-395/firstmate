"""Scratch holds no file a live timeline points at.

The builder promotes placed overlays and cards into step-owned OUTPUT
areas, and every placement site refuses a scratch path. The measured
incident is in `library/tools/reel_placed_assets.py`'s docstring.
"""

import dataclasses
import os

import pytest

from library.tools.project_layout import Area
from library.tools.reel_placed_assets import (
    PlacedAssetInScratch,
    assert_placeable,
    is_under_scratch,
    promote_cards,
    promote_frame_overlays,
    promote_to_durable,
)


def _project(tmp_path):
    """A project folder with a scratch overlay drafted in it."""
    project = str(tmp_path / "proj")
    os.makedirs(
        os.path.join(
            project, "pipeline_output", "scratch", "reel_look", "frame_overlays"
        )
    )
    src = os.path.join(
        project,
        "pipeline_output",
        "scratch",
        "reel_look",
        "frame_overlays",
        "tv_frame_deadbeef01_100f.mov",
    )
    with open(src, "wb") as handle:
        handle.write(os.urandom(1 << 20))
    return project, src


def test_only_a_scratch_path_is_refused_placement(tmp_path):
    """The guard refuses scratch, not everything: the promoted copy
    passes, and so does footage from outside the project entirely."""
    project, src = _project(tmp_path)
    assert is_under_scratch(src, project)
    with pytest.raises(PlacedAssetInScratch, match="scratch"):
        assert_placeable(src, project)
    dest = promote_to_durable(src, project, Area.REEL_FRAME_OVERLAYS)
    assert assert_placeable(dest, project) == dest
    assert assert_placeable("/footage/clip_001.mxf", project) == "/footage/clip_001.mxf"


def test_promoted_overlay_lands_durable_not_under_scratch(tmp_path):
    """The whole point: what the timeline will point at can no longer
    be removed by anything that trusts the SCRATCH declaration."""
    project, src = _project(tmp_path)
    before = os.path.getsize(src)
    (segments,) = promote_frame_overlays(
        [{"overlay_path": src, "timeline_start": 0.0, "total_frames": 100}], project
    )
    dest = segments["overlay_path"]
    assert dest != src
    assert not is_under_scratch(dest, project)
    assert dest.startswith(
        os.path.join(
            project, "pipeline_output", "steps", "7_01_build_reels", "frame_overlays"
        )
        + os.sep
    )
    assert os.path.isfile(dest)
    assert os.path.getsize(dest) == before
    # The renderer's own cache is untouched: promotion shares bytes,
    # it does not move the source.
    assert os.path.isfile(src)
    assert os.path.getsize(src) == before


def test_promoting_a_missing_file_is_refused_not_placed(tmp_path):
    project, _ = _project(tmp_path)
    missing = os.path.join(
        project, "pipeline_output", "scratch", "reel_look", "frame_overlays", "gone.mov"
    )
    with pytest.raises(PlacedAssetInScratch, match="not a file"):
        promote_to_durable(missing, project, Area.REEL_FRAME_OVERLAYS)


def test_cards_promote_to_the_card_area(tmp_path):
    """Dict cards and frozen PlannedCard-shaped dataclasses alike."""
    project, src = _project(tmp_path)

    @dataclasses.dataclass(frozen=True)
    class _Card:
        render_name: str
        rendered_path: str = ""

    card_src = os.path.join(project, "pipeline_output", "scratch", "reel_cards")
    os.makedirs(card_src, exist_ok=True)
    card_file = os.path.join(card_src, "reel_01_card_00.mov")
    with open(card_file, "wb") as handle:
        handle.write(b"card-bytes")
    (as_dict, as_dataclass) = promote_cards(
        [
            {"render_name": "reel_01_card_00", "rendered_path": card_file},
            _Card(render_name="reel_01_card_00", rendered_path=card_file),
        ],
        project,
    )
    for promoted in (as_dict["rendered_path"], as_dataclass.rendered_path):
        assert not is_under_scratch(promoted, project)
        assert os.path.join("7_01_build_reels", "reel_cards") in promoted
        with open(promoted, "rb") as handle:
            assert handle.read() == b"card-bytes"
    # Promotion re-points; it does not report. A card with no file
    # is the builder's own missing-file refusal, unchanged.
    (card,) = promote_cards([{"render_name": "x", "rendered_path": ""}], project)
    assert card["rendered_path"] == ""


