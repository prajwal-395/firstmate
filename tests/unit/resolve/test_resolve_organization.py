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
import json
from library.tools.execution import organise_media_pool as ex
from library.tools.resolve_organization import (
    BIN_SOURCE,
    CURRENT,
)
from tests.resolve_double import FakeProject, make_pool_clip, place_clip
import os
import shutil
import subprocess
from unittest.mock import patch
from library.tools import pool_stream_meta
import sys
from library.tools.resolve_relinker import (
    DestinationMismatchError,
    relink_project,
    scan_offline_clips,
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

    # `SpeakerOne` on the field test is unplaced with no file path, so
    # nothing shows a run wrote it: source material, not a leftover.
    report = unplaced_report(a_project() + [clip("c-nofile", "SpeakerOne", path="")],
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


# --------------------------------------------------------------------------
# From test_organise_media_pool.py
#
# The executor's own behaviour, against a media pool that is not Resolve.
#
# The canonical double answers the way the real API was measured to answer
# on 21.0.0b.28 - `AddSubFolder` makes a SECOND folder of a name that
# already exists and becomes current, `SetMetadata` refuses a key Resolve
# does not know, `MoveClips` returns a bool - so a test failing here is a
# rule being broken and not the double being wrong.  What the API actually does is written down in
# `library/tools/execution/organise_media_pool.py`.

MASTER_2 = "Main Edit"


@pytest.fixture
def project(tmp_path):
    """A project directory and a pool shaped like the field test's."""
    proj = FakeProject("Fake")
    pool = proj.GetMediaPool()
    master = pool.CreateEmptyTimeline(MASTER_2)
    live = pool.CreateEmptyTimeline("Reel 01 - live")
    pool.CreateEmptyTimeline("Reel 09 - old")
    steps = tmp_path / "pipeline_output" / "steps" / "4_05_render_subtitles"
    root = pool.GetRootFolder()
    cap = root.add_clip(make_pool_clip("sub_a.mov", path=str(steps / "a.mov")))
    root.add_clip(make_pool_clip("sub_b.mov", path=str(steps / "b.mov")))
    source = root.add_clip(make_pool_clip("cam.mov", path="/elsewhere/cam.mov"))
    place_clip(master, source, 0, 47)
    place_clip(live, cap, 0, 47)
    place_clip(live, source, 0, 47)

    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "plan_provenance.json").write_text(json.dumps({
        "plan_path": str(review / "reel_proposals_v2.json"),
        "plan_content_hash": "a" * 64,
        "built_at": "2026-09-07T00:00:00+00:00",
        "built_reels": ["Reel 01 - live"],
    }), encoding="utf-8")
    (review / "reel_proposals_v2_20260901T000000Z.json").write_text(
        json.dumps({"moments": [{"timeline_name": "Reel 09 - old"},
                                {"timeline_name": "Reel 01 - live"}]}),
        encoding="utf-8")
    (tmp_path / "project.yaml").write_text(
        "resolve:\n  project_name: Fake\n  timeline_name: Main Edit\n",
        encoding="utf-8")
    return proj, str(tmp_path)


def bins_of(folder, path=()):
    out = {}
    for clip in folder.GetClipList():
        out[clip.GetName()] = "/".join(path)
    for sub in folder.GetSubFolderList():
        out.update(bins_of(sub, path + (sub.GetName(),)))
    return out


# ------------------------------------------------------------- reading


def test_read_pool_measures_which_timeline_places_what(project):
    proj, _folder = project
    artefacts, duplicates, _recorded, root_name = ex.read_pool(proj)
    by_name = {a.name: a for a in artefacts}
    assert by_name["sub_a.mov"].placed_by == ("Reel 01 - live",)
    assert by_name["sub_b.mov"].placed_by == ()
    assert by_name["cam.mov"].placed_by == (MASTER_2, "Reel 01 - live")
    assert by_name[MASTER_2].kind == "timeline"
    assert duplicates == [] and root_name == "Master"


# ------------------------------------------------------------ applying


def test_apply_files_everything_where_the_evidence_says(project):
    proj, folder = project
    result = ex.organise_project(proj, folder, MASTER_2, apply=True)
    where = bins_of(proj.GetMediaPool().GetRootFolder())
    assert where["Reel 01 - live"] == f"{BIN_REELS}/{STATE_BINS['current']}"
    assert where["Reel 09 - old"] == f"{BIN_REELS}/{STATE_BINS['earlier']}"
    assert where["sub_a.mov"] == f"{BIN_SUBTITLES}/Reel 01 - live"
    assert where["sub_b.mov"] == f"{BIN_SUBTITLES}/Not placed on any timeline"
    assert where["cam.mov"] == BIN_SOURCE
    assert where[MASTER_2] == ""          # the master is never moved
    assert result["applied"]


def test_apply_stamps_the_reels_with_the_plan_that_built_them(project):
    proj, folder = project
    ex.organise_project(proj, folder, MASTER_2, apply=True)
    live = next(c for c in _all_clips(proj) if c.GetName() == "Reel 01 - live")
    assert f"state={CURRENT}" in live.GetMetadata("Keywords")
    assert "plan=aaaaaaaaaaaa" in live.GetMetadata("Keywords")
    assert live.GetClipProperty("Clip Color") == "Green"
    master = next(c for c in _all_clips(proj) if c.GetName() == MASTER_2)
    assert master.GetMetadata("Keywords") == ""
    assert master.GetClipProperty("Clip Color") == ""


def _all_clips(proj):
    def walk(folder):
        yield from folder.GetClipList()
        for sub in folder.GetSubFolderList():
            yield from walk(sub)
    return list(walk(proj.GetMediaPool().GetRootFolder()))


def test_applying_twice_moves_nothing_the_second_time(project):
    proj, folder = project
    first = ex.organise_project(proj, folder, MASTER_2, apply=True)
    before = bins_of(proj.GetMediaPool().GetRootFolder())
    second = ex.organise_project(proj, folder, MASTER_2, apply=True)
    assert len(first["journal"]["moves"]) > 0
    assert second["journal"]["moves"] == []
    assert bins_of(proj.GetMediaPool().GetRootFolder()) == before


def test_apply_restores_the_current_folder_it_found(project):
    """`AddSubFolder` sets the current folder, and the current folder is
    where `CreateEmptyTimeline` puts the next timeline."""
    proj, folder = project
    pool = proj.GetMediaPool()
    before = pool.GetCurrentFolder()
    ex.organise_project(proj, folder, MASTER_2, apply=True)
    assert pool.GetCurrentFolder() is before


def test_the_check_passes_once_it_is_organised_and_fails_before(project):
    proj, folder = project
    assert ex.check_project(proj, folder, MASTER_2)          # fails dirty
    ex.organise_project(proj, folder, MASTER_2, apply=True)
    assert ex.check_project(proj, folder, MASTER_2) == []    # passes clean


def test_the_check_fails_a_timeline_moved_to_the_wrong_bin(project):
    proj, folder = project
    ex.organise_project(proj, folder, MASTER_2, apply=True)
    pool = proj.GetMediaPool()
    earlier = ex._find_path(pool.GetRootFolder(),
                            (BIN_REELS, STATE_BINS["earlier"]))
    live = next(c for c in _all_clips(proj) if c.GetName() == "Reel 01 - live")
    pool.MoveClips([live], earlier)
    found = ex.check_project(proj, folder, MASTER_2)
    assert [f["kind"] for f in found] == ["misfiled"]


# ------------------------------------------------------------ the journal


def test_a_second_apply_cannot_destroy_the_first_journals_undo(project):
    """The defect this closes: with one fixed filename, the second apply
    moves nothing, writes that over the first record, and the whole
    organisation becomes irreversible."""
    proj, folder = project
    # Held, not recomputed: `journal_path_for` NAMES A NEW FILE and
    # suffixes past one that already exists
    # (`library/tools/journal_naming.py`), so asking again after the
    # write would answer with the sibling, not with the first journal.
    first_journal = ex.journal_path_for(folder, "A")
    ex.organise_project(proj, folder, MASTER_2, apply=True,
                        journal_path=first_journal)
    ex.organise_project(proj, folder, MASTER_2, apply=True,
                        journal_path=ex.journal_path_for(folder, "B"))
    undone = ex.revert(proj, first_journal)
    assert len(undone["moved_back"]) > 0
    assert bins_of(proj.GetMediaPool().GetRootFolder())["Reel 01 - live"] == ""


def test_revert_puts_every_item_and_every_stamp_back(project):
    proj, folder = project
    before = bins_of(proj.GetMediaPool().GetRootFolder())
    result = ex.organise_project(proj, folder, MASTER_2, apply=True)
    ex.revert(proj, result["journal"]["journal_path"])
    assert bins_of(proj.GetMediaPool().GetRootFolder()) == before
    for clip in _all_clips(proj):
        assert clip.GetMetadata("Keywords") == ""
        assert clip.GetClipProperty("Clip Color") == ""


def test_a_journal_is_written_even_when_the_apply_raises(project, monkeypatch):
    """A half-applied move nobody recorded is the one outcome with no
    way back."""
    proj, folder = project
    pool = proj.GetMediaPool()
    calls = {"n": 0}
    real_move = pool.MoveClips

    def flaky(clips, target):
        calls["n"] += 1
        if calls["n"] > 1:
            return False
        return real_move(clips, target)

    monkeypatch.setattr(pool, "MoveClips", flaky)
    path = ex.journal_path_for(folder, "X")
    with pytest.raises(OrganizationError, match="MoveClips"):
        ex.organise_project(proj, folder, MASTER_2, apply=True,
                            journal_path=path)
    written = json.loads(Path(path).read_text(encoding="utf-8"))
    assert written["moves"], "the moves that DID happen were not journalled"

    monkeypatch.setattr(pool, "MoveClips", real_move)
    ex.revert(proj, path)
    moved = {m["name"] for m in written["moves"]}
    where = bins_of(pool.GetRootFolder())
    assert all(where[name] == "" for name in moved), where


def test_a_project_that_names_no_master_timeline_refuses(project):
    _proj, folder = project
    Path(folder, "project.yaml").write_text(
        "resolve:\n  project_name: Fake\n", encoding="utf-8")
    with pytest.raises(OrganizationError, match="timeline_name"):
        ex.open_project(folder)


# ------------------------------------------ what nothing plays, and its cost


def test_the_unplaced_cost_is_measured_from_disk_not_assumed(project,
                                                             tmp_path):
    """The orphan's file is real here, so the size is a `stat` and not a
    guess. AGENTS.md 10.3: a file on disk is not a measurement - but its
    SIZE is, and it is the figure a removal decision turns on."""
    orphan = (tmp_path / "pipeline_output" / "steps"
              / "4_05_render_subtitles" / "b.mov")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"x" * 4096)
    proj, folder = project
    result = ex.organise_project(proj, folder, MASTER_2, apply=True)
    cost = result["unplaced"]
    assert cost["count"] == 1
    assert cost["on_disk"] == 1 and cost["missing"] == 0
    assert cost["bytes_on_disk"] == 4096
    assert cost["shared_with_placed"] == ()


