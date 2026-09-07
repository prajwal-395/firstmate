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
    BIN_SOURCE,
    BIN_SUBTITLES,
    BIN_UNPLACED,
    CURRENT,
    EARLIER,
    STATE_BINS,
    STATE_CLIP_COLOURS,
    STATES,
    TAG_PREFIX,
    UNRECORDED,
    Artefact,
    OrganizationError,
    assert_organized,
    findings,
    is_generated,
    plan_organization,
    reel_state,
    render_plan,
    state_from_keywords,
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
             path=f"{PROJECT_ROOT}/pipeline_output/scratch/x/a.mov",
             placed_by=["Reel 01 - live (harvest)"]),
        clip("c-old", "sub_old_a.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/scratch/x/b.mov",
             placed_by=["Reel 01 - superseded"]),
        clip("c-orphan", "sub_orphan.mov",
             path=f"{PROJECT_ROOT}/pipeline_output/scratch/x/c.mov"),
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
        built_reels=kw.pop("built_reels", BUILT),
        archived_plan_names=kw.pop("archived_plan_names", ARCHIVED),
        plan_hash=kw.pop("plan_hash", "1cf79aebb3c6" + "0" * 52),
        built_at=kw.pop("built_at", "2026-09-07T00:36:28+00:00"),
        **kw)


# ---------------------------------------------------------------- states


def test_the_live_provenance_record_makes_a_reel_current():
    assert reel_state("Reel 01 - live (harvest)", BUILT, ARCHIVED)[0] == CURRENT


def test_an_archived_plan_name_makes_a_reel_earlier():
    state, why = reel_state("Reel 01 - superseded", BUILT, ARCHIVED)
    assert state == EARLIER
    assert "archived" in why


def test_a_name_no_plan_carries_is_unrecorded_not_guessed_by_prefix():
    """The eight one-offs on the field test are a plan name plus a typed
    suffix.  Calling them EARLIER would decide by prefix, which is what
    AGENTS.md 5 forbids for exactly this reason."""
    state, why = reel_state("Reel 03 - superseded (fragment fix)",
                            BUILT, ARCHIVED)
    assert state == UNRECORDED
    assert "no plan" in why


def test_every_state_has_a_bin_and_a_colour():
    assert set(STATE_BINS) == set(STATES) == set(STATE_CLIP_COLOURS)


# ----------------------------------------------------------------- rules


def test_generated_is_a_path_fact_about_the_project_directory():
    assert is_generated(f"{PROJECT_ROOT}/pipeline_output/a.mov", PROJECT_ROOT)
    assert not is_generated("/elsewhere/a.mov", PROJECT_ROOT)
    assert not is_generated("", PROJECT_ROOT)
    assert not is_generated(f"{PROJECT_ROOT}-other/a.mov", PROJECT_ROOT)


def test_the_master_timeline_is_never_moved():
    plan = a_plan()
    assert MASTER not in [v.name for v in plan.verdicts]
    assert MASTER in [name for name, _ in plan.left_alone]
    assert MASTER not in [v.name for v in plan.moves]


def test_organising_without_a_master_name_refuses():
    with pytest.raises(OrganizationError, match="master"):
        plan_organization(a_project(), PROJECT_ROOT, "", BUILT, ARCHIVED)


def test_each_reel_files_under_its_state():
    dest = {v.name: v.destination for v in a_plan().verdicts}
    assert dest["Reel 01 - live (harvest)"] == (BIN_REELS, STATE_BINS[CURRENT])
    assert dest["Reel 01 - superseded"] == (BIN_REELS, STATE_BINS[EARLIER])
    assert dest["Reel 03 - superseded (fragment fix)"] == (
        BIN_REELS, STATE_BINS[UNRECORDED])


def test_a_generated_clip_files_under_the_one_timeline_that_places_it():
    dest = {v.name: v.destination for v in a_plan().verdicts}
    assert dest["sub_live_a.mov"] == (
        BIN_SUBTITLES, "Reel 01 - live (harvest)")
    assert dest["sub_old_a.mov"] == (
        BIN_SUBTITLES, "Reel 01 - superseded")


