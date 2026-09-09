"""Scratch holds no file a live timeline points at.

Measured 2026-09-09 on `lucie/geo-podcast`: five TV-frame overlays
(1.5 GB) rendered into `scratch/reel_look/frame_overlays/` sat on V2
of live reel timelines - read off the live Resolve database's
`Sm2TiItem.MediaFilePath`, not off filenames - while `Kind.SCRATCH`
declares the whole tree safe to discard at any moment. Full-frame
cards rendered into `scratch/reel_cards/` are the same shape: placed
on V1, deletable by declaration.

The fix under test: the builder promotes both into step-owned OUTPUT
areas (`Area.REEL_FRAME_OVERLAYS` / `Area.REEL_CARDS`) before anything
is imported, and every placement site refuses a scratch path
(`reel_placed_assets.assert_placeable`). These tests fail on a tree
where the promotion does not exist - importing the helper raises - and
pass where it does.
"""

import dataclasses
import os

import pytest

from library.tools.project_layout import Area, Kind, ProjectLayout
from library.tools.reel_placed_assets import (
    PlacedAssetInScratch,
    assert_placeable,
    durable_copy_exists,
    is_under_scratch,
    promote_cards,
    promote_frame_overlays,
    promote_to_durable,
    scratch_dir_for,
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


def test_the_durable_areas_are_step_owned_output():
    """The declaration half: placed reel assets are OUTPUT of the step
    that builds reels, filed under that step's own directory - never
    scratch."""
    for area in (Area.REEL_FRAME_OVERLAYS, Area.REEL_CARDS):
        spec = ProjectLayout.spec(area)
        assert spec.kind is Kind.OUTPUT, f"{area.value} is not OUTPUT"
        assert spec.step == "build_reels", f"{area.value} has no owner"
        assert spec.relpath.startswith("pipeline_output/steps/7_01_build_reels/"), (
            spec.relpath
        )


def test_scratch_says_placed_files_do_not_live_here():
    """The honest half of the declaration: scratch names the boundary
    and where the placed files went instead."""
    purpose = ProjectLayout.spec(Area.SCRATCH).purpose
    assert "REEL_FRAME_OVERLAYS" in purpose
    assert "REEL_CARDS" in purpose


def test_a_scratch_path_is_not_placeable(tmp_path):
    project, src = _project(tmp_path)
    assert is_under_scratch(src, project)
    with pytest.raises(PlacedAssetInScratch, match="scratch"):
        assert_placeable(src, project)


def test_a_durable_path_and_footage_pass_through(tmp_path):
    """The guard refuses scratch, not everything: the promoted copy
    passes, and so does footage from outside the project entirely."""
    project, src = _project(tmp_path)
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


def test_promotion_is_idempotent(tmp_path):
    project, src = _project(tmp_path)
    first = promote_to_durable(src, project, Area.REEL_FRAME_OVERLAYS)
    second = promote_to_durable(src, project, Area.REEL_FRAME_OVERLAYS)
    assert first == second
    assert len(os.listdir(os.path.dirname(first))) == 1


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


def test_a_card_with_no_file_passes_through_to_the_builder(tmp_path):
    """Promotion re-points; it does not report. A card with no file
    is the builder's own missing-file refusal, unchanged."""
    project, _ = _project(tmp_path)
    (card,) = promote_cards([{"render_name": "x", "rendered_path": ""}], project)
    assert card["rendered_path"] == ""


def test_the_placer_refuses_scratch_when_it_knows_the_folder(tmp_path):
    """The guard at the placement site: with the folder in scope, a
    scratch segment never reaches Resolve."""
    from library.tools.reel_build import place_overlay_segments

    project, src = _project(tmp_path)

    class _Pool:
        def ImportMedia(self, paths):
            raise AssertionError("must refuse before importing")

    class _Timeline:
        def GetUniqueId(self):
            return "t"

    class _Project:
        def GetCurrentTimeline(self):
            return _Timeline()

    with pytest.raises(PlacedAssetInScratch):
        place_overlay_segments(
            _Pool(),
            _Project(),
            _Timeline(),
            "reel",
            24000 / 1001,
            [{"overlay_path": src, "timeline_start": 0.0, "total_frames": 100}],
            2,
            kind="TV frame",
            check="F4",
            project_folder=project,
        )


def test_durable_copy_exists_creates_nothing(tmp_path):
    project, src = _project(tmp_path)
    assert durable_copy_exists(src, project, Area.REEL_FRAME_OVERLAYS) is None
    dest = promote_to_durable(src, project, Area.REEL_FRAME_OVERLAYS)
    assert durable_copy_exists(src, project, Area.REEL_FRAME_OVERLAYS) == dest
    assert scratch_dir_for(project) == os.path.join(
        project, "pipeline_output", "scratch"
    )