def test_an_unplaced_item_whose_file_is_gone_is_counted_as_offline(project):
    """131 of the field test's 1,216 point at a file that no longer
    exists. Keeping the pool item recovers nothing, and a report that
    sized it as recoverable disk would be wrong by that much."""
    proj, folder = project
    cost = ex.organise_project(proj, folder, MASTER_2,
                               apply=True)["unplaced"]
    assert cost["count"] == 1
    assert cost["on_disk"] == 0 and cost["missing"] == 1
    assert cost["bytes_on_disk"] == 0


# --------------------------------------- retiring the migration's shells


def _shell_pool(project):
    """The captain's complaint in miniature: the migration emptied the
    legacy shells and left them standing beside the numbered bins."""
    proj, _folder = project
    pool = proj.GetMediaPool()
    root = pool.GetRootFolder()
    shells = pool.AddSubFolder(root, "Reels")
    pool.AddSubFolder(shells, "Unrecorded")
    pool.AddSubFolder(root, "Reel subtitles")
    pool.AddSubFolder(root, "My selects")
    pool.SetCurrentFolder(root)
    return proj


def _tree_names(proj):
    out = []

    def walk(folder, path):
        for sub in folder.GetSubFolderList():
            out.append("/".join(path + (sub.GetName(),)))
            walk(sub, path + (sub.GetName(),))
    walk(proj.GetMediaPool().GetRootFolder(), ())
    return sorted(out)


