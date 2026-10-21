"""The premise a brief rests on, and the footage it is measured against.

The defect this file names: an acceptance set whose briefs ask for
things the footage does not contain. Four of the eight briefs in the
shipped set name a statement of what something costs, a moment of
overlapping speech, a caption that names the wrong word, or a music
bed - and the substrate reel carried none of them.

The captain's ruling on what to do about it: keep the HEADLINE the
engine's headline. A brief the footage cannot carry is still scored,
because the engine still had to do the work the request asks for. The
premise is measured and reported ALONGSIDE the score, never instead of
it - and the fix is the substrate, so the report has to say which
footage each brief still needs.
"""

from __future__ import annotations

import copy
import json

import pytest

from library.tools.editorial_acceptance import briefs, footage, runner
from library.tools.editorial_acceptance import record as record_module


def _catalogue(*entries: dict) -> dict:
    return {"schema": footage.SCHEMA, "substrates": list(entries)}


def _substrate(substrate_id: str = "synthetic-reel",
               profile: str = "two_camera_interview",
               features: tuple[str, ...] = (),
               checked: tuple[str, ...] = ()) -> dict:
    return {
        "id": substrate_id,
        "profile": profile,
        "aliases": [],
        "features": list(features),
        "checked_features": list(checked),
    }


def _record(brief_id: str, substrate: str | None = "podcast-reel-13",
            **sections) -> dict:
    record = record_module.new_record(
        task_id=brief_id,
        brief_id=brief_id,
        instruction=briefs.brief(briefs.load_brief_set(), brief_id)[
            "instruction"],
        interpretation={
            "restated_outcome": "the run restated the request",
            "selected_operations": [],
            "resolved_targets": [],
        },
        retrieval={"moments": [], "abstained": False, "unavailable": []},
        planning={"operations": [], "requirements_covered": []},
        execution={"receipt": {"status": "refused"},
                   "checks": {}, "collateral_changes": [],
                   "refusal": {"reason": "the footage does not hold it"}},
        delivery={},
    )
    record["project"] = {"substrate": substrate or "",
                         "timeline": "Reel 13 - the-accounting-firm-ai-called-"
                                     "healthcare"}
    for key, value in sections.items():
        record[key] = value
    return record


def _row(report: dict, brief_id: str) -> dict:
    for row in report["results"]:
        if row["brief_id"] == brief_id:
            return row
    raise AssertionError(f"the report carries no row for {brief_id!r}")


def test_the_shipped_catalogue_holds_its_own_contract():
    """The defect: a substrate catalogue that names a feature nobody can
    check, or a feature it both carries and denies, is a catalogue that
    reads as evidence and is not."""
    raw = json.loads(footage.catalogue_path().read_text(encoding="utf-8"))
    assert footage.validate_catalogue(raw) == []


def test_the_shipped_brief_set_states_the_premise_each_brief_rests_on():
    """The defect: a brief whose premise is nowhere stated cannot be
    checked against anything, so the set's meaning is a comment."""
    the_set = briefs.load_brief_set()
    assert briefs.validate_brief_set(the_set) == []
    requirements = 0
    for brief in the_set["briefs"]:
        key = briefs.oracle(brief)
        assert "footage_requirements" in key
        for requirement in key["footage_requirements"]:
            assert requirement["feature"] in footage.FEATURES
            assert requirement["presence"] in footage.PRESENCE
            requirements += 1
    assert requirements, "no brief states a premise at all"


def test_a_brief_whose_premise_the_substrate_lacks_is_still_scored():
    """The defect: the harness reclassifies the brief as not applicable
    and the headline stops counting the engine's result. The brief
    asks for the work; the engine still has to do it."""
    report = runner.run_acceptance(
        [_record("open-with-the-cost", substrate="no-cost-reel")],
        catalogue=_catalogue(_substrate(
            "no-cost-reel", features=(), checked=("cost_statement",))),
    )
    row = _row(report, "open-with-the-cost")
    assert row["status"] == "scored"
    assert report["scored"] == 1
    assert row["failure"]["failed"] is True
    assert row["failure"]["cause"]


def test_the_premise_the_footage_does_not_carry_is_named_beside_the_score():
    """The defect: the report says a brief failed and never says the
    footage could not carry the request, so the next change targets the
    wrong thing."""
    report = runner.run_acceptance(
        [_record("open-with-the-cost", substrate="no-cost-reel")],
        catalogue=_catalogue(_substrate(
            "no-cost-reel", features=(), checked=("cost_statement",))),
    )
    row = _row(report, "open-with-the-cost")
    assert row["premise"]["status"] == footage.PREMISE_NOT_CARRIED
    assert [item["feature"]
            for item in row["premise"]["missing_premises"]] == [
                "cost_statement"]
    assert report["premise"]["premise_not_carried"] == ["open-with-the-cost"]


