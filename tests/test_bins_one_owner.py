"""One module decides every bin path; everything else asks it.

The captain's pool holds two schemes side by side - the numbered bins
(`05 - Reels`, `06 - Subtitle renders`, ...) and the unnumbered ones
(`Reels`, `Reel subtitles`, `Subtitles`) - plus a firstmate proof
timeline (`SOP Proof_...`) filed among their reels. `resolve_bin_layout`
is the single owner of every bin path the way `timeline_layout` is the
single owner of every track name; `resolve_organization` and the build
half that imports media ask it rather than declaring their own names.

Every case below has a failing side: a legacy bin that must be moved
rather than stranded, a proof timeline that must file separately, and
the two MAYBES the brief names as known unknowns - answered here, not
assumed.
"""
from __future__ import annotations

from pathlib import Path

from library.tools import resolve_bin_layout as bins
from library.tools import resolve_organization as org

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return org.Artefact(item_id=item_id, name=name, kind="clip",
                        file_path=path, placed_by=tuple(placed_by),
                        folder_path=tuple(folder))


def timeline(item_id, name, *, folder=()):
    return org.Artefact(item_id=item_id, name=name, kind="timeline",
                        file_path="", placed_by=(),
                        folder_path=tuple(folder))


def a_plan(artefacts, **kw):
    return org.plan_organization(
        artefacts=artefacts,
        project_root=PROJECT_ROOT,
        master_timeline_name=MASTER,
        current_reels=kw.pop("current_reels", []),
        archived_plan_names=kw.pop("archived_plan_names", []),
        **kw)


# ------------------------------------------------------- one owner


def test_the_numbered_scheme_survives_as_the_top_level():
    """The winner is the scheme bound to `project_layout.Area` - the
    existing owner of where files go - not the one invented beside it."""
    assert bins.REELS_BIN.startswith("05")
    assert bins.SUBTITLES_BIN.startswith("06")
    assert bins.MOTION_GRAPHICS_BIN.startswith("07")
    assert bins.SOURCE_BIN == "Source footage"


def test_the_organiser_declares_no_bin_names_of_its_own():
    """Every destination the plan emits is rooted at a bin the layout
    module owns. A second declaration beside it is the defect."""
    assert org.BIN_REELS == bins.REELS_BIN
    assert org.BIN_SUBTITLES == bins.SUBTITLES_BIN
    assert org.BIN_SOURCE == bins.SOURCE_BIN
    assert org.BIN_UNPLACED == bins.UNPLACED_BIN
    assert set(org.STATE_BINS.values()) == set(bins.REEL_STATE_BINS.values())


def test_no_build_call_site_invents_a_bin_name():
    """`resolve_build_timeline` used to import into `V1`, `V2`,
    `Audio`, `Subtitles`, `MotionGraphics`, `TimedText` and
    `Generators` bins of its own invention. Every `AddSubFolder` and
    every import-folder call there must name a `resolve_bin_layout`
    attribute, never a string literal."""
    source = Path(
        "library/steps/step_6_01_render/resolve_build_timeline.py"
    ).read_text(encoding="utf-8")
    import re
    for match in re.finditer(
            r"(?:AddSubFolder\([^,]+,\s*|_import_to_folder\()\"([^\"]+)\"",
            source):
        assert False, (
            f"resolve_build_timeline invents a bin "
            f"{match.group(1)!r} instead of asking resolve_bin_layout")


# --------------------------------------- per-timeline filing survives