def test_the_check_flags_an_empty_legacy_shell(project):
    proj = _shell_pool(project)
    kinds = [f["kind"] for f in ex.check_project(proj, project[1], MASTER_2)]
    assert "empty_legacy_bin" in kinds


def test_apply_retires_the_shells_and_the_check_passes_after(project):
    """The migration's two-pass shape: read-only plan first, then act,
    then re-read and reconcile."""
    proj = _shell_pool(project)
    _, folder = project
    planned = ex.organise_project(proj, folder, MASTER_2, apply=False)
    assert not planned["applied"]
    assert "Reels" in planned["census"] and "RETIRE" in planned["census"]
    assert "My selects" in planned["census"]
    assert "Reels" in _tree_names(proj), "planning must change nothing"

    applied = ex.organise_project(proj, folder, MASTER_2, apply=True)
    assert applied["applied"]
    assert applied["retirement"]["retired"] == [
        "Reels/Unrecorded", "Reel subtitles", "Reels"]
    remaining = _tree_names(proj)
    assert "Reels" not in remaining
    assert "Reels" not in remaining
    assert "Reel subtitles" not in remaining
    assert "My selects" in remaining
    assert ex.check_project(proj, folder, MASTER_2) == []


def test_a_shell_holding_the_captains_tier_is_left_where_it_is(project):
    """`Reels/Fully approved` is the captain's organisation: the tier
    stays, and `Reels` above it stays too - retiring the parent would
    take the captain's bin with it."""
    proj = _shell_pool(project)
    _, folder = project
    pool = proj.GetMediaPool()
    shells = next(s for s in pool.GetRootFolder().GetSubFolderList()
                  if s.GetName() == "Reels")
    pool.AddSubFolder(shells, "Fully approved")
    pool.CreateEmptyTimeline("Reel 01 - mine")  # lands in the new bin
    pool.SetCurrentFolder(pool.GetRootFolder())
    result = ex.organise_project(proj, folder, MASTER_2, apply=True)
    assert result["retirement"]["retired"] == [
        "Reels/Unrecorded", "Reel subtitles"]
    remaining = _tree_names(proj)
    assert "Reels" in remaining
    assert "Reels/Fully approved" in remaining


