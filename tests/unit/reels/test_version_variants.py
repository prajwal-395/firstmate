"""Variations as branches, merging declarations, rebuilding the final.

Covers library/tools/versions/variants.py and the canonical JSON
spelling in library/tools/stable_json.py:

- branch <-> timeline names derive from each other, both ways, on the
  exact shapes the captain has in Resolve today (` (j-cut)`,
  ` (reaction-cutaway)`);
- the spec expresses a SEAM or a DECLARATION derived from
  `external_inputs.DECLARATIONS` and nothing else, and writes canonically
  (same record, same bytes - otherwise every variant merge conflicts);
- the conformance sweep skips a variant only when it is DECLARED;
- creating a variation branches, records and commits, and refuses a
  dirty tree;
- combining variations merges declarations while generated run state
  auto-resolves to the target side, proved on the captain's real case:
  a cutaway variation merged with a CTA-and-grade variation;
- declaration conflicts reach a human; marker pulls union-merge.

No Resolve, no renders, no pipeline runs: every merge here is git on
fixture files with the production shapes.
"""
from __future__ import annotations
import json
import subprocess
import pytest
from library.tools.versions import store as bvc
from library.tools.versions import variants as tv
import pathlib
from library.tools import reel_replace_guard
from library.tools import reel_retirement as retire
from library.tools.versions import variants as choice
from tests.promotion_test_helpers import record_ren_owned_inventory
from tests.resolve_double import FakeProject, FakeTimeline
import ast
from pathlib import Path
from unittest.mock import patch
from library.tools import capability_outputs
from library.processes.edit_video.run_pipeline import (
    load_pipeline_state,
    run_pipeline,
)


BASE_TIMELINE = "Reel 09 - your-website-is-only-20-percent (final)"
CUTAWAY_SUFFIX = " (reaction-cutaway)"
JCUT_SUFFIX = " (j-cut)"

CUTAWAY_SPEC = {
    "suffix": CUTAWAY_SUFFIX,
    "cutaway": {
        "hide_angle": "SpeakerTwo",
        "window_seconds": [574, 598],
        "cover_words": ["exactly", "why"],
    },
    "cover": {
        "source_file": "/footage/LC4932.MXF",
        "source_in": 1450.9495,
        "source_out": 1451.9505,
    },
    "watch": "SpeakerTwo hides, SpeakerOne covers 24 frames",
}

CTA_EDIT = {
    "key": "captain_edits",
    "source": "captain",
    "value": [{
        "kind": "redraw_closer",
        "anchor": "it's exactly why",
        "new_start": 481.311,
    }],
}


