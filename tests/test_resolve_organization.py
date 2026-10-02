"""The filing rules, and the two directions the gate must work in.

AGENTS.md 10.4: a gate that cannot fail reads as coverage and is worse
than none, and one that fails correct output is the same defect from the
other side.  Both are asserted here on the shape the field-test project
actually has - a master, reels in three states, generated overlays that
one timeline places, overlays that nothing places, and footage from
outside the project.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from library.tools.resolve_organization import (
    BIN_REELS,
    BIN_SHARED,
    BIN_SUBTITLES,
    EARLIER,
    STATE_BINS,
    TAG_PREFIX,
    UNRECORDED,
    Artefact,
    OrganizationError,
    assert_organized,
    findings,
    plan_organization,
    reel_state,
    state_from_keywords,
    unplaced_report,
)

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(item_id=item_id, name=name, kind="clip", file_path=path,
                    placed_by=tuple(placed_by), folder_path=tuple(folder))


def timeline(item_id, name, *, folder=()):
    return Artefact(item_id=item_id, name=name, kind="timeline",
                    file_path="", placed_by=(), folder_path=tuple(folder))


def a_project():
    """The field test in miniature: every case, one of each."""
    return [
        timeline("t-master", MASTER),
        timeline("t-cur", "Reel 01 - live (harvest)"),
        timeline("t-old", "Reel 01 - superseded"),
        timeline("t-oneoff", "Reel 03 - superseded (fragment fix)"),
        clip("c-cur", "sub_live_a.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/a.mov",
             placed_by=["Reel 01 - live (harvest)"]),
        clip("c-old", "sub_old_a.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/b.mov",
             placed_by=["Reel 01 - superseded"]),
        clip("c-orphan", "sub_orphan.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                  f"4_05_render_subtitles/c.mov"),
        clip("c-source", "podcast_cam_a.mov", path="/elsewhere/cam_a.mov",
             placed_by=[MASTER, "Reel 01 - live (harvest)"]),
        clip("c-vfx", "flare.mov", path="/assets/vfx/flare.mov"),
    ]


BUILT = ["Reel 01 - live (harvest)"]
ARCHIVED = ["Reel 01 - live (harvest)", "Reel 01 - superseded",
            "Reel 03 - superseded"]


def a_plan(artefacts=None, **kw):
    return plan_organization(
        artefacts=artefacts if artefacts is not None else a_project(),
        project_root=PROJECT_ROOT,
        master_timeline_name=MASTER,
        current_reels=kw.pop("current_reels", BUILT),
        archived_plan_names=kw.pop("archived_plan_names", ARCHIVED),
        plan_hash=kw.pop("plan_hash", "1cf79aebb3c6" + "0" * 52),
        built_at=kw.pop("built_at", "2026-09-07T00:36:28+00:00"),
        **kw)


# ---------------------------------------------------------------- states


def test_a_name_no_plan_carries_is_unrecorded_not_guessed_by_prefix():
    """The eight one-offs on the field test are a plan name plus a typed
    suffix.  Calling them EARLIER would decide by prefix, which is what
    AGENTS.md 5 forbids for exactly this reason."""
    state, why = reel_state("Reel 03 - superseded (fragment fix)",
                            BUILT, ARCHIVED)
    assert state == UNRECORDED
    assert "no plan" in why


# ----------------------------------------------------------------- rules


def test_the_master_timeline_is_never_moved():
    plan = a_plan()
    assert MASTER not in [v.name for v in plan.verdicts]
    assert MASTER in [name for name, _ in plan.left_alone]
    assert MASTER not in [v.name for v in plan.moves]
    # And organising without a master name refuses rather than guessing.
    with pytest.raises(OrganizationError, match="master"):
        plan_organization(a_project(), PROJECT_ROOT, "", BUILT, ARCHIVED)


def test_a_caption_bin_does_not_move_when_its_reel_changes_state():
    """A captions tree mirroring the reel's state would strand an empty
    bin on every plan change, and nothing here may delete one."""
    artefacts = a_project()
    live = {v.name: v.destination for v in a_plan(artefacts).verdicts}
    demoted = {v.name: v.destination
               for v in a_plan(artefacts, current_reels=[]).verdicts}
    assert live["sub_live_a.mov"] == demoted["sub_live_a.mov"]
    assert live["Reel 01 - live (harvest)"] != \
        demoted["Reel 01 - live (harvest)"]


def test_a_generated_clip_several_timelines_place_files_under_the_shared_leaf():
    """Several placers means no single reel owns it - but it is still
    generated, so it files under the shared leaf instead of the root
    beside the unfiled, and never as outside material. Measured
    2026-09-10: a rebuild beside its backup shares every reused
    overlay file, and the old verdict filed all of them as Source
    footage. Measured 2026-09-18: reels ending on the same words
    share those words' caption renders, structurally, not as
    leftovers."""
    shared = clip("c-shared", "shared.mov",
                  path=f"{PROJECT_ROOT}/pipeline_output/steps/"
                       f"4_05_render_subtitles/shared.mov",
                  placed_by=["Reel 01 - live (harvest)",
                             "Reel 01 - superseded"])
    plan = a_plan(a_project() + [shared])
    by_name = {v.name: v for v in plan.verdicts}
    assert by_name["shared.mov"].destination == (
        BIN_SUBTITLES, BIN_SHARED)
    assert "no single reel" in by_name["shared.mov"].why


# ---------------------------------------------------------------- stamps


def test_a_stamp_only_uses_metadata_keys_resolve_accepts():
    """`SetMetadata` returns False and stores nothing for a key Resolve
    does not know - measured on 21.0.0b.28."""
    accepted = {"Comments", "Keywords", "Description", "Scene", "Shot",
                "Take", "Angle", "Reel Number", "Move", "Day / Night",
                "Camera #", "Production Name", "Episode Name", "Shot Type",
                "Environment", "Genre", "People", "Location"}
    for stamp in a_plan().stamps:
        assert set(stamp["fields"]) <= accepted, stamp["fields"]


def test_keywords_that_say_nothing_of_ours_read_back_as_nothing():
    assert state_from_keywords("") is None
    assert state_from_keywords("interview b-roll") is None
    assert state_from_keywords(f"{TAG_PREFIX}state=invented") is None


# ------------------------------------------------- the gate, both ways


def _filed(artefacts, plan):
    """`artefacts` as they would be after the plan is applied."""
    dest = {v.item_id: v.destination for v in plan.verdicts}
    return [Artefact(a.item_id, a.name, a.kind, a.file_path, a.placed_by,
                     dest.get(a.item_id, a.folder_path))
             for a in artefacts]


def test_the_gate_fails_a_misfiled_timeline_or_a_duplicate_bin():
    artefacts = a_project()
    plan = a_plan(artefacts)
    settled = _filed(artefacts, plan)
    wrong = [a if a.item_id != "t-cur"
             else Artefact(a.item_id, a.name, a.kind, a.file_path,
                           a.placed_by, (BIN_REELS, STATE_BINS[EARLIER]))
             for a in settled]
    found = findings(wrong, a_plan(settled))
    assert [f["kind"] for f in found] == ["misfiled"]
    assert "Reel 01 - live (harvest)" in found[0]["detail"]
    with pytest.raises(OrganizationError, match="misfiled"):
        assert_organized(found)
    # `AddSubFolder` makes a second bin of the same name on every call -
    # measured - so half the reels can file into each.
    found = findings(settled, a_plan(settled), [f"{BIN_REELS}"])
    assert [f["kind"] for f in found] == ["duplicate_bin"]


def test_a_reel_in_a_bin_the_pipeline_does_not_manage_stays_there():
    """The captain's tiers survive a build: no verdict, no move, no
    stamp - hands off means hands off, including the metadata write."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-a1", "Reel 01 - seo-ranks-geo-understands",
                 folder=(BIN_REELS, "Fully approved")),
        timeline("t-b1", "Reel 05 - the-audit-that-was-eye-opening",
                 folder=(BIN_REELS, "50-50")),
        timeline("t-c1", "Reel 01 - geo-is-comprehension-not-position (harvest)",
                 folder=(BIN_REELS, "Didn't make the cut")),
        timeline("t-vox", "Reel 09 - your-website-is-only-20-percent (vox test)",
                 folder=("VOX test",)),
    ]
    plan = a_plan(artefacts, current_reels=["Reel 01 - seo-ranks-geo-understands"],
                  archived_plan_names=["Reel 05 - the-audit-that-was-eye-opening"])
    assert plan.verdicts == []
    assert plan.moves == []
    assert plan.stamps == []
    left = {name for name, _ in plan.left_alone}
    assert {"Reel 01 - seo-ranks-geo-understands",
            "Reel 05 - the-audit-that-was-eye-opening",
            "Reel 01 - geo-is-comprehension-not-position (harvest)",
            "Reel 09 - your-website-is-only-20-percent (vox test)"} <= left
    # A project the captain organised reads as organised: the gate must
    # not fail correct output (AGENTS.md 10.4).
    assert findings(artefacts, a_plan(artefacts,
                    current_reels=["Reel 01 - seo-ranks-geo-understands"],
                    archived_plan_names=["Reel 05 - the-audit-that-was-eye-opening"])) == []