def test_a_caption_bin_does_not_move_when_its_reel_changes_state():
    """A captions tree mirroring the reel's state would strand an empty
    bin on every plan change, and nothing here may delete one."""
    artefacts = a_project()
    live = {v.name: v.destination for v in a_plan(artefacts).verdicts}
    demoted = {v.name: v.destination
               for v in a_plan(artefacts, built_reels=[]).verdicts}
    assert live["sub_live_a.mov"] == demoted["sub_live_a.mov"]
    assert live["Reel 01 - live (harvest)"] != \
        demoted["Reel 01 - live (harvest)"]


def test_a_generated_clip_no_timeline_places_says_so():
    dest = {v.name: v.destination for v in a_plan().verdicts}
    assert dest["sub_orphan.mov"] == (BIN_SUBTITLES, BIN_UNPLACED)


def test_footage_from_outside_the_project_is_source_footage():
    by_name = {v.name: v for v in a_plan().verdicts}
    assert by_name["podcast_cam_a.mov"].destination == (BIN_SOURCE,)
    assert by_name["flare.mov"].destination == (BIN_SOURCE,)
    assert "outside the project" in by_name["flare.mov"].why


def test_a_generated_clip_several_timelines_place_belongs_to_no_single_one():
    shared = clip("c-shared", "shared.mov",
                  path=f"{PROJECT_ROOT}/pipeline_output/x.mov",
                  placed_by=["Reel 01 - live (harvest)",
                             "Reel 01 - superseded"])
    plan = a_plan(a_project() + [shared])
    by_name = {v.name: v for v in plan.verdicts}
    assert by_name["shared.mov"].destination == (BIN_SOURCE,)
    assert "no single one" in by_name["shared.mov"].why


def test_every_verdict_carries_its_evidence():
    assert all(v.why.strip() for v in a_plan().verdicts)


def test_every_bin_the_plan_files_into_is_in_its_folder_list():
    plan = a_plan()
    for v in plan.verdicts:
        assert v.destination in plan.folders, v.destination_path


def test_parent_bins_are_listed_before_their_children():
    folders = a_plan().folders
    for index, path in enumerate(folders):
        if len(path) > 1:
            assert path[:-1] in folders[:index]


def test_an_item_already_in_place_is_not_a_move():
    settled = [
        timeline("t-master", MASTER),
        timeline("t-cur", "Reel 01 - live (harvest)",
                 folder=(BIN_REELS, STATE_BINS[CURRENT])),
    ]
    plan = a_plan(settled)
    assert [v.name for v in plan.moves] == []
    assert len(plan.verdicts) == 1


def test_organising_twice_changes_nothing_the_second_time():
    """Idempotence, on the plan side: file everything, re-read, re-plan."""
    first = a_plan()
    settled = []
    for a in a_project():
        match = next((v for v in first.verdicts if v.item_id == a.item_id),
                     None)
        settled.append(a if match is None
                       else Artefact(a.item_id, a.name, a.kind, a.file_path,
                                     a.placed_by, match.destination))
    assert a_plan(settled).moves == []


# ---------------------------------------------------------------- stamps


def test_a_current_reel_is_stamped_with_the_plan_that_built_it():
    stamps = {s["name"]: s for s in a_plan().stamps}
    keywords = stamps["Reel 01 - live (harvest)"]["fields"]["Keywords"]
    assert f"{TAG_PREFIX}state={CURRENT}" in keywords
    assert f"{TAG_PREFIX}plan=1cf79aebb3c6" in keywords
    assert stamps["Reel 01 - live (harvest)"]["clip_color"] == \
        STATE_CLIP_COLOURS[CURRENT]