def test_motion_graphics_kind_files_beneath_07_not_06():
    """The numbered scheme binds bins to Areas, so a render whose file
    lives under the motion-graphics area files under the
    motion-graphics bin - by path fact, not by name parse."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-cur", "Reel 01 - live",
                 folder=(bins.REELS_BIN, "Current plan")),
        clip("c-mg", "mg_live.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_06_render_motion_graphics/motion_graphics/b.mov",
             placed_by=["Reel 01 - live"],
             folder=("Reel subtitles", "Reel 01 - live")),
    ]
    dest = {v.name: v.destination for v in a_plan(
        artefacts, current_reels=["Reel 01 - live"]).verdicts}
    assert dest["mg_live.mov"] == (
        bins.MOTION_GRAPHICS_BIN, "Reel 01 - live")


def test_unrecorded_and_unplaced_are_different_axes():
    """The brief's known unknown, answered: UNRECORDED is a reel
    timeline's state (no plan names it); "Not placed" is a clip's
    placement fact (no timeline plays it). They never mean each other."""
    assert (bins.REELS_BIN, bins.REEL_STATE_BINS[org.UNRECORDED]) != (
        bins.SUBTITLES_BIN, bins.UNPLACED_BIN)
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-oneoff", "Reel 09 - hand-made",
                 folder=(bins.REELS_BIN,
                         bins.REEL_STATE_BINS[org.UNRECORDED])),
        clip("c-orphan", "sub_orphan.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/c.mov",
             folder=(bins.SUBTITLES_BIN, bins.UNPLACED_BIN)),
    ]
    plan = a_plan(artefacts)
    assert plan.moves == []


# ------------------------------------------------- firstmate's proof


def test_a_proof_timeline_files_apart_from_the_captains_reels():
    """`SOP Proof_...` is firstmate's, not the captain's. It files
    under `05 - Reels/Proof`, never among the state bins."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-proof", "SOP Proof_timeline_sop",
                 folder=("Reel subtitles",)),
        clip("c-proof", "proof_cap.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/p.mov",
             placed_by=["SOP Proof_timeline_sop"],
             folder=("Reel subtitles", "SOP Proof_timeline_sop")),
    ]
    plan = a_plan(artefacts)
    dest = {v.name: v.destination for v in plan.verdicts}
    assert dest["SOP Proof_timeline_sop"] == (
        bins.REELS_BIN, bins.REELS_PROOF_BIN)
    # Its captions still answer which timeline places them - the name
    # says whose they are, so they need no separate bin.
    assert dest["proof_cap.mov"] == (
        bins.SUBTITLES_BIN, "SOP Proof_timeline_sop")


# ----------------------------------------------- the captain's pool


def screenshot_pool():
    """The captain's screenshot as artefacts: both schemes, flat, plus
    the proof timeline and its captions inside `Reel subtitles`."""
    return [
        timeline("t-master", MASTER, folder=()),
        timeline("t-12", "Reel 12 - ai-isnt-making-things-up",
                 folder=("Reels",)),
        timeline("t-09", "Reel 09 - your-website-is-only-20-percent",
                 folder=("Reels",)),
        timeline("t-27", "Reel 27 - google-reviews-build-ai-trust",
                 folder=("Reels",)),
        timeline("t-proof", "SOP Proof_timeline_sop",
                 folder=("Reel subtitles",)),
        timeline("t-tiered", "Reel 01 - seo-ranks-geo-understands",
                 folder=("Reels", "Fully approved")),
        clip("c-12", "sub_12_a.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/12a.mov",
             placed_by=["Reel 12 - ai-isnt-making-things-up"],
             folder=("Reel subtitles", "Reel 12 - ai-isnt-making-things-up")),
        clip("c-orphan", "sub_old.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/old.mov",
             folder=("Reel subtitles", "Not placed on any timeline")),
        clip("c-proof", "proof_cap.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/p.mov",
             placed_by=["SOP Proof_timeline_sop"],
             folder=("Reel subtitles", "SOP Proof_timeline_sop")),
        clip("c-loose", "sub_loose.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/loose.mov",
             placed_by=["Reel 09 - your-website-is-only-20-percent"],
             folder=("Subtitles",)),
        clip("c-cam", "podcast_cam_a.mov", path="/elsewhere/cam_a.mov",
             placed_by=[MASTER],
             folder=("V1",)),
        clip("c-mg", "mg_09.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_06_render_motion_graphics/motion_graphics/09.mov",
             placed_by=["Reel 09 - your-website-is-only-20-percent"],
             folder=("MotionGraphics",)),
    ]