# ------------------------------------------------------- non-destructive


DELETE_CALLS = ("DeleteFolders", "DeleteClips", "DeleteTimelines",
                "DeleteClipMattes")


def test_no_module_here_can_delete_anything():
    """Organising must never delete a timeline. Asserted of the SOURCE,
    because a reviewer reading a docstring cannot see a call that is
    not there and a test can."""
    for module in ("library/tools/resolve_organization.py",
                   "library/tools/execution/organise_media_pool.py"):
        source = Path(module).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#"))
        executable = "".join(code.split('"""')[::2])
        for call in DELETE_CALLS:
            assert f"{call}(" not in executable, (
                f"{module} calls {call} - organising must never delete "
                f"(AGENTS.md 5, the captain's ruling of 2026-09-06)")


def test_the_unplaced_report_counts_only_what_this_pipeline_generated():
    """Footage from outside the project is not this pipeline's leftover,
    however long it sits in the pool unused."""
    report = unplaced_report(a_project(), PROJECT_ROOT)
    assert report["count"] == 1
    assert report["paths"] == (
        f"{PROJECT_ROOT}/pipeline_output/steps/4_05_render_subtitles/c.mov",)
    # `flare.mov` is unplaced too, and comes from outside the project.
    assert not any("flare" in path for path in report["paths"])

    # `Akshita` on the field test is unplaced with no file path, so
    # nothing shows a run wrote it: source material, not a leftover.
    report = unplaced_report(a_project() + [clip("c-nofile", "Akshita", path="")],
                             PROJECT_ROOT)
    assert report["count"] == 1
    assert len(report["paths"]) == report["count"], (
        "count and paths must be the same population, or a caller sizing "
        "`paths` under-reports `count` with nothing saying so")

    # Removing a pool item whose FILE a placed item also uses is safe;
    # deleting the file is not. The two must not be one number.
    shared = (f"{PROJECT_ROOT}/pipeline_output/steps/"
              f"4_05_render_subtitles/shared.mov")
    artefacts = a_project() + [
        clip("c-dup-placed", "sub_dup.mov", path=shared,
             placed_by=["Reel 01 - live (harvest)"]),
        clip("c-dup-orphan", "sub_dup.mov", path=shared),
    ]
    report = unplaced_report(artefacts, PROJECT_ROOT)
    assert report["count"] == 2
    assert report["shared_with_placed"] == (shared,)