# --------------------------------------------------------------------------
# From test_pool_stream_meta_refresh.py
#
# A pool item whose cached stream metadata disagrees with its file is
# refreshed at the build's safe rebinding point, not reused.
#
# History: docs/evidence/resolve_test_history.md#test_pool_stream_meta_refresh.

# ── The comparison ──────────────────────────────────────────────

def test_stream_disagreement_fires_each_leg_and_never_on_ignorance():
    """Reel 26: pool `866x480` against disk `904x480` is stale, whatever
    else. Same dimensions transcoded underneath (`transcode_in_place`
    keeps the path and pixels, turns only the codec over) fires the
    codec leg. A missing file or an unparseable pool Resolution is
    incomparable, never stale; and a codec name the table has not
    learned is NOT staleness - flagging it would read every camera
    format in the pool as stale (AGENTS.md 10.4)."""
    found = pool_stream_meta.stream_disagreement(
        {"width": 866, "height": 480, "codec": None},
        {"width": 904, "height": 480, "codec": "qtrle"})
    assert found["mismatches"] == ["resolution"]
    assert found["pool_resolution"] == "866x480"
    assert found["disk_resolution"] == "904x480"
    assert found["codec_compared"] is False

    found = pool_stream_meta.stream_disagreement(
        {"width": 904, "height": 480, "codec": "apple prores 4444"},
        {"width": 904, "height": 480, "codec": "qtrle"})
    assert found["mismatches"] == ["codec"]
    assert found["codec_compared"] is True

    unknown = {"width": None, "height": None, "codec": None}
    assert pool_stream_meta.stream_disagreement(
        {"width": 866, "height": 480, "codec": None}, unknown) is None
    assert pool_stream_meta.stream_disagreement(
        unknown, {"width": 904, "height": 480, "codec": "qtrle"}) is None

    assert pool_stream_meta.codecs_agree("xavc high l5.1", "h264") is None
    pool = {"width": 3840, "height": 2160, "codec": "xavc high l5.1"}
    disk = {"width": 3840, "height": 2160, "codec": "h264"}
    assert pool_stream_meta.stream_disagreement(pool, disk) is None
    found = pool_stream_meta.stream_disagreement(
        dict(pool, width=1920, height=1080), disk)
    assert found["mismatches"] == ["resolution"]
    assert found["codec_compared"] is False


