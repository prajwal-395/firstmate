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

import json
import subprocess

import pytest

from library.tools.versions import store as bvc
from library.tools.versions import variants as tv

BASE_TIMELINE = "Reel 09 - your-website-is-only-20-percent (final)"
CUTAWAY_SUFFIX = " (reaction-cutaway)"
JCUT_SUFFIX = " (j-cut)"

CUTAWAY_SPEC = {
    "suffix": CUTAWAY_SUFFIX,
    "cutaway": {
        "hide_angle": "Craig",
        "window_seconds": [574, 598],
        "cover_words": ["exactly", "why"],
    },
    "cover": {
        "source_file": "/footage/LC4932.MXF",
        "source_in": 1450.9495,
        "source_out": 1451.9505,
    },
    "watch": "Craig hides, Akshita covers 24 frames",
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
    assert specs["variants"]["9"][0]["cutaway"]["hide_angle"] == "Craig"
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
        cwd=str(pathlib.Path(__file__).resolve().parents[1]),
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
