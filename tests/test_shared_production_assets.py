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
       "sub_akshita_x.mov")
FRAME = (f"{PROJECT_ROOT}/pipeline_output/steps/7_01_build_reels/"
         "frame_overlays/tv_frame_x.mov")
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
        master_timeline_name=MASTER, built_reels=[REEL_01, REEL_09],
        archived_plan_names=[])


def base():
    return [timeline("t-master", MASTER), timeline("t-01", REEL_01),
            timeline("t-09", REEL_09)]


def test_kind_is_a_path_fact_not_a_name_parse():
    assert bins.is_render_file(SUB, PROJECT_ROOT)
    assert not bins.is_render_file(FRAME, PROJECT_ROOT)
    assert not bins.is_render_file(FREEZE, PROJECT_ROOT)
    assert not bins.is_render_file("", PROJECT_ROOT)


def test_a_shared_frame_files_under_the_assets_bin():
    plan = a_plan(base() + [
        clip("c-frame", "tv_frame_1f8e8d06ff.mov", path=FRAME,
             placed_by=[REEL_01, REEL_09])])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["tv_frame_1f8e8d06ff.mov"] == (BIN_ASSETS,)


def test_a_shared_freeze_files_under_the_assets_bin():
    plan = a_plan(base() + [
        clip("c-freeze", "reel_freeze_8da72bf764.mov", path=FREEZE,
             placed_by=[REEL_01, REEL_09])])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["reel_freeze_8da72bf764.mov"] == (BIN_ASSETS,)


def test_a_sole_placed_production_asset_files_under_the_assets_bin():
    """Placement never earns a production asset a per-reel folder under
    a render bin: the category is a path fact, and the assets bin has
    no per-reel leaves."""
    plan = a_plan(base() + [
        clip("c-freeze", "reel_freeze_854a84fdb0.mov", path=FREEZE,
             placed_by=[REEL_09])])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["reel_freeze_854a84fdb0.mov"] == (BIN_ASSETS,)


def test_an_unplaced_production_asset_files_under_the_assets_bin():
    """No `Not placed` leaf is invented there: the bins that exist are
    the captain's structure."""
    plan = a_plan(base() + [
        clip("c-freeze", "reel_freeze_old.mov", path=FREEZE)])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["reel_freeze_old.mov"] == (BIN_ASSETS,)


def test_a_shared_subtitle_render_files_under_the_shared_leaf():
    """The other half of the rule: several placers means no single
    reel owns it, but it is still a render, so it files under the
    shared leaf instead of sitting at the root beside the unfiled."""
    plan = a_plan(base() + [
        clip("c-sub", "sub_akshita_x.mov", path=SUB,
             placed_by=[REEL_01, REEL_09])])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["sub_akshita_x.mov"] == (BIN_SUBTITLES, BIN_SHARED)


def test_a_shared_motion_graphic_files_under_its_own_shared_leaf():
    """The rule holds per category bin, even with nothing shared there
    today: the series will share a graphic the moment two reels want
    the same lower third."""
    mg = (f"{PROJECT_ROOT}/pipeline_output/steps/"
          f"4_06_render_motion_graphics/motion_graphics/lower_third.mov")
    plan = a_plan(base() + [
        clip("c-mg", "lower_third.mov", path=mg,
             placed_by=[REEL_01, REEL_09])])
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["lower_third.mov"] == (bins.MOTION_GRAPHICS_BIN, BIN_SHARED)


def test_no_clip_verdict_targets_a_render_root():
    """The checkable property: per-reel means that reel uses it, shared
    means several do, unplaced means none do, and nothing sits at a
    render root. (The flat assets bin is a home, not a root-loose
    state: production assets file at its only level.)"""
    artefacts = base() + [
        clip("c-sole", "sub_sole.mov", path=SUB, placed_by=[REEL_01]),
        clip("c-shared", "sub_shared.mov", path=SUB,
             placed_by=[REEL_01, REEL_09]),
        clip("c-unplaced", "sub_none.mov", path=SUB),
        clip("c-asset", "tv_frame.mov", path=FRAME,
             placed_by=[REEL_01, REEL_09]),
    ]
    for verdict in a_plan(artefacts).verdicts:
        if verdict.kind != "clip":
            continue
        assert verdict.destination not in (
            (BIN_SUBTITLES,), (bins.MOTION_GRAPHICS_BIN,)), (
            f"{verdict.name} files at a render root")


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


def test_filing_a_shared_asset_is_idempotent():
    plan = a_plan(base() + [
        clip("c-frame", "tv_frame_1f8e8d06ff.mov", path=FRAME,
             placed_by=[REEL_01, REEL_09],
             folder=(BIN_ASSETS,))])
    assert [v for v in plan.moves if v.item_id == "c-frame"] == []


def test_import_time_agrees_with_filing_time():
    """What an import lands in is what the next organise keeps."""
    from library.tools.reel_build import import_dest_bin, overlay_import_bin

    assert import_dest_bin(FRAME, PROJECT_ROOT) == (BIN_ASSETS,)
    assert import_dest_bin(SUB, PROJECT_ROOT) == (BIN_SUBTITLES,)
    assert overlay_import_bin(PROJECT_ROOT, REEL_01, FREEZE) == (BIN_ASSETS,)
    assert overlay_import_bin(PROJECT_ROOT, REEL_01, SUB) == (
        BIN_SUBTITLES, REEL_01)