def test_the_headline_is_the_engines_headline():
    """The defect: the premise view replaces the score instead of
    accompanying it, and the report's headline stops being the engine's
    result."""
    report = runner.run_acceptance(
        [_record("open-with-the-cost", substrate="no-cost-reel")],
        catalogue=_catalogue(_substrate(
            "no-cost-reel", features=(), checked=("cost_statement",))),
    )
    assert report["passed"] == 0
    assert report["failed"] == 1
    assert report["stage_metrics"]["stages"]["interpretation"]["measured"] == 1
    assert report["stage_metrics"]["stages"]["interpretation"]["total"] == 1


def test_the_premise_view_counts_a_brief_the_footage_cannot_carry_as_unmeasured():
    """The defect: the premise view counts a brief it could not measure
    as measured, so the two views look alike."""
    report = runner.run_acceptance(
        [_record("open-with-the-cost", substrate="no-cost-reel"),
         _record("name-bar-fights-the-captions", substrate="no-cost-reel")],
        catalogue=_catalogue(_substrate(
            "no-cost-reel", features=("lower_third_overlay",),
            checked=("cost_statement",))),
    )
    view = report["premise"]["stage_metrics"]["stages"]["interpretation"]
    assert view["measured"] == 1
    assert view["not_measured"] == 1
    assert view["total"] == 2
    assert report["premise"]["premise_carried_count"] == 1
    assert report["premise"]["premise_not_carried"] == ["open-with-the-cost"]


def test_a_premise_nobody_looked_for_is_unchecked_rather_than_missing():
    """The defect: nobody measured the feature, so the harness calls it
    absent and reports a missing premise that was never checked."""
    report = runner.run_acceptance(
        [_record("open-with-the-cost", substrate="unmeasured-reel")],
        catalogue=_catalogue(_substrate("unmeasured-reel")),
    )
    row = _row(report, "open-with-the-cost")
    assert row["premise"]["status"] == footage.PREMISE_UNCHECKED
    assert row["premise"]["missing_premises"] == []
    assert [item["feature"]
            for item in row["premise"]["unchecked_premises"]] == [
                "cost_statement"]
    assert report["premise"]["premise_unchecked"] == ["open-with-the-cost"]


def test_a_record_that_names_no_substrate_says_the_premise_is_unchecked():
    """The defect: a record with no substrate is scored as if the
    footage had been checked, and nobody is told."""
    report = runner.run_acceptance(
        [_record("open-with-the-cost", substrate="")],
        catalogue=_catalogue(_substrate("some-reel")),
    )
    row = _row(report, "open-with-the-cost")
    assert row["premise"]["status"] == footage.PREMISE_UNCHECKED
    assert report["premise"]["premise_unchecked"] == ["open-with-the-cost"]


def test_the_run_picks_the_substrate_whose_profile_matches_the_brief():
    """The defect: with two substrates available the run takes whichever
    reel it opened, and the brief is measured against footage it was
    not written for."""
    the_set = briefs.load_brief_set()
    brief = briefs.brief(the_set, "two-voices-at-once")
    catalogue = _catalogue(
        _substrate("other-profile-reel", profile="product_demo",
                   features=("overlapping_speech", "second_camera_angle")),
        _substrate("matching-profile-reel", profile="two_camera_interview",
                   features=("overlapping_speech", "second_camera_angle")),
    )
    selection = footage.select_substrate(brief, catalogue)
    assert selection["selected"] == "matching-profile-reel"
    assert selection["profile_matches"] == ["matching-profile-reel"]


def test_the_pairing_names_the_premises_a_brief_still_needs():
    """The defect: no substrate carries the brief's premise and nobody
    is told what to film, so the brief waits forever."""
    the_set = briefs.load_brief_set()
    brief = briefs.brief(the_set, "the-wrong-caption")
    catalogue = _catalogue(
        _substrate("clean-captions-reel", checked=("caption_mismatch",)))
    selection = footage.select_substrate(brief, catalogue)
    assert selection["selected"] is None
    assert [item["feature"]
            for item in selection["missing_premises"]] == [
                "caption_mismatch"]
    required = [item["feature"] for item in selection["requires"]]
    assert required == ["caption_mismatch"]