def _git(project, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _write(project, rel, content):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _base_project(project):
    """A versioned project at its first commit, production file shapes."""
    assert bvc.init_project_repo(str(project))["initialised"] is True
    _write(project, "project.yaml", "name: demo\n")
    _write(project, "external/captain_edits.json", json.dumps(
        {"key": "captain_edits", "source": "captain", "value": []}))
    _write(project, "pipeline_data.json", json.dumps(
        {"last_updated": "2026-09-10T11:00:00",
         "edit_completed": {"select_reels": {}}}))
    _write(project, "pipeline_run.json", json.dumps({"mode": "x"}))
    _write(project, "pipeline_output/steps/5_04_compile_manifest/"
                    "assembly_manifest.json",
           json.dumps({"tracks": {"V1": {"clips": []}}}))
    _write(project, "marker_feedback/Reel_09.20260910T110000Z.markers.json",
           json.dumps({"format": "marker_feedback/1", "timeline": BASE_TIMELINE,
                       "notes": []}))
    result = bvc.commit_build(str(project), message="base\n")
    assert result["committed"] is True
    try:
        default = _git(project, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except AssertionError:
        default = "main"
    return default


# ── names ────────────────────────────────────────────────────────────

def test_names_derive_both_ways_and_final_is_not_a_suffix():
    for suffix in (CUTAWAY_SUFFIX, JCUT_SUFFIX):
        specs = ({"suffix": suffix},)
        branch = tv.variant_branch_name(9, suffix)
        timeline = tv.variant_timeline_name(BASE_TIMELINE, suffix)
        assert tv.branch_for_timeline(timeline, specs) == branch
        assert tv.timeline_for_branch(branch, BASE_TIMELINE, specs) == \
            timeline
    assert tv.variant_branch_name(9, CUTAWAY_SUFFIX) == \
        "variant/r09-reaction-cutaway"
    assert tv.variant_timeline_name(BASE_TIMELINE, CUTAWAY_SUFFIX) == \
        BASE_TIMELINE + CUTAWAY_SUFFIX
    # `(final)` is the approved timeline's own name. Only the spec
    # record tells a base-name paren from a variant suffix.
    specs = (dict(CUTAWAY_SPEC),)
    assert tv.branch_for_timeline(BASE_TIMELINE, specs) is None
    assert tv.branch_for_timeline(
        BASE_TIMELINE + " (something-undeclared)", specs) is None


# ── specs: what a variant can and cannot express ─────────────────────

def test_the_declarable_set_is_derived_not_listed_again():
    """A store added to `external_inputs.DECLARATIONS` becomes
    variant-expressible with no second edit; two lists would drift."""
    from library.tools import external_inputs

    assert set(tv.declarable()) == set(external_inputs.DECLARATIONS)
    assert len(tv.declarable()) >= 5


def test_valid_specs_pass_and_every_malformed_spec_is_refused():
    """A variant differs in a SEAM or a per-project DECLARATION and in
    nothing else (AGENTS.md 10.4). Each refused row names its reason."""
    for spec in (dict(CUTAWAY_SPEC),
                 {"suffix": " (cta-b)", "declares": ["reel_ending"],
                  "watch": "the close"},
                 {"suffix": " (reaction-cutaway)",
                  "cutaway": {"hide_angle": "A", "window_seconds": [12.4, 12.7]},
                  "declares": ["caption_timing"]}):
        assert tv.validate_variant_spec(spec) == [], spec
    refused = [
        ({"suffix": " (same)"}, "declares"),
        ({"suffix": CUTAWAY_SUFFIX, "watch": "look"}, "j_cut / cutaway"),
        (dict(CUTAWAY_SPEC, suffix="reaction-cutaway"), "suffix"),
        ({"suffix": " (a)", "declares": "reel_ending"}, ""),
        ({"suffix": " (a)", "declares": []}, ""),
        ({"suffix": " (a)", "declares": ["reel_ending", "reel_ending"]}, ""),
        ({"suffix": " (a)", "declares": ["reel_ending"],
          "transcript": "other.json"}, "unknown keys"),
    ]
    for spec, needle in refused:
        errors = tv.validate_variant_spec(spec)
        assert errors, spec
        assert any(needle in e for e in errors), (spec, errors)


def test_a_store_nothing_owns_is_refused_and_the_boundary_is_quoted():
    """`series_look` is a property of the SERIES: a variant differing in
    it would compare two series, not two treatments of one moment."""
    errors = tv.validate_variant_spec(
        {"suffix": " (warm)", "declares": ["series_look"]})
    message = " ".join(errors)
    assert "series_look" in message
    assert "reel_ending" in message
    assert "a different look, grade or delivery format" in message


def test_spec_write_is_canonical(tmp_path):
    record = {"format": tv.VARIANTS_FORMAT, "variants": {
        "9": [dict(CUTAWAY_SPEC),
              {"suffix": JCUT_SUFFIX,
               "j_cut": {"join_seconds": 0.4, "lead_seconds": 0.2}}],
        "10": [dict(CUTAWAY_SPEC, suffix=" (cover-10)")],
        "11": [{"suffix": " (a)",
                "declares": ["reel_ending", "caption_timing"]}],
    }}
    first = tv.write_variant_specs(str(tmp_path), record)
    before = (tmp_path / "pipeline_output" / "review"
              / tv.VARIANTS_FILENAME).read_text(encoding="utf-8")
    # Same record in a scrambled order is the same bytes.
    scrambled = {"variants": {
        "11": [{"suffix": " (a)",
                "declares": ["caption_timing", "reel_ending"]}],
        "10": [dict(CUTAWAY_SPEC, suffix=" (cover-10)")],
        "9": [dict(record["variants"]["9"][1]),
              dict(record["variants"]["9"][0])]}}
    tv.write_variant_specs(str(tmp_path), scrambled)
    after = (tmp_path / "pipeline_output" / "review"
             / tv.VARIANTS_FILENAME).read_text(encoding="utf-8")
    assert before == after
    assert first.endswith(tv.VARIANTS_FILENAME)
    assert after.endswith("\n")
    assert tv.read_variant_specs(str(tmp_path))["variants"]["11"][0][
        "declares"] == ["caption_timing", "reel_ending"]
    # Two specs with one suffix is not a canonical record at all.
    with pytest.raises(ValueError):
        tv.write_variant_specs(str(tmp_path), {"variants": {
            "9": [dict(CUTAWAY_SPEC), dict(CUTAWAY_SPEC)]}})


# ── which timelines are variants, and what the sweep grades ─────────

def test_the_conformance_sweep_grades_by_declaration_not_by_name():
    """A live variant's offsets are INTENTIONAL deviations, so the plan
    verifier does not grade it - but only when DECLARED: no syntactic
    rule tells `(final)` from `(reaction-cutaway)`. Archived names are
    never graded; a name the operator asks for always is."""
    from library.tools import reel_retirement as retire
    from library.tools.reel_conformance_verifier import grades_as_a_reel

    cutaway = BASE_TIMELINE + CUTAWAY_SUFFIX
    declared = {cutaway}
    assert grades_as_a_reel(BASE_TIMELINE, variant_names=declared)
    assert not grades_as_a_reel(cutaway, variant_names=declared)
    assert grades_as_a_reel(cutaway)
    assert grades_as_a_reel(cutaway, variant_names=set())
    assert grades_as_a_reel(cutaway, only_reels=[cutaway],
                            variant_names=declared)
    assert not grades_as_a_reel(retire.archived_name(BASE_TIMELINE, 3),
                                variant_names=declared)
    assert not grades_as_a_reel("Podcast - Synced", variant_names=declared)
    comparison = "Reel 13 - x (baseline scratch)"
    assert grades_as_a_reel(comparison)
    assert not grades_as_a_reel(retire.archived_name(comparison, 2))


def test_declared_variants_resolve_through_the_plans_own_reel_name(
        tmp_path, monkeypatch):
    """The reel's name comes from the proposal - the same read the
    variant builder makes. No record answers EMPTY."""
    project = tmp_path / "p"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    assert tv.declared_variant_timelines(str(project)) == set()
    tv.write_variant_specs(str(project), {"variants": {
        "9": [{"suffix": CUTAWAY_SUFFIX,
               "cutaway": {"hide_angle": "A", "window_seconds": [1, 2]}}],
        "13": [{"suffix": " (tight)", "declares": ["caption_timing"]}]}})

    class Moment:
        def __init__(self, number, name):
            self.number = number
            self.timeline_name = name

    monkeypatch.setattr(
        "library.tools.reel_proposal.read_proposal",
        lambda path: [Moment(9, BASE_TIMELINE),
                      Moment(13, "Reel 13 - the-accounting-firm (final)")])
    monkeypatch.setattr("library.tools.reel_proposal.proposal_path",
                        lambda folder: "unused")
    assert tv.declared_variant_timelines(str(project)) == {
        BASE_TIMELINE + CUTAWAY_SUFFIX,
        "Reel 13 - the-accounting-firm (final) (tight)"}


# ── create ───────────────────────────────────────────────────────────

def test_create_variation_branches_records_commits(tmp_path):
    default = _base_project(tmp_path)
    # Another lane's records sit untracked in a live project; they ride
    # no branch, so branching around them loses nothing.
    _write(tmp_path, "pipeline_output/review/other_lane_note.json", "{}")
    result = tv.create_variation(str(tmp_path), 9, BASE_TIMELINE,
                                 dict(CUTAWAY_SPEC))
    assert result["created"] is True, result
    assert result["branch"] == "variant/r09-reaction-cutaway"
    assert result["timeline_name"] == BASE_TIMELINE + CUTAWAY_SUFFIX
    assert _git(tmp_path, "rev-parse", "--abbrev-ref",
                "HEAD").strip() == result["branch"]
    specs = tv.read_variant_specs(str(tmp_path))
    assert specs["variants"]["9"][0]["cutaway"]["hide_angle"] == "SpeakerTwo"
    # The spec rode on the branch commit, not the working tree.
    _git(tmp_path, "checkout", default)
    assert tv.read_variant_specs(str(tmp_path))["variants"] == {}


def test_create_refuses_without_a_repo_or_on_a_dirty_tree(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    result = tv.create_variation(str(bare), 9, BASE_TIMELINE,
                                 dict(CUTAWAY_SPEC))
    assert result["created"] is False
    assert "no git repo" in result["reason"]

    _base_project(tmp_path)
    _write(tmp_path, "project.yaml", "name: dirty\n")
    result = tv.create_variation(str(tmp_path), 9, BASE_TIMELINE,
                                 dict(CUTAWAY_SPEC))
    assert result["created"] is False
    assert "dirty" in result["reason"]


def test_a_declaring_variant_builds_only_from_its_own_branch(tmp_path):
    """A seam offset is IN the spec, so it builds anywhere; a declaring
    variant's difference is the CONTENT of `external/<store>.json` on its
    own branch, so off that branch it would differ from the approved
    reel NOWHERE and be reported as a comparison."""
    _base_project(tmp_path)
    seam = {"suffix": CUTAWAY_SUFFIX,
            "cutaway": {"hide_angle": "A", "window_seconds": [1, 2]}}
    assert tv.branch_requirement(str(tmp_path), 9, seam) == ""
    blocked = tv.branch_requirement(
        str(tmp_path), 9, {"suffix": " (cta-b)", "declares": ["reel_ending"]})
    assert "variant/r09-cta-b" in blocked
    assert "checkout" in blocked


def test_choosing_requires_a_reason_at_the_command_line():
    """`--why` is required rather than defaulted."""
    import pathlib
    import sys

    result = subprocess.run(
        [sys.executable, "manage_project.py", "variant", "x", "choose",
         "9", " (a)"],
        cwd=str(pathlib.Path(__file__).resolve().parents[3]),
        capture_output=True, encoding="utf-8", check=False)
    assert result.returncode != 0
    assert "--why" in result.stderr


# ── answers merge for real ───────────────────────────────────────

def test_two_answers_are_a_real_conflict(tmp_path):
    """Both sides answering the same reel differently is a fork in
    decisions, not bookkeeping - it reaches a human, never
    auto-resolve."""
    default = _base_project(tmp_path)
    _write(tmp_path, "pipeline_output/llm_responses/reel_motion_09.json",
           json.dumps([{"shot": 1}]))
    bvc.commit_build(str(tmp_path), message="base answer\n")
    _git(tmp_path, "checkout", "-b", "variant/r09-reaction-cutaway")
    _write(tmp_path, "pipeline_output/llm_responses/reel_motion_09.json",
           json.dumps([{"shot": 1, "drift": "push in"}]))
    bvc.commit_build(str(tmp_path), message="variant answer\n")
    _git(tmp_path, "checkout", default)
    _write(tmp_path, "pipeline_output/llm_responses/reel_motion_09.json",
           json.dumps([{"shot": 1, "drift": "pull out"}]))
    bvc.commit_build(str(tmp_path), message="main answer\n")

    result = tv.merge_variations(str(tmp_path),
                                 "variant/r09-reaction-cutaway")
    assert result["merged"] is False
    assert result["conflicts"] == [
        "pipeline_output/llm_responses/reel_motion_09.json"]


def _build_touches_generated(project, stamp):
    """What a rebuild writes: state, run record and a step output move
    on every build even when the declarations did not change."""
    _write(project, "pipeline_data.json", json.dumps(
        {"last_updated": stamp, "edit_completed": {"select_reels": {}}}))
    _write(project, "pipeline_run.json", json.dumps({"mode": stamp}))
    _write(project, "pipeline_output/steps/5_04_compile_manifest/"
                    "assembly_manifest.json",
           json.dumps({"tracks": {"V1": {"clips": []}}, "built": stamp}))


# ── merge: the captain's real case ───────────────────────────────────

def test_merge_cutaway_with_cta_and_grade(tmp_path):
    """One variation carries the cutaway, another the CTA redraw (and
    the grade route, which lives outside versioned declarations - see
    below); merged, the final carries both declarations and both
    markers, and generated state never reaches a human."""
    default = _base_project(tmp_path)

    # Variation A: the reaction cutaway, spec recorded on its branch.
    created = tv.create_variation(str(tmp_path), 9, BASE_TIMELINE,
                                  dict(CUTAWAY_SPEC))
    assert created["created"] is True
    branch_a = created["branch"]
    _build_touches_generated(tmp_path, "2026-09-10T11:45:00")
    _write(tmp_path, "marker_feedback/Reel_09.20260910T114500Z.markers.json",
           json.dumps({"timeline": BASE_TIMELINE + CUTAWAY_SUFFIX,
                       "notes": [{"note": "cutaway lands"}]}))
    assert bvc.commit_build(str(tmp_path), message="build A\n")["committed"]

    # Variation B: the CTA redraw in captain_edits, branched from base.
    _git(tmp_path, "checkout", default)
    _git(tmp_path, "checkout", "-b", "variant/r09-cta-and-grade")
    branch_b = "variant/r09-cta-and-grade"
    _write(tmp_path, "external/captain_edits.json",
           json.dumps(CTA_EDIT, indent=2))
    _build_touches_generated(tmp_path, "2026-09-10T12:33:00")
    _write(tmp_path, "marker_feedback/Reel_09.20260910T123300Z.markers.json",
           json.dumps({"timeline": BASE_TIMELINE, "notes": []}))
    assert bvc.commit_build(str(tmp_path), message="build B\n")["committed"]

    # Combine: A into the default branch, then B on top - the merge
    # plus rebuild shape, minus the Resolve rebuild (blocked lane).
    # The first merge is clean (main never touched the generated
    # files after A branched); the second meets B's rebuild output
    # and auto-resolves it to the target side.
    first = tv.merge_variations(str(tmp_path), branch_a, default)
    assert first["merged"] is True, first
    assert first["auto_resolved"] == []
    second = tv.merge_variations(str(tmp_path), branch_b)
    assert second["merged"] is True, second
    assert "pipeline_data.json" in second["auto_resolved"]

    specs = tv.read_variant_specs(str(tmp_path))
    assert specs["variants"]["9"][0]["cover"]["source_file"] == \
        "/footage/LC4932.MXF"
    edits = json.loads((tmp_path / "external" / "captain_edits.json")
                       .read_text(encoding="utf-8"))
    assert edits["value"][0]["kind"] == "redraw_closer"
    # Marker pulls union-merged: both files present, neither touched.
    pulls = sorted((tmp_path / "marker_feedback").glob("*.markers.json"))
    assert len(pulls) == 3
    assert _git(tmp_path, "status", "--porcelain").strip() == ""
    # ... The grade rides outside versioned declarations (the .drx is
    # captain-supplied and unversioned by policy), so a merge carries
    # the CTA edit and the cutaway spec while the grade travels by
    # rebuild: the merged tree is rebuilt through the same grade
    # route, never merged as bytes.


# ── conflicts ────────────────────────────────────────────────────────

def test_merge_leaves_declaration_conflicts_for_a_human(tmp_path):
    default = _base_project(tmp_path)
    _git(tmp_path, "checkout", "-b", "variant/r09-cta-and-grade")
    _write(tmp_path, "external/captain_edits.json",
           json.dumps(dict(CTA_EDIT, source="lane-b"), indent=2))
    _build_touches_generated(tmp_path, "2026-09-10T11:45:00")
    bvc.commit_build(str(tmp_path), message="B\n")
    _git(tmp_path, "checkout", default)
    _git(tmp_path, "checkout", "-b", "variant/r09-other-edit")
    _write(tmp_path, "external/captain_edits.json",
           json.dumps(dict(CTA_EDIT, source="lane-c"), indent=2))
    _build_touches_generated(tmp_path, "2026-09-10T12:33:00")
    bvc.commit_build(str(tmp_path), message="C\n")

    result = tv.merge_variations(str(tmp_path), "variant/r09-cta-and-grade")
    assert result["merged"] is False
    assert result["conflicts"] == ["external/captain_edits.json"]
    # Generated state resolved itself away; only the declaration waits.
    assert "pipeline_data.json" in result["auto_resolved"]
    assert "pipeline_output/steps/5_04_compile_manifest/" \
           "assembly_manifest.json" in result["auto_resolved"]


# --------------------------------------------------------------------------
# From test_version_variant_choice.py
#
# Choosing between two versions of a reel is an ACT with a consequence.
#
# Before this, two versions of a reel could be BUILT side by side and then
# nothing: they could only be compared by eye, choosing one was not a
# command, the loser sat in the captain's bin under its comparison name,
# and the round said nothing about a decision having been made.
#
# Three things are proved here, and they pull against each other exactly
# the way retirement's two do:
#
# 1. the choice is REAL - the chosen variant becomes the reel, the version
#    that held the name is retired, the loser is archived and the round
#    records which won and why;
# 2. variants cannot ACCUMULATE - the retention bound is per REEL, so the
#    archive is bounded by how many reels the project has rather than by
#    how often the captain compares. This is the constraint that has
#    bitten four times;
# 3. a SIGN-OFF still means what it meant - a signed-off reel is not
#    replaced by accident, and an approval does not lapse because a
#    timeline was renamed.
#
# Every test fails if its mechanism is removed.

REEL = "Reel 09 - your-website-is-only-20-percent (final)"
JCUT = f"{REEL} (j-cut)"
CUTAWAY = f"{REEL} (reaction-cutaway)"


def rows(count=2, frames=100, name="clip"):
    return {
        "video:V1": {
            "media_type": "video",
            "index": 1,
            "name": "V1",
            "items": [
                {
                    "name": f"{name}-{i}",
                    "start": i * 10,
                    "end": i * 10 + 5,
                    "duration": 5,
                }
                for i in range(count)
            ],
            "count": count,
            "frames": frames,
        }
    }


@pytest.fixture()
def project_folder(tmp_path):
    folder = tmp_path / "geo-podcast"
    (folder / "pipeline_output" / "review").mkdir(parents=True)
    return str(folder)


@pytest.fixture
def fake_preservation_snapshots(monkeypatch):
    monkeypatch.setattr(
        reel_replace_guard,
        "full_timeline_snapshot",
        lambda timeline, _project, _folder=None: {
            "timeline": {
                "name": timeline.GetName(),
                "unique_id": timeline.GetUniqueId(),
                "settings": {},
                "start_frame": 0,
                "end_frame": 0,
            },
            "items": [],
            "markers": [],
        },
    )


# ── What is alive ────────────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_built_variant_records_what_it_contains(project_folder):
    """The rows are the payload that makes the comparison free, and
    that outlives the timeline they describe."""
    choice.record_build(
        project_folder,
        9,
        REEL,
        " (j-cut)",
        rows(2),
        watch="the join under the question",
    )
    entry = choice.build_for(project_folder, 9, " (j-cut)")
    assert entry["timeline"] == JCUT
    assert entry["watch"] == "the join under the question"
    assert entry["rows"]["video:V1"]["count"] == 2
    written = pathlib.Path(choice.builds_path_for(project_folder))
    assert json.loads(written.read_text(encoding="utf-8"))["builds"][JCUT]["reel"] == 9
    # Rebuilding replaces the record: two row snapshots for one live
    # timeline is the disagreement AGENTS.md 10.1 keeps catching.
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(7))
    assert len(choice.builds_for_reel(project_folder, 9)) == 1
    assert (
        choice.build_for(project_folder, 9, " (j-cut)")["rows"]["video:V1"]["count"]
        == 7
    )


# ── COMPARED, off disk ───────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_two_variants_are_compared_without_resolve(project_folder):
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(2), watch="the join")
    choice.record_build(
        project_folder,
        9,
        REEL,
        " (reaction-cutaway)",
        rows(4),
        watch="SpeakerOne's reaction",
    )
    diff = choice.compare(project_folder, 9, " (j-cut)", " (reaction-cutaway)")
    assert diff["earlier"] == JCUT and diff["later"] == CUTAWAY
    assert diff["changed"], "four items against two is a change"
    text = choice.render_comparison(diff)
    assert "the join" in text and "SpeakerOne's reaction" in text
    # An empty side reads exactly like a version that contained nothing,
    # so comparing against a variant nobody built refuses.
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.compare(project_folder, 9, " (j-cut)", " (never-built)")
    assert "never-built" in str(refusal.value)


# ── The choice, planned ──────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_the_chosen_variant_takes_the_reels_name_and_the_rest_is_archived():
    # `variant_names` comes from the SPEC RECORD, so another reel's
    # archived variant is never touched.
    other = "Reel 13 - the-accounting-firm (final) (tight)"
    plan = choice.plan_choice(
        [REEL, JCUT, CUTAWAY, f"{other} (archived round 001)"],
        REEL, {JCUT, CUTAWAY}, CUTAWAY, [JCUT], 4
    )
    assert plan["promote"] == (CUTAWAY, REEL)
    # The version that held the name is RETIRED, never deleted.
    assert plan["retire"][1] == f"{REEL} (archived round 004)"
    # And the loser goes to the same archive under the same family of
    # name, so no comparison timeline is left reading as a deliverable.
    assert plan["archive"][JCUT] == f"{JCUT} (archived round 004)"
    assert retire.is_archived_timeline(plan["archive"][JCUT])
    assert plan["collect"] == []
    assert other not in json.dumps(plan)
    with pytest.raises(choice.ChoiceRefused):
        choice.plan_choice([REEL, JCUT], REEL, {JCUT, CUTAWAY}, CUTAWAY, [JCUT], 4)


# ── The bound: variants may not accumulate ───────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_the_archive_holds_one_unchosen_variant_per_REEL_not_per_suffix():
    """THE constraint. Every comparison invents a new suffix, so a bound
    keyed on the variant's own identity would keep one archived timeline
    per suffix the project ever tried - which grows with the number of
    comparisons and is exactly the clutter that has been complained
    about four times. Grouping the reel's variants together is what
    bounds the archive by the number of REELS."""
    tight = f"{REEL} (tight)"
    loose = f"{REEL} (loose)"
    existing = [REEL, f"{JCUT} (archived round 004)", tight, loose]
    plan = choice.plan_choice(
        existing, REEL, {JCUT, CUTAWAY, tight, loose}, loose, [tight], 5
    )
    assert plan["archive"][tight] == f"{tight} (archived round 005)"
    # The previous comparison's runner-up goes: one per reel, and the
    # one kept is the newest.
    assert plan["collect"] == [f"{JCUT} (archived round 004)"]
    assert f"{tight} (archived round 005)" in [k["name"] for k in plan["kept"]]


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_the_bound_is_a_retention_not_a_timeout():
    """Nothing expires with the clock. A reel whose runner-up is the
    only archived variant keeps it however many rounds pass; it is the
    NEXT comparison that releases it, which is what makes the bound a
    function of the reels rather than of time."""
    assert choice.RETAINED_UNCHOSEN == 1
    existing = [REEL, f"{JCUT} (archived round 004)", CUTAWAY]
    for round_number in (5, 50, 500):
        plan = choice.plan_choice(
            existing, REEL, {JCUT, CUTAWAY}, CUTAWAY, [], round_number
        )
        assert plan["collect"] == [], (
            f"round {round_number} collected the runner-up with no new "
            f"comparison - the bound has become a timeout"
        )
        assert f"{JCUT} (archived round 004)" in [k["name"] for k in plan["kept"]]


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_the_reels_OWN_retired_generations_are_bounded_too():
    """A choice retires the version that held the reel's name exactly
    as a promotion does, so the reel's own generations accumulate by
    the other door unless the same bound is asked of them. Measured
    2026-09-12 against a real Resolve: two choices left BOTH
    `(archived round 001)` and `(archived round 001.2)` standing."""
    first = f"{REEL} (archived round 001)"
    existing = [REEL, first, CUTAWAY]
    plan = choice.plan_choice(existing, REEL, {JCUT, CUTAWAY}, CUTAWAY, [], 1)
    # The one retired by THIS choice is kept; the one before it goes.
    assert plan["retire"][1] == f"{REEL} (archived round 001.2)"
    assert first in plan["collect"]
    assert f"{REEL} (archived round 001.2)" in [k["name"] for k in plan["kept"]]


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_signed_off_generation_or_variant_is_never_collected():
    first = f"{REEL} (archived round 001)"
    plan = choice.plan_choice(
        [REEL, first, CUTAWAY],
        REEL,
        {JCUT, CUTAWAY},
        CUTAWAY,
        [],
        1,
        signed_off_reels={REEL},
    )
    assert plan["collect"] == []
    # Nor is a signed-off UNCHOSEN variant: collecting it would delete
    # the only copy of the thing the captain approved.
    tight = f"{REEL} (tight)"
    existing = [REEL, f"{JCUT} (archived round 004)", tight, CUTAWAY]
    plan = choice.plan_choice(
        existing,
        REEL,
        {JCUT, CUTAWAY, tight},
        CUTAWAY,
        [tight],
        5,
        signed_off_reels={JCUT},
    )
    assert plan["collect"] == []
    assert any(JCUT in k["why"] and "sign-off" in k["why"] for k in plan["kept"])


# ── The Resolve half ─────────────────────────────────────────────


def _project(timelines, *, delete_ok=True):
    project = FakeProject(timelines, delete_ok=delete_ok)
    project.SetSettings({"timelineFrameRate": "23.976"})
    return project


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_the_collected_names_are_read_before_the_delete(project_folder):
    """Reading the report off a timeline object AFTER deleting it
    crashes the collection it was reporting on. Latent until now:
    `RETAINED_UNCHOSEN = 1` means nothing is collected until a reel is
    compared a SECOND time, so no test and no build had ever reached
    this line against a real Resolve."""
    tight = f"{REEL} (tight)"
    loose = f"{REEL} (loose)"
    older = f"{JCUT} (archived round 001)"
    choice.record_build(project_folder, 9, REEL, " (tight)", rows(1))
    choice.record_build(project_folder, 9, REEL, " (loose)", rows(1))
    project = _project(
        [
            FakeTimeline(REEL),
            FakeTimeline(older),
            FakeTimeline(tight),
            FakeTimeline(loose),
        ]
    )
    record_ren_owned_inventory(project_folder, project)
    report = choice.choose(
        project,
        project.GetMediaPool(),
        project_folder,
        9,
        REEL,
        " (loose)",
        "the loose framing breathes",
        variant_names={JCUT, CUTAWAY, tight, loose},
    )
    # The first comparison's runner-up goes; this one's is kept.
    assert report["collected"] == [older]
    assert older in choice.render_choice(report)
    assert None not in report["collected"]


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_delete_resolve_declines_is_refused_not_reported_collected(project_folder):
    """The live-demo defect's sibling: PR 1149 settled this exact shape
    for `reel_retirement.collect_superseded`, and `choose` carries its
    own copy - `DeleteTimelines` returns falsy and the names are kept
    under `collected` anyway, while the census still shows them present
    (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`). Fails on the old
    shape (no raise, the name reported collected while still present);
    passes on the new (refused, the generation still present for the
    next choice to plan again)."""
    tight = f"{REEL} (tight)"
    loose = f"{REEL} (loose)"
    older = f"{JCUT} (archived round 001)"
    choice.record_build(project_folder, 9, REEL, " (tight)", rows(1))
    choice.record_build(project_folder, 9, REEL, " (loose)", rows(1))
    project = _project(
        [
            FakeTimeline(REEL),
            FakeTimeline(older),
            FakeTimeline(tight),
            FakeTimeline(loose),
        ],
        delete_ok=False,
    )
    record_ren_owned_inventory(project_folder, project)
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.choose(
            project,
            project.GetMediaPool(),
            project_folder,
            9,
            REEL,
            " (loose)",
            "the loose framing breathes",
            variant_names={JCUT, CUTAWAY, tight, loose},
        )
    assert older in str(refusal.value)
    assert "nothing was reported collected" in str(refusal.value)
    assert older in project.names()


def _built(project_folder):
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(2))
    choice.record_build(project_folder, 9, REEL, " (reaction-cutaway)", rows(4))


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_choosing_renames_retires_archives_and_records(project_folder):
    _built(project_folder)
    incumbent = FakeTimeline(REEL)
    project = _project([incumbent, FakeTimeline(JCUT), FakeTimeline(CUTAWAY)])
    report = choice.choose(
        project,
        project.GetMediaPool(),
        project_folder,
        9,
        REEL,
        " (reaction-cutaway)",
        "her reaction lands the joke; the J-cut reads as a mistake",
        variant_names={JCUT, CUTAWAY},
    )
    names = project.names()
    # The chosen variant IS the reel now - the same timeline, renamed.
    assert REEL in names
    assert CUTAWAY not in names
    # The version that held the name was retired, not deleted.
    assert report["retired"] in names
    assert retire.is_archived_timeline(report["retired"])
    # The loser is in the archive under a name nothing reads as a
    # deliverable.
    assert report["archived"][JCUT] in names
    assert project.deleted == []
    # And the round says which won and why.
    from library.tools.versions import rounds

    recorded = rounds.read_rounds(project_folder)
    entry = recorded["rounds"][-1]["reels"][REEL]
    assert entry["choice"]["chosen"] == " (reaction-cutaway)"
    assert entry["choice"]["over"] == [JCUT]
    assert "lands the joke" in entry["choice"]["why"]
    # The round carries the CHOSEN cut's rows, so the next round can be
    # diffed against it with Resolve closed.
    assert entry["rows"]["video:V1"]["count"] == 4
    assert "lands the joke" in choice.render_choice(report)
    # The build record stops claiming what is no longer alive.
    assert choice.builds_for_reel(project_folder, 9) == []


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_choice_with_no_reason_is_refused(project_folder):
    _built(project_folder)
    project = _project([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.choose(
            project,
            project.GetMediaPool(),
            project_folder,
            9,
            REEL,
            " (reaction-cutaway)",
            "   ",
        )
    assert "no reason" in str(refusal.value)
    assert project.names() == [REEL, CUTAWAY], "nothing moved"


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_rename_resolve_refuses_leaves_everything_recoverable(project_folder):
    _built(project_folder)
    project = _project([FakeTimeline(REEL), FakeTimeline(CUTAWAY, rename_ok=False)])
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.choose(
            project,
            project.GetMediaPool(),
            project_folder,
            9,
            REEL,
            " (reaction-cutaway)",
            "chose the cutaway",
            variant_names={JCUT, CUTAWAY},
        )
    assert "Nothing was deleted" in str(refusal.value)
    assert project.deleted == []
    # The incumbent is safe under its archived name and the variant is
    # still under its own.
    assert CUTAWAY in project.names()


# ── The sign-off, and what a variant does to it ──────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_choosing_over_a_signed_off_reel_refuses_by_name(project_folder):
    from library.tools import reel_signoff

    _built(project_folder)
    reel_signoff.sign_off(project_folder, REEL, note="this is the one, do not touch it")
    project = _project([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    with pytest.raises(reel_signoff.SignOffNotDeclared) as refusal:
        choice.choose(
            project,
            project.GetMediaPool(),
            project_folder,
            9,
            REEL,
            " (reaction-cutaway)",
            "chose the cutaway",
            variant_names={JCUT, CUTAWAY},
        )
    assert "do not touch it" in str(refusal.value)
    assert project.names() == [REEL, CUTAWAY], "nothing moved"


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_declared_supersession_proceeds_and_is_recorded(project_folder):
    from library.tools import reel_signoff

    _built(project_folder)
    reel_signoff.sign_off(project_folder, REEL, note="the old one")
    project = _project([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    report = choice.choose(
        project,
        project.GetMediaPool(),
        project_folder,
        9,
        REEL,
        " (reaction-cutaway)",
        "the cutaway is better",
        variant_names={JCUT, CUTAWAY},
        supersede_declared=[REEL],
    )
    assert report["superseded_signoff"]["note"] == "the old one"
    # Recorded, never deleted.
    document = reel_signoff.read_signoffs(project_folder)
    assert reel_signoff.signoff_for(project_folder, REEL) is None
    assert any(entry["note"] == "the old one" for entry in document["superseded"])


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_signed_off_variant_carries_its_approval_onto_the_reel(project_folder):
    """A variant IS built, so it can be signed off (the captain's own
    ruling). When it wins, the cut the captain approved did not change -
    its container did - and an approval the machine dropped because of a
    rename would be an approval it withdrew on their behalf."""
    from library.tools import reel_signoff

    _built(project_folder)
    reel_signoff.sign_off(
        project_folder, CUTAWAY, note="her reaction is the whole thing", by="captain"
    )
    project = _project([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    report = choice.choose(
        project,
        project.GetMediaPool(),
        project_folder,
        9,
        REEL,
        " (reaction-cutaway)",
        "chose the cutaway",
        variant_names={JCUT, CUTAWAY},
    )
    assert report["carried_signoff"]["note"] == "her reaction is the whole thing"
    carried = reel_signoff.signoff_for(project_folder, REEL)
    assert carried is not None
    assert carried["note"] == "her reaction is the whole thing"
    assert carried["by"] == "captain"
    # And it is no longer claimed on the name that no longer exists.
    assert reel_signoff.signoff_for(project_folder, CUTAWAY) is None
    # A variant is its own reel to a sign-off (`base_name` strips only
    # the BUILD's container suffixes), which is what let it be signed.
    assert reel_signoff.base_name(CUTAWAY) == CUTAWAY
    assert reel_signoff.base_name(f"{REEL} (rebuild staging)") == REEL


# --------------------------------------------------------------------------
# From test_reel_variants_carry_recorded_obedience.py
#
# A variant must carry every RECORDED OBEDIENCE and DECLARATION the rebuild carries.
#
# A variant is the approved reel with ONE change in it; a build that drops a
# recorded decision (pinned closer, struck span, declared grade, declared
# ending) shows two changes and calls the difference the seam - and conformance
# passes it, because a dropped obedience is structurally perfect. The
# Reel 09 incident (2026-09-11) is in docs/RULE_EVIDENCE.md and
# docs/evidence/variants.md.

SOURCE = (pathlib.Path(__file__).resolve().parents[3]
          / "library" / "tools" / "reel_build.py")

#: The calls that APPLY something the captain recorded or declared.
#: Named explicitly: a heuristic over every call in a 6,000-line module
#: would either miss one or drown the failure in noise.
RECORDED_OBEDIENCE = (
    "apply_closer_redraws",   # captain_edits: the pinned closer
    "keep_exclusions",        # transcript_corrections: struck spans
    "exclusion_cuts_for_span",
    "resolve_power_grade",    # the declared Color page grade
    "resolve_grade_cdl",      # the declared CDL half
    "resolve_look",           # the declared series look
    "resolve_document_mic_bleed",  # the measured ISO mic choice
    # The per-reel DECLARATION readers - the stores a variant may differ
    # in, so also the ones it must READ.
    "load_intent", "load_pins", "resolve_ending", "apply_ending",
    "apply_pins",
)


def _module_functions():
    """Every top-level function `reel_build.py` defines, by name."""
    return {node.name: node
            for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _called_names(func):
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _called_names_transitive(defined, name):
    """What `name` reaches, following same-module helpers to a fixpoint
    (#1214 moved the rebuild's reads behind shared helpers one frame out,
    and a flat check read that refactor as a dropped obedience)."""
    seen, stack = set(), [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        func = defined.get(current)
        if func is not None:
            stack.extend(_called_names(func) - seen)
    seen.discard(name)
    return seen


def test_variant_carries_every_obedience_the_rebuild_carries():
    defined = _module_functions()
    rebuild = _called_names_transitive(defined, "rebuild_reels_in_project")
    variant = _called_names_transitive(defined, "build_reel_variants")
    stale = [call for call in RECORDED_OBEDIENCE if call not in rebuild]
    assert not stale, (
        f"rebuild_reels_in_project no longer reaches {stale} - either the "
        f"rebuild stopped obeying a recorded decision, or RECORDED_OBEDIENCE "
        f"is stale and must be updated to the new spelling.")
    dropped = [call for call in RECORDED_OBEDIENCE if call not in variant]
    assert not dropped, (
        f"rebuild_reels_in_project applies {dropped} and build_reel_variants "
        f"does not: the variant differs from the approved reel somewhere "
        f"other than its seam, and conformance passes it.")


def test_a_variant_passes_its_declarations_on_and_files_its_record():
    """Reading a declaration and not passing it to `build_reel_timeline`
    is the same defect one step later. And a variant that files no
    semantic record cannot be promoted carrying a disabled graphic
    (Reel 09, 2026-10-02: `reel_disabled_clip_carry`)."""
    func = _module_functions()["build_reel_variants"]
    calls = [node for node in ast.walk(func) if isinstance(node, ast.Call)]
    builds = [node for node in calls
              if getattr(node.func, "id", None) == "build_reel_timeline"]
    assert builds, "build_reel_variants calls no build_reel_timeline()"
    for keyword in ("ending", "overlay_intent"):
        assert keyword in {kw.arg for kw in builds[0].keywords}, (
            f"build_reel_variants calls build_reel_timeline without "
            f"{keyword}=, so the declaration it read never reaches the picture")
    assert any(getattr(node.func, "id", None) == "_write_reel_record"
               and any(isinstance(arg, ast.Attribute)
                       and arg.attr == "write_records" for arg in node.args)
               for node in calls), (
        "build_reel_variants files no semantic record, so a promoted "
        "variant cannot carry a disabled graphic")


# --------------------------------------------------------------------------
# From test_rebuild_determinism.py
#
# Is a rebuild from identical declarations reproducible? (issue #937)
#
# The variations-as-branches workflow merges declarations and rebuilds the
# timeline instead of merging generated state. That is safe only if a
# rebuild from identical declarations gives the same timeline. This file
# measures that claim at every level that can be measured without Resolve,
# without renders and without the captain's project data (all out of
# scope for this lane):
#
# 1. RECORDED ANSWERS ARE REUSED. A plain second run of the runner
#    executes zero step bodies - `run_pipeline` skips completed steps
#    (`already completed`, run_pipeline.py) - so a step that calls a
#    model is NOT re-asked. Only `--rerun` discards the recorded output
#    and re-executes, which for an LLM step means re-authorship.
# 2. DERIVATION IS BYTE-STABLE. The Fusion comp text is identical across
#    two derivations from the same inputs.
# 3. THE DIFFS THAT REMAIN ARE NAMED. The per-build request file differs
#    only in `timestamp` (reel_semantic_visual.write_request), the build
#    provenance only in `built_at` (plan_provenance.write_provenance) -
#    bookkeeping nobody reads back into a decision.
#
# What this file does NOT claim: Resolve read-back stability across a
# delete-and-recreate (Resolve mints new timeline/item ids - PR #928
# measured identical placements across one such cycle live), fresh
# Remotion render byte-reproducibility (the content-keyed reuse cache
# pairs rebuilds back to the artefact on disk, so a rebuild with no
# changes never re-renders - which masks rather than proves renderer
# determinism), and LLM re-authorship stability under `--rerun` (a
# re-asked model is a new variation, not a rebuild).

# ── The runner: recorded answers are reused, --rerun re-authors ──────

# a -> b -> c, all deterministic, all cheap. Same shape as
# test_run_configuration_end_to_end.THREE_STEPS, owned here so that
# file can move without taking this measurement with it.
THREE_STEPS = {
    "id": "edit_video",
    "nodes": [
        {"id": "scan", "name": "Scan Project Folder",
         "step_ref": "steps/step_1_01_scan_project"},
        {"id": "catalog", "name": "Catalog Footage",
         "step_ref": "steps/step_1_02_catalog_footage"},
        {"id": "temporal_index", "name": "Temporal Index",
         "step_ref": "steps/step_1_04_temporal_index"},
    ],
    "edges": [
        {"from": "scan", "to": "catalog",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
        {"from": "catalog", "to": "temporal_index",
         "data_mapping": {"catalog": "catalog"}},
    ],
}

_OUTPUT = {
    "scan": {"raw_footage_files": ["one.mov", "two.mov", "three.mov"]},
    "catalog": {"catalog": [{"clip_id": "clip_001"}]},
    "temporal_index": {"temporal_event_indices": []},
}

_NODE_OF = {
    "step_1_01_scan_project": "scan",
    "step_1_02_catalog_footage": "catalog",
    "step_1_04_temporal_index": "temporal_index",
}


@pytest.fixture
def project(tmp_path):
    """A project with nothing done yet, so the first run really runs."""
    folder = tmp_path / "rebuild_project"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(folder),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "capability_outputs": {},
    }), encoding="utf-8")
    (folder / "project.yaml").write_text(
        "name: Rebuild Project\nslug: rebuild-project\n", encoding="utf-8")
    return folder


class _Runner:
    """Drives the real runner over THREE_STEPS, recording which step
    bodies executed - the stand-in for "was the model re-asked". """

    def __init__(self, folder: Path):
        self.folder = folder
        self.seen = []

    def _implementation(self, step_dir):
        return {"type": "deterministic",
                "entry": str(Path(step_dir) / "step.py"), "manifest": {}}

    def _deterministic(self, entry, inputs):
        node_id = _NODE_OF[Path(entry).parent.name]
        self.seen.append(node_id)
        return dict(_OUTPUT[node_id])

    def run(self, **kw):
        with patch("library.processes.edit_video.run_pipeline.load_dag",
                   return_value=THREE_STEPS), \
             patch("library.processes.edit_video.run_pipeline"
                   ".get_step_implementation",
                   side_effect=self._implementation), \
             patch("library.processes.edit_video.run_pipeline"
                   ".run_deterministic_step",
                   side_effect=self._deterministic), \
             patch("library.processes.edit_video.run_pipeline"
                   ".apply_source_identity", return_value=None):
            return run_pipeline(str(self.folder), **kw)


def test_a_plain_second_run_reuses_recorded_answers(project):
    """The single fact that decides issue #937 for the pipeline path.

    A second identical run executes NO step body - every step is
    skipped as already completed - and the recorded outputs are
    unchanged. A model step is therefore NOT re-asked on rebuild;
    its recorded answer stands. If this ever fails, the merge
    workflow's determinism assumption fails with it.
    """
    first = _Runner(project)
    summary = first.run()
    assert summary["status"] == "SUCCESS"
    assert first.seen == ["scan", "catalog", "temporal_index"]

    before = capability_outputs.node_outputs(
        load_pipeline_state(str(project)))

    second = _Runner(project)
    summary = second.run()
    assert summary["status"] == "SUCCESS"
    assert second.seen == [], (
        f"a plain second run re-executed {second.seen} - recorded "
        f"answers are NOT being reused")

    after = capability_outputs.node_outputs(
        load_pipeline_state(str(project)))
    assert after == before, "recorded step outputs moved under a no-op run"


def test_rerun_is_the_reauthorship_path(project):
    """The boundary of the guarantee above: `--rerun` discards the
    recorded output and re-executes the step. For an LLM step that is
    a fresh model call - a new variation, never a rebuild - and the
    merge workflow must not smuggle it through a rebuild."""
    first = _Runner(project)
    assert first.run()["status"] == "SUCCESS"

    second = _Runner(project)
    summary = second.run(rerun=["catalog"])
    assert summary["status"] == "SUCCESS"
    assert second.seen == ["catalog"], (
        f"--rerun catalog should re-execute exactly catalog, ran "
        f"{second.seen}")


# ── Derivation: two builds from identical declarations ───────────────


def test_effect_comp_is_byte_identical_across_builds():
    """The Fusion comp - the picture itself - serialised twice from
    the same effect parameters. One byte different here is a
    different picture, so this compares bytes, not parses."""
    from library.tools.fusion.comp_builder import build_effect_comp

    effects = {"zoom_start": 1.0, "zoom_end": 1.08,
               "source_in_frame": 100, "source_out_frame": 400,
               "vignette": True, "vignette_soft": 0.35,
               "vignette_blend": 0.25}
    first = build_effect_comp(dict(effects), 500,
                              source_res=(1080, 1920), played_frames=383)
    second = build_effect_comp(dict(effects), 500,
                               source_res=(1080, 1920), played_frames=383)
    assert first == second, "same parameters serialised to different comps"
    assert len(first) > 0


def test_provenance_rewrite_differs_only_in_built_at(tmp_path):
    """`write_provenance` re-stamps `built_at` on every build - the
    cosmetic diff - plus the per-reel `built_at_reels` stamp of the
    reel it just built. Everything else must stand still, because the
    verifier grades the staging against this record."""
    from library.tools.plan_provenance import (
        caption_content_hash,
        write_provenance,
    )

    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    plan = tmp_path / "proposal.json"
    plan.write_text(json.dumps({"moments": []}), encoding="utf-8")
    card_hash = caption_content_hash(
        [{"text": "hello world", "start": 0.5, "duration": 2.0}])

    write_provenance(str(review), str(plan), ["Reel 09 - test"],
                     caption_hashes={"Reel 09 - test": card_hash})
    first = json.loads(
        (review / "plan_provenance.json").read_text(encoding="utf-8"))
    write_provenance(str(review), str(plan), ["Reel 09 - test"],
                     caption_hashes={"Reel 09 - test": card_hash})
    second = json.loads(
        (review / "plan_provenance.json").read_text(encoding="utf-8"))

    first_built, second_built = first.pop("built_at"), second.pop("built_at")
    first_reel_built = first.pop("built_at_reels")
    second_reel_built = second.pop("built_at_reels")
    assert first == second, (
        "provenance moved more than its timestamp across a rebuild")
    assert first_built != second_built, (
        "expected the cosmetic built_at re-stamp; without it this test "
        "proves nothing about what differs")
    assert first_reel_built != second_reel_built, (
        "the rebuilt reel's own stamp must refresh too - a per-reel "
        "stamp that stood still would be the file-level one wearing "
        "per-reel clothes")
    assert first["built_with"] == second["built_with"], (
        "same code, same engine digest - the revision stamp must stand "
        "still across a rebuild that changed no code")