def test_pool_codec_is_the_video_one_never_the_first_codec_key():
    """THE INPUT THAT BROKE THIS: Resolve 21.1's property dict, in its
    real order. Three keys match `codec` and `Audio Codec` comes
    first, so a plain substring match returned `Linear PCM` for every
    overlay artefact in the project on 2026-09-13 and compared an
    audio codec against a video one. `Codec Bitrate` is not a codec
    either."""

    class _Item:
        def GetClipProperty(self, key=None):
            if key is None:
                return {"Audio Codec": "Linear PCM", "Camera Format": "",
                        "Codec Bitrate": "", "Format": "QuickTime",
                        "Resolution": "904x480",
                        "Video Codec": "Apple ProRes 4444"}
            return {"Resolution": "904x480"}.get(key, "")

    stream = pool_stream_meta.pool_stream(_Item())
    assert (stream["width"], stream["height"]) == (904, 480)
    assert stream["codec"] == "apple prores 4444"


def test_disk_stream_reads_a_real_file_by_name_not_by_position(tmp_path):
    """THE DEFECT THIS CLOSES, against a real ffprobe.

    `-show_entries` selects fields and does NOT order them: ffprobe
    prints its own stream order, so `stream=width,height,codec_name`
    emits `qtrle,904,480`. The positional parse landed in #1089 read
    `qtrle` as the width, raised `ValueError`, and returned all-None -
    so every comparison read `comparable: False` and the staleness
    check could not fire while 13 stale items sat in the pool.

    A synthesised qtrle file at a NON-SQUARE size, so a transposed
    width and height cannot pass either."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("needs ffmpeg and ffprobe - CI installs both "
                    "(AGENTS.md 9, 'What CI actually checks')")
    made = tmp_path / "overlay.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=black:s=904x480:d=0.2:r=30",
         "-c:v", "qtrle", str(made)],
        check=True, capture_output=True, encoding="utf-8")
    assert pool_stream_meta.disk_stream(str(made)) == {
        "width": 904, "height": 480, "codec": "qtrle"}


# ── The build refresh ───────────────────────────────────────────

class _Clip:
    """A pooled overlay item with readable stream metadata."""

    def __init__(self, path, resolution="904x480", codec=None):
        self._path = path
        self._resolution = resolution
        self._codec = codec

    def GetClipProperty(self, key=None):
        table = {"File Path": self._path,
                 "Clip Name": os.path.basename(self._path),
                 "Resolution": self._resolution}
        if key is None:
            if self._codec is not None:
                table = dict(table, **{"Audio Codec": "Linear PCM",
                                       "Video Codec": self._codec})
            return dict(table)
        return table.get(key, "")

    def GetName(self):
        return os.path.basename(self._path)


class _Folder:
    def __init__(self):
        self._clips = []
        self._subs = []

    def GetName(self):
        return "bin"

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _Pool:
    """A pool that imports fresh-metadata items and never deletes."""

    def __init__(self, fresh_resolution="904x480", fresh_codec="qtrle"):
        self.root = _Folder()
        self.deletes = []
        self.imports = []
        self._fresh = (fresh_resolution, fresh_codec)

    def GetRootFolder(self):
        return self.root

    def ImportMedia(self, paths):
        self.imports.append(list(paths))
        items = [_Clip(p, self._fresh[0], self._fresh[1]) for p in paths]
        self.root._clips.extend(items)
        return items

    def DeleteClips(self, items):
        self.deletes.append(list(items))
        return True


def _overlay(tmp_path, name="sub_speakertwo_162243-166986_e135147f.mov"):
    path = str(tmp_path / name)
    with open(path, "wb") as handle:
        handle.write(b"\x00")
    return path


def _disk_as(width, height, codec):
    return patch.object(pool_stream_meta, "disk_stream",
                        return_value={"width": width, "height": height,
                                      "codec": codec})


def test_stale_hit_imports_fresh_and_never_deletes(tmp_path):
    """THE breaking input, end to end at the import: pooled `866x480`
    against a disk stub of `904x480`/qtrle. The build must place the
    fresh item, must not touch DeleteClips, and must leave the stale
    item pooled for the archive that still plays it."""
    from library.tools.reel_build import import_pool_item

    path = _overlay(tmp_path)
    pool = _Pool()
    stale = _Clip(path, resolution="866x480", codec="Apple ProRes 4444")
    pool.root._clips.append(stale)
    with _disk_as(904, 480, "qtrle"):
        placed = import_pool_item(pool, path)
    assert placed is not stale
    assert pool.imports == [[path]]
    assert pool.deletes == []
    assert stale in pool.root._clips


def test_a_fresh_hit_reuses_without_importing(tmp_path):
    """Agreement reuses as before: no second item, no probe-driven
    churn. This is the 2026-09-09 duplication lesson, held."""
    from library.tools.reel_build import import_pool_item

    path = _overlay(tmp_path)
    pool = _Pool()
    item = _Clip(path, resolution="904x480", codec="Animation")
    pool.root._clips.append(item)
    with _disk_as(904, 480, "qtrle"):
        assert import_pool_item(pool, path) is item
    assert pool.imports == []
    assert pool.deletes == []

    # After one refresh the path holds two items: the next build binds
    # the fresh one WITHOUT importing a third - otherwise every rebuild
    # grows the pool by the whole overlay set again.
    pool = _Pool()
    stale = _Clip(path, resolution="866x480", codec="Apple ProRes 4444")
    fresh = _Clip(path, resolution="904x480", codec="Animation")
    pool.root._clips.extend([stale, fresh])
    with _disk_as(904, 480, "qtrle"):
        assert import_pool_item(pool, path) is fresh
    assert pool.imports == []
    assert pool.deletes == []


# --------------------------------------------------------------------------
# From test_relinker_destination_guard.py
#
# H12 - relink_project rewrites the media pool of whatever project is open.
#
# History: docs/evidence/resolve_test_history.md#test_relinker_destination_guard.

# ── Mock helpers ────────────────────────────────────────────────

class MockClip:
    def __init__(self, name, file_path, exists=False):
        self._name = name
        self._file_path = file_path
        self._exists = exists
        self.replaced_with = None

    def GetName(self):
        return self._name

    def GetClipProperty(self, key):
        if key == "File Path":
            return self._file_path
        return ""

    def ReplaceClip(self, new_path):
        self.replaced_with = new_path
        return True


class MockFolder:
    def __init__(self, clips=None, subfolders=None):
        self._clips = clips or []
        self._subfolders = subfolders or []

    def GetClipList(self):
        return self._clips

    def GetSubFolderList(self):
        return self._subfolders


class MockMediaPool:
    def __init__(self, root_folder=None):
        self._root = root_folder or MockFolder()

    def GetRootFolder(self):
        return self._root


class MockProject:
    def __init__(self, name, media_pool=None):
        self._name = name
        self._mp = media_pool or MockMediaPool()

    def GetName(self):
        return self._name

    def GetMediaPool(self):
        return self._mp

    def GetCurrentTimeline(self):
        return getattr(self, "_current_timeline", None)

    def SetCurrentTimeline(self, tl):
        self._current_timeline = tl
        return True

class MockProjectManager:
    def __init__(self, project=None):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class MockResolve:
    def __init__(self, pm=None):
        self._pm = pm or MockProjectManager()

    def GetProjectManager(self):
        return self._pm


# ── H12 Tests ──────────────────────────────────────────────────

class TestRelinkerDestinationGuard:
    """Writes verify their destination and refuse on mismatch."""

    def test_correct_project_passes(self, monkeypatch):
        """When expected_project matches, the relink proceeds."""
        project = MockProject("MyProject")
        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        # No offline clips, so it returns early after verification
        result = relink_project(
            expected_project="MyProject",
        )
        assert result["success"]

    def test_wrong_project_refused(self, monkeypatch):
        """When expected_project does not match, refuse with error."""
        project = MockProject("CaptainsRoughCut")
        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        with pytest.raises(DestinationMismatchError, match="CaptainsRoughCut"):
            relink_project(expected_project="PipelineProject")

    def test_no_project_open_refused(self, monkeypatch):
        """When no project is open and expected_project is set, refuse."""
        pm = MockProjectManager(project=None)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        with pytest.raises(DestinationMismatchError, match="No Resolve project"):
            relink_project(expected_project="SomeProject")

    def test_no_expected_project_backward_compatible(self, monkeypatch):
        """When expected_project is empty, no verification occurs."""
        project = MockProject("AnyProject")
        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        # Should not raise - backward compatible behavior
        result = relink_project(expected_project="")
        assert result["success"]

    def test_no_project_open_no_expected_returns_error_dict(self, monkeypatch):
        """When no project is open and no expected, returns error dict."""
        pm = MockProjectManager(project=None)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        result = relink_project(expected_project="")
        assert not result["success"]
        assert "No project open" in result["error"]


class TestRelinkerPreMutationVerification:
    """The project is re-verified immediately before the first mutation."""

    def test_project_changed_between_scan_and_mutation(self, monkeypatch, tmp_path):
        """If the project changes after scan but before mutation, refuse."""
        # Create a clip that appears offline
        clip = MockClip("clip1.mov", "/old/path/clip1.mov")
        folder = MockFolder(clips=[clip])
        media_pool = MockMediaPool(root_folder=folder)

        project = MockProject("PipelineProject", media_pool=media_pool)

        # Track calls to GetName and switch after enough calls for the
        # first verification + scan to pass.  The function calls GetName
        # at the initial check, then again immediately before mutation.
        call_count = [0]
        original_name = "PipelineProject"
        switched_name = "CaptainsProject"

        def changing_project_name():
            call_count[0] += 1
            # Switch on the last call (the pre-mutation re-verification)
            if call_count[0] > 1:
                return switched_name
            return original_name

        project.GetName = changing_project_name

        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )
        monkeypatch.setattr(
            "library.tools.resolve_relinker.build_path_mappings",
            lambda slug: [("/old/path/", str(tmp_path) + "/")],
        )

        # Create the file at the new path so the clip is fixable
        new_clip = tmp_path / "clip1.mov"
        new_clip.write_bytes(b"fake")

        with pytest.raises(DestinationMismatchError, match="Project changed"):
            relink_project(
                expected_project="PipelineProject",
            )


class TestRelinkerDestinationMismatchErrorDocs:
    """The error class documents the hazard."""
    pass
