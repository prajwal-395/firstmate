"""A shared production asset files under the shared assets bin.

Measured 2026-09-18 on the captain's project: a TV frame placed on all
eight reels and two freeze holds placed on three each sat loose at the
top of `06 - Subtitle renders`. They are not subtitle renders - their
files live beside the reel builder's own step, under no render area -
so the subtitle fallback was never their category, and no one reel's
folder is their home either. KIND is a path fact
(`resolve_bin_layout.is_render_file`); `03 - Assets` is the captain's
own bin for exactly that class.

The companion fact, pinned here so the two cannot drift apart: a
subtitle render several timelines place is still a render, and files
under its render bin's shared leaf rather than sitting at the root
beside the unfiled - or joining the assets.
"""

from library.tools import resolve_bin_layout as bins
from library.tools.resolve_organization import (
    BIN_ASSETS,
    BIN_SHARED,
    BIN_SUBTITLES,
    Artefact,
    plan_organization,
)

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"

REEL_01 = "Reel 01 - geo-is-comprehension-not-position"
REEL_09 = "Reel 09 - your-website-is-only-20-percent"

SUB = (f"{PROJECT_ROOT}/pipeline_output/steps/4_05_render_subtitles/"
       "sub_speakerone_x.mov")
FRAME = (f"{PROJECT_ROOT}/pipeline_output/steps/7_01_build_reels/"
         "frame_overlays/tv_frame_x.png")
FREEZE = (f"{PROJECT_ROOT}/pipeline_output/steps/7_01_build_reels/"
          "reel_cards/reel_freeze_x.mov")


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(item_id=item_id, name=name, kind="clip", file_path=path,
                    placed_by=tuple(placed_by), folder_path=tuple(folder))


def timeline(item_id, name):
    return Artefact(item_id=item_id, name=name, kind="timeline",
                    file_path="", placed_by=(), folder_path=())


def a_plan(artefacts):
    return plan_organization(
        artefacts=artefacts, project_root=PROJECT_ROOT,
        master_timeline_name=MASTER, current_reels=[REEL_01, REEL_09],
        archived_plan_names=[])


def base():
    return [timeline("t-master", MASTER), timeline("t-01", REEL_01),
            timeline("t-09", REEL_09)]


def test_kind_is_a_path_fact_not_a_name_parse():
    assert bins.is_render_file(SUB, PROJECT_ROOT)
    assert not bins.is_render_file(FRAME, PROJECT_ROOT)
    assert not bins.is_render_file(FREEZE, PROJECT_ROOT)
    assert not bins.is_render_file("", PROJECT_ROOT)


def test_a_production_asset_files_under_the_assets_bin_however_placed():
    """Shared, sole-placed or unplaced: placement never earns a
    production asset a per-reel folder (the category is a path fact),
    and no `Not placed` leaf is invented in the captain's structure."""
    plan = a_plan(base() + [
        clip("c-frame", "tv_frame_1f8e8d06ff.png", path=FRAME,
             placed_by=[REEL_01, REEL_09]),
        clip("c-freeze", "reel_freeze_854a84fdb0.mov", path=FREEZE,
             placed_by=[REEL_09]),
        clip("c-old", "reel_freeze_old.mov", path=FREEZE)])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["tv_frame_1f8e8d06ff.png"] == (BIN_ASSETS,)
    assert dest["reel_freeze_854a84fdb0.mov"] == (BIN_ASSETS,)
    assert dest["reel_freeze_old.mov"] == (BIN_ASSETS,)


def test_a_shared_subtitle_render_files_under_the_shared_leaf():
    """The other half of the rule: several placers means no single
    reel owns it, but it is still a render, so it files under the
    shared leaf instead of sitting at the root beside the unfiled."""
    plan = a_plan(base() + [
        clip("c-sub", "sub_speakerone_x.mov", path=SUB,
             placed_by=[REEL_01, REEL_09])])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["sub_speakerone_x.mov"] == (BIN_SUBTITLES, BIN_SHARED)


def test_the_shared_leaf_is_never_a_retirement():
    """A canonical destination: the dead-bin sweep cannot take it with
    its contents, and the empty-shell sweep cannot take it empty."""
    from library.tools.resolve_organization import (
        is_retired_canonical_bin,
        is_spent_render_bin,
        plan_dead_render_bins,
    )
    leaf = (BIN_SUBTITLES, BIN_SHARED)
    assert not is_spent_render_bin(leaf)
    assert not is_retired_canonical_bin(leaf, frozenset())
    retirements, _declined = plan_dead_render_bins(
        base() + [clip("c-sub", "sub_x.mov", path=SUB,
                       placed_by=[REEL_01, REEL_09],
                       folder=(BIN_SUBTITLES, BIN_SHARED))],
        [list(leaf)], PROJECT_ROOT)
    assert retirements == []


def test_import_time_agrees_with_filing_time():
    """What an import lands in is what the next organise keeps."""
    from library.tools.reel_build import import_dest_bin, overlay_import_bin

    assert import_dest_bin(FRAME, PROJECT_ROOT) == (BIN_ASSETS,)
    assert import_dest_bin(SUB, PROJECT_ROOT) == (BIN_SUBTITLES,)
    assert overlay_import_bin(PROJECT_ROOT, REEL_01, FREEZE) == (BIN_ASSETS,)
    assert overlay_import_bin(PROJECT_ROOT, REEL_01, SUB) == (
        BIN_SUBTITLES, REEL_01)