def test_the_pairing_is_what_the_footage_lane_works_from():
    """The defect: the report cannot say what a brief needs, so the
    footage lane has nothing to export."""
    the_set = briefs.load_brief_set()
    catalogue = footage.load_catalogue()
    pairing = footage.select_for_set(the_set, catalogue)
    whole_job = pairing["briefs"]["the-whole-job"]
    required = {item["feature"] for item in whole_job["requires"]}
    assert "music_bed" in required
    assert "second_camera_angle" in required
    assert whole_job["declared_profile"] == "interview_with_broll"


def test_a_premise_the_footage_must_NOT_carry_is_satisfied_by_its_absence():
    """The defect: a brief whose correct answer is "that footage does
    not exist" is refused because the footage does not exist."""
    report = runner.run_acceptance(
        [_record("no-extra-footage", substrate="no-bench-reel")],
        catalogue=_catalogue(_substrate(
            "no-bench-reel", checked=("workshop_bench_coverage",))),
    )
    row = _row(report, "no-extra-footage")
    assert row["premise"]["status"] == footage.PREMISE_CARRIED


def test_a_brief_whose_premise_the_footage_carries_is_refused():
    """The defect: the workshop bench WAS filmed, so the brief that asks
    for it to be absent no longer applies to this footage."""
    report = runner.run_acceptance(
        [_record("no-extra-footage", substrate="bench-reel")],
        catalogue=_catalogue(_substrate(
            "bench-reel", features=("workshop_bench_coverage",))),
    )
    row = _row(report, "no-extra-footage")
    assert row["premise"]["status"] == footage.PREMISE_NOT_CARRIED
    assert [item["feature"]
            for item in row["premise"]["missing_premises"]] == [
                "workshop_bench_coverage"]


def test_a_premise_naming_a_feature_outside_the_enumeration_is_refused():
    """The defect: a brief states its premise in words nobody can
    check, so the set cannot be enforced at all."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    raw["briefs"][0]["oracle"]["footage_requirements"] = [
        {"feature": "a_vibe", "presence": "present"}]
    problems = briefs.validate_brief_set(raw)
    assert any("premise outside the enumeration" in problem
               for problem in problems)


def test_a_premise_that_leaks_into_the_public_view_is_refused():
    """The defect: the premise is handed to the agent, and the agent is
    told where to look."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    brief = copy.deepcopy(raw["briefs"][0])
    brief["oracle"]["footage_requirements"] = [
        {"feature": "cost_statement", "presence": "present"}]
    brief["project_state"]["media_conditions"] = ["the cost_statement reel"]
    problems = briefs.leak_problems(briefs.public_view(brief),
                                    brief["oracle"])
    assert any("cost_statement" in problem for problem in problems)


def test_a_substrate_that_carries_and_denies_the_same_feature_is_refused():
    """The defect: a substrate that says a feature is both there and
    not there settles nothing, and reads as a measurement."""
    raw = {"schema": footage.SCHEMA, "substrates": [
        {"id": "contradictory", "profile": "product_demo",
         "features": ["music_bed"], "checked_features": ["music_bed"]}]}
    problems = footage.validate_catalogue(raw)
    assert any("carries a feature it also checked for" in problem
               for problem in problems)


def test_the_shipped_live_records_name_a_substrate_the_catalogue_carries():
    """The defect: the shipped records name no substrate, so the premise
    of every one of them is unchecked and the enforcement never bites."""
    from pathlib import Path

    records_dir = Path("tests/live/reports/editorial-bench-records")
    if not records_dir.is_dir():
        pytest.skip("the live records are not in this checkout")
    catalogue = footage.load_catalogue()
    for path in sorted(records_dir.glob("*.json")):
        record = record_module.read(path)
        substrate, tried = footage.resolve_substrate(catalogue, record)
        assert substrate is not None, (
            f"{path.name} names no substrate the catalogue carries: "
            f"{tried}")


def test_the_shipped_catalogue_carries_the_reel_the_live_run_measured():
    """The defect: a catalogue whose entries were never measured is a
    list of hopes, and it would refuse briefs the footage does carry."""
    catalogue = footage.load_catalogue()
    reel = next(entry for entry in footage.substrates(catalogue)
                if entry["id"] == "podcast-reel-13")
    assert reel["features"], "the measured reel carries nothing"
    assert reel["checked_features"], "the measured reel was checked for "\
                                      "nothing"
    assert reel["measured"]["evidence"]