def test_a_stamp_only_uses_metadata_keys_resolve_accepts():
    """`SetMetadata` returns False and stores nothing for a key Resolve
    does not know - measured on 21.0.0b.28."""
    accepted = {"Comments", "Keywords", "Description", "Scene", "Shot",
                "Take", "Angle", "Reel Number", "Move", "Day / Night",
                "Camera #", "Production Name", "Episode Name", "Shot Type",
                "Environment", "Genre", "People", "Location"}
    for stamp in a_plan().stamps:
        assert set(stamp["fields"]) <= accepted, stamp["fields"]


def test_only_reel_timelines_are_stamped():
    stamped = {s["name"] for s in a_plan().stamps}
    assert MASTER not in stamped
    assert "sub_live_a.mov" not in stamped


def test_an_earlier_reel_is_not_stamped_with_the_live_plan_hash():
    stamps = {s["name"]: s for s in a_plan().stamps}
    assert "plan=" not in stamps["Reel 01 - superseded"]["fields"]["Keywords"]


def test_state_reads_back_off_the_keywords_that_were_written():
    for stamp in a_plan().stamps:
        assert state_from_keywords(
            stamp["fields"]["Keywords"]) == stamp["state"]


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


def test_the_gate_passes_on_a_correctly_filed_project():
    """A gate that fails correct output is the same defect as one that
    cannot fail (AGENTS.md 10.4)."""
    artefacts = a_project()
    plan = a_plan(artefacts)
    settled = _filed(artefacts, plan)
    recorded = {s["item_id"]: s["state"] for s in plan.stamps}
    assert findings(settled, a_plan(settled), (), recorded) == []
    assert_organized([])


def test_the_gate_fails_a_misfiled_timeline():
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


def test_the_gate_fails_a_duplicate_bin():
    """`AddSubFolder` makes a second bin of the same name on every call -
    measured - so half the reels can file into each."""
    artefacts = a_project()
    plan = a_plan(artefacts)
    settled = _filed(artefacts, plan)
    found = findings(settled, a_plan(settled), [f"{BIN_REELS}"])
    assert [f["kind"] for f in found] == ["duplicate_bin"]


def test_the_gate_fails_a_state_stamp_that_no_longer_matches_the_record():
    artefacts = a_project()
    plan = a_plan(artefacts)
    settled = _filed(artefacts, plan)
    stale = {s["item_id"]: EARLIER for s in plan.stamps
             if s["state"] == CURRENT}
    found = findings(settled, a_plan(settled), (), stale)
    assert [f["kind"] for f in found] == ["stale_state"]


def test_the_gate_fails_an_item_that_left_the_pool():
    artefacts = a_project()
    plan = a_plan(artefacts)
    settled = [a for a in _filed(artefacts, plan) if a.item_id != "c-orphan"]
    found = findings(settled, plan)
    assert any(f["kind"] == "missing_item" for f in found)


def test_a_finding_names_the_item_and_says_what_is_wrong():
    artefacts = a_project()
    found = findings(artefacts, a_plan(artefacts))
    assert found
    for f in found:
        assert f["name"] and f["detail"] and f["kind"]


# ------------------------------------------------------- non-destructive


DELETE_CALLS = ("DeleteFolders", "DeleteClips", "DeleteTimelines",
                "DeleteClipMattes")


@pytest.mark.parametrize("module", [
    "library/tools/resolve_organization.py",
    "library/tools/execution/organise_media_pool.py",
])
def test_no_module_here_can_delete_anything(module):
    """Organising must never delete a timeline. Asserted of the SOURCE,
    because a reviewer reading a docstring cannot see a call that is
    not there and a test can."""
    source = Path(module).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#"))
    body = code.split('"""')
    executable = "".join(body[::2])
    for call in DELETE_CALLS:
        assert f"{call}(" not in executable, (
            f"{module} calls {call} - organising must never delete "
            f"(AGENTS.md 5, the captain's ruling of 2026-09-06)")


def test_the_plan_renders_something_an_operator_can_read():
    text = render_plan(a_plan())
    assert BIN_REELS in text and "would move" in text
    assert MASTER in text
    for state in STATES:
        assert state in text
