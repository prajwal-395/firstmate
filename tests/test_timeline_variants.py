"""Variations as branches, merging declarations, rebuilding the final.

Covers library/tools/timeline_variants.py and the canonical JSON
spelling in library/tools/stable_json.py:

- branch <-> timeline names derive from each other, both ways, on the
  exact shapes the captain has in Resolve today (` (j-cut)`,
  ` (reaction-cutaway)`);
- the seam spec validates structurally and writes canonically (same
  record, same bytes - otherwise every variant merge conflicts);
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

from library.tools import build_version_control as bvc
from library.tools import stable_json, timeline_variants as tv

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

def test_names_derive_both_ways_on_real_shapes():
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


def test_names_resolve_exactly_through_the_spec_record():
    specs = ({"suffix": " (Reaction Cutaway)", "cutaway": {
        "hide_angle": "Craig", "window_seconds": [574, 598]}},)
    branch = tv.variant_branch_name(9, " (Reaction Cutaway)")
    assert tv.timeline_for_branch(branch, BASE_TIMELINE, specs) == \
        BASE_TIMELINE + " (Reaction Cutaway)"


def test_final_is_not_a_variant_suffix():
    # `(final)` is the approved timeline's own name. Only the spec
    # record tells a base-name paren from a variant suffix.
    specs = (dict(CUTAWAY_SPEC),)
    assert tv.branch_for_timeline(BASE_TIMELINE, specs) is None
    assert tv.branch_for_timeline(BASE_TIMELINE + CUTAWAY_SUFFIX, specs) == \
        "variant/r09-reaction-cutaway"
    assert tv.branch_for_timeline(
        BASE_TIMELINE + " (something-undeclared)", specs) is None


def test_approved_timeline_lives_on_no_variant_branch():
    assert tv.branch_for_timeline("Reel 09 - slug") is None
    assert tv.timeline_for_branch("main", BASE_TIMELINE) is None


# ── specs ────────────────────────────────────────────────────────────

def test_cutaway_spec_validates():
    assert tv.validate_variant_spec(dict(CUTAWAY_SPEC)) == []


def test_spec_without_a_seam_is_refused():
    errors = tv.validate_variant_spec({"suffix": CUTAWAY_SUFFIX,
                                       "watch": "look"})
    assert any("j_cut / cutaway" in e for e in errors)


def test_spec_with_a_wandering_suffix_is_refused():
    bad = dict(CUTAWAY_SPEC, suffix="reaction-cutaway")
    assert any("suffix" in e for e in tv.validate_variant_spec(bad))


def test_spec_write_is_canonical(tmp_path):
    record = {"format": tv.VARIANTS_FORMAT, "variants": {
        "9": [dict(CUTAWAY_SPEC),
              {"suffix": JCUT_SUFFIX,
               "j_cut": {"join_seconds": 0.4, "lead_seconds": 0.2}}],
        "10": [dict(CUTAWAY_SPEC, suffix=" (cover-10)")],
    }}
    first = tv.write_variant_specs(str(tmp_path), record)
    before = (tmp_path / "pipeline_output" / "review"
              / tv.VARIANTS_FILENAME).read_text(encoding="utf-8")
    # Same record in a scrambled order is the same bytes.
    scrambled = {"variants": {
        "10": [dict(CUTAWAY_SPEC, suffix=" (cover-10)")],
        "9": [dict(record["variants"]["9"][1]),
              dict(record["variants"]["9"][0])]}}
    tv.write_variant_specs(str(tmp_path), scrambled)
    after = (tmp_path / "pipeline_output" / "review"
             / tv.VARIANTS_FILENAME).read_text(encoding="utf-8")
    assert before == after
    assert first.endswith(tv.VARIANTS_FILENAME)
    assert after.endswith("\n")


def test_duplicate_suffix_is_refused(tmp_path):
    record = {"variants": {"9": [dict(CUTAWAY_SPEC),
                                 dict(CUTAWAY_SPEC)]}}
    with pytest.raises(ValueError):
        tv.write_variant_specs(str(tmp_path), record)


# ── reformatting never changes what the pipeline builds ──────────

def test_canonical_spelling_is_parse_identical(tmp_path):
    """The merge-friendly writers reorder keys and add a trailing
    newline; the parsed document is byte-for-byte the same object, so
    a rebuild from reformatted declarations builds the same timeline.
    Proved here for the spelling itself and for the step-output
    writer that every plan flows through."""
    from library.tools import step_exporter
    doc = {"tracks": {"V1": {"clips": [{"clip_id": "c1", "b": 2}]}},
           "captions": ["it's exactly why"], "a": 1}
    assert json.loads(stable_json.dumps_stable(doc)) == doc
    paths = step_exporter.export_step_output(
        str(tmp_path), "compile_manifest", "compile_manifest", doc)
    on_disk = json.loads((tmp_path / paths["json"]).read_text(
        encoding="utf-8"))
    assert on_disk == doc


# ── stable_json ──────────────────────────────────────────────────────

def test_stable_spelling_sorts_keys_and_ends_in_newline():
    text = stable_json.dumps_stable({"b": 1, "a": {"d": 4, "c": 3}})
    assert text == '{\n  "a": {\n    "c": 3,\n    "d": 4\n  },\n  "b": 1\n}\n'
    assert json.loads(text) == {"b": 1, "a": {"d": 4, "c": 3}}


# ── create ───────────────────────────────────────────────────────────

def test_create_variation_branches_records_commits(tmp_path):
    default = _base_project(tmp_path)
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


def test_create_refuses_a_dirty_tree(tmp_path):
    _base_project(tmp_path)
    _write(tmp_path, "project.yaml", "name: dirty\n")
    result = tv.create_variation(str(tmp_path), 9, BASE_TIMELINE,
                                 dict(CUTAWAY_SPEC))
    assert result["created"] is False
    assert "dirty" in result["reason"]


def test_untracked_files_do_not_block_a_variation(tmp_path):
    # Another lane's records sit untracked in a live project; they
    # ride no branch, so branching around them loses nothing.
    _base_project(tmp_path)
    _write(tmp_path, "pipeline_output/review/other_lane_note.json", "{}")
    result = tv.create_variation(str(tmp_path), 9, BASE_TIMELINE,
                                 dict(CUTAWAY_SPEC))
    assert result["created"] is True, result


def test_create_refuses_without_a_repo(tmp_path):
    result = tv.create_variation(str(tmp_path), 9, BASE_TIMELINE,
                                 dict(CUTAWAY_SPEC))
    assert result["created"] is False
    assert "no git repo" in result["reason"]


# ── answers merge for real ───────────────────────────────────────

def test_new_answer_on_one_side_wins_cleanly(tmp_path):
    """The refresh direction: main re-answers reel 9's motion while
    the variant lives. Answers are written once, never rewritten by a
    rebuild, so the merge takes the answer with no conflict - the
    variant rebuild then reads it like any other declaration."""
    default = _base_project(tmp_path)
    _git(tmp_path, "checkout", "-b", "variant/r09-reaction-cutaway")
    _build_touches_generated(tmp_path, "2026-09-10T11:45:00")
    bvc.commit_build(str(tmp_path), message="variant build\n")
    _git(tmp_path, "checkout", default)
    _write(tmp_path, "pipeline_output/llm_responses/reel_motion_09.json",
           json.dumps([{"shot": 1, "drift": "push in"}]))
    bvc.commit_build(str(tmp_path), message="re-answer motion\n")

    result = tv.merge_variations(str(tmp_path),
                                 "variant/r09-reaction-cutaway")
    assert result["merged"] is True, result
    answer = json.loads(
        (tmp_path / "pipeline_output" / "llm_responses" /
         "reel_motion_09.json").read_text(encoding="utf-8"))
    assert answer == [{"shot": 1, "drift": "push in"}]


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
