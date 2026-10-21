"""The acceptance run: briefs in, one report out.

The defect: a report that cannot be read. A report with no brief set
behind it, no stage denominators, no named bottleneck, and a combined
score that hides which of the five stages broke is a number with nothing
behind it, and the next engineering change is chosen by taste.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.editorial_acceptance import briefs, runner
from library.tools.editorial_acceptance import record as record_module


def _assessment() -> dict:
    return {
        "judge": "an independent editor",
        "editorial": {"status": "pass", "score": 1.0, "passed": 2,
                      "failed": 0, "not_measured": 0,
                      "criteria": [{"id": "hook-first", "status": "pass",
                                    "rationale": "read off the file",
                                    "evidence": []}]},
        "technical": {"status": "pass", "checks": {"audio_streams": True}},
    }


def _criteria(brief_id: str = "open-with-the-cost") -> list[str]:
    """Every acceptance criterion of the brief this fixture runs.

    A plan that declares none of them covered is NOT a valid plan, and
    the planning stage says so - so a fixture that means to test a
    LATER stage has to hand it a valid plan.
    """
    the_set = briefs.load_brief_set()
    try:
        brief = briefs.brief(the_set, brief_id)
    except KeyError:
        return []
    return [str(criterion["id"])
            for criterion in brief["acceptance_criteria"]]


def _record(brief_id: str = "open-with-the-cost", **overrides) -> dict:
    record = record_module.new_record(
        task_id=brief_id,
        brief_id=brief_id,
        instruction="Open on the moment she says what it costs.",
        interpretation={
            "restated_outcome": "open on the moment she names the cost",
            "selected_operations": ["reel.opening"],
            "resolved_targets": [{"role": "opening_section",
                                  "resolved": True}],
        },
        retrieval={
            "moments": [{"moment_id": "cost-statement", "source_id": "cam-a",
                         "start_seconds": 12.0, "end_seconds": 18.0,
                         "evidence_ids": ["ev-1"]}],
            "abstained": False,
            "unavailable": [],
        },
        planning={
            "operations": [{"op": "reel.opening",
                            "target_role": "opening_section",
                            "reason": "the hook"}],
            "requirements_covered": _criteria(brief_id),
        },
        execution={
            "receipt": {"status": "verified"},
            "checks": {"target_readback": True},
            "collateral_changes": [],
            "attempts": 1,
            "route": "resolve_axi",
        },
        delivery={
            "path": "/tmp/out.mp4",
            "sha256": "c" * 64,
            "duration_seconds": 30.0,
            "evidence": [{"kind": "frame", "artifact": "/tmp/f.png",
                          "observation": "the first frame"}],
            "technical_verification": {"audio_streams": True},
        },
        assessment=_assessment(),
    )
    for key, value in overrides.items():
        record[key] = value
    return record


def _judge():
    def _judge_fn(brief, render):
        _judge_fn.seen.append({"brief": brief, "render": render})
        return {
            "judge": "an independent editor",
            "criteria": [
                {"id": criterion["id"], "status": "pass",
                 "rationale": "read off the file",
                 "evidence": [{"artifact": "/tmp/f.png",
                               "observation": "the first frame"}]}
                for criterion in brief["acceptance_criteria"]],
        }
    _judge_fn.seen = []
    return _judge_fn


def test_the_report_measures_five_stages_on_their_own_denominators():
    """The defect: one number over five stages, which answers no
    question anybody asked."""
    report = runner.run_acceptance([_record()])
    stages = report["stage_metrics"]["stages"]
    assert set(stages) == {"interpretation", "retrieval", "planning",
                           "execution", "final_quality"}
    for stage in stages.values():
        assert stage["measured"] == 1
        assert stage["not_measured"] == 0
    assert "value" not in report["stage_metrics"]
    assert report["stage_metrics"]["stages_are_not_combined"] is True


def test_the_report_names_the_brief_set_it_measured():
    """The defect: a report with no brief set behind it, so a reader
    cannot tell which requests produced these numbers."""
    report = runner.run_acceptance([_record()])
    assert report["brief_set"]["sha256"]
    assert report["brief_set"]["brief_count"] == 8
    assert report["brief_set"]["coverage"]["edge_cases_missing"] == []


def test_a_brief_the_set_does_not_carry_is_reported_as_not_run():
    """The defect: a record for a brief nobody knows is scored as if it
    were one of ours."""
    report = runner.run_acceptance([_record(brief_id="no-such-brief")])
    assert report["scored"] == 0
    assert report["not_run"] == 1
    assert report["results"][0]["status"] == "not_run"
    assert "no-such-brief" in report["results"][0]["reason"]


def test_a_run_that_changed_unrelated_work_is_reported_as_unsafe():
    """The captain's standing rule: an unintended change to approved work
    is never a successful outcome, and the report has to say which run
    did it."""
    record = _record()
    record["execution"]["collateral_changes"] = [{"item": "a-lower-third"}]
    report = runner.run_acceptance([record])
    failure = report["results"][0]["failure"]
    assert failure["cause"] == "execution.collateral_change"
    assert failure["severity"] == "unsafe"
    assert report["failure_causes"]["unsafe_failures"] == 1
    assert report["failure_causes"]["bottleneck"] == "execution"


def test_the_report_publishes_the_reading_and_grounding_split():
    """The defect: the interpretation stage measures the reading AND the
    grounding together, so a report that says "interpretation 5/8"
    cannot say which half of it was earned."""
    report = runner.run_acceptance([_record()])
    assert report["reading"]["measured"] == 1
    assert report["reading"]["value"] == 1.0
    assert report["grounding"]["role_instances"] == 1
    assert report["grounding"]["unit"] == "role instances"
    assert "role instance" in report["grounding"]["note"]
    assert report["stage_metrics"]["stages"]["interpretation"]["value"] == 1.0


def test_the_split_is_published_alongside_the_headline_and_moves_nothing():
    """The defect: a supplementary view REPLACES the headline, and the
    engine's score becomes a number the harness chose."""
    report = runner.run_acceptance([_record()])
    assert set(report["stage_metrics"]["stages"]) == {
        "interpretation", "retrieval", "planning", "execution",
        "final_quality"}
    assert "reading" not in report["stage_metrics"]["stages"]
    assert "grounding" not in report["stage_metrics"]["stages"]


def test_the_report_publishes_the_criteria_coverage_beside_the_headline():
    """The defect: the headline's planning number says how many plans
    were valid answers and nothing says how much of the brief each plan
    carried - so a plan that covers one criterion and is silent about the
    preservation of the rest reads as a full answer."""
    record = _record()
    record["planning"]["requirements_covered"] = ["hook-first"]
    report = runner.run_acceptance([record])
    coverage = report["criteria_coverage"]
    assert coverage["briefs_expected_to_edit"] == 1
    assert coverage["acceptance_criteria"] == 3
    assert coverage["declared_covered"] == 1
    assert coverage["not_declared_covered"] == 2
    assert coverage["value"] == pytest.approx(1 / 3)
    assert report["stage_metrics"]["stages"]["planning"]["passed"] == 0


def test_the_report_names_the_bottleneck_the_next_change_targets():
    """The defect: counts with no target, so the next engineering change
    is picked by taste."""
    record = _record()
    record["interpretation"]["selected_operations"] = ["clip.trim"]
    report = runner.run_acceptance([record])
    assert report["failure_causes"]["bottleneck"] == "interpretation"
    assert report["failure_causes"]["dominant_cause"]
    assert report["failure_causes"]["dominant_cause_summary"]


def test_the_judge_is_never_handed_the_answer_key():
    """The defect: the judge is told the operations the request implies,
    so the assessment measures the plan rather than the film."""
    judge = _judge()
    report = runner.run_acceptance([_record()], judge=judge)
    seen = json.dumps(judge.seen[0])
    assert "expected_operations" not in seen
    assert "opening_section" not in seen
    assert report["results"][0]["assessment"]["independence"]


def test_a_record_that_breaks_its_contract_is_refused():
    """The defect: a half-written record is read as a run that did
    nothing, and the report calls it a failure of the run."""
    broken = _record()
    del broken["execution"]
    problems = record_module.validate(broken)
    assert any("execution" in problem for problem in problems)
    with pytest.raises(record_module.RecordError):
        record_module.write(Path("/tmp/never-written.json"), broken)


def test_records_are_read_from_a_file_or_a_directory(tmp_path):
    """The defect: the live lane's records are in a directory and the
    harness only reads a file, so the run is never measured."""
    record_module.write(tmp_path / "one.json", _record("open-with-the-cost"))
    record_module.write(tmp_path / "two.json", _record("the-wrong-caption"))
    from_file = runner.read_records(tmp_path)
    assert len(from_file) == 2
    assert {entry["brief_id"] for entry in from_file} == {
        "open-with-the-cost", "the-wrong-caption"}
    listed = tmp_path / "list.json"
    listed.write_text(json.dumps(from_file), encoding="utf-8")
    assert len(runner.read_records(listed)) == 2


def test_a_record_with_a_file_and_no_measurement_is_measured_by_the_runner(
        tmp_path, monkeypatch):
    """The defect: the run left a delivered file and no verdict, and the
    harness scored the task without ever looking at the file."""
    from library.tools.editorial_acceptance import delivery

    measured = {"sha256": "d" * 64, "path": "/tmp/out.mp4",
                "duration_seconds": 12.0, "evidence": [{"kind": "frame"}],
                "technical_verification": {"audio_streams": True}}
    calls: list[str] = []

    def _fake_build(path, **kwargs):
        calls.append(str(path))
        return measured

    monkeypatch.setattr(delivery, "build_render_evidence", _fake_build)
    record = _record()
    record["delivery"] = {"path": "/tmp/out.mp4"}
    record.pop("assessment")
    report = runner.run_acceptance([record])
    assert calls == ["/tmp/out.mp4"]
    assert report["results"][0]["delivery"]["sha256"] == "d" * 64


def test_the_records_own_measurement_is_not_thrown_away(tmp_path,
                                                        monkeypatch):
    """The defect: the harness re-measured the file and REPLACED the
    record's own measurement with the fresh one - so a delivery graded
    against the run's declarations (a declared silent ending) came back
    undeclared, and the report contradicted the record it read."""
    from library.tools.editorial_acceptance import delivery

    monkeypatch.setattr(
        delivery, "build_render_evidence",
        lambda path, **kwargs: {
            "sha256": "d" * 64, "path": path, "duration_seconds": 12.0,
            "evidence": [{"kind": "frame", "artifact": "/tmp/f.png",
                          "observation": "the first frame"}],
            "technical_verification": {"silence_under_picture": False},
        })
    record = _record()
    record["delivery"] = {
        "path": "/tmp/out.mp4",
        "sha256": "c" * 64,
        "duration_seconds": 30.0,
        "technical_verification": {"silence_under_picture": True},
    }
    report = runner.run_acceptance([record])
    row = report["results"][0]["delivery"]
    assert row["technical_verification"]["silence_under_picture"] is True
    assert row["sha256"] == "c" * 64


def test_the_report_records_which_footage_each_brief_ran_against():
    """The defect: a set of briefs against representative real footage,
    and the report cannot say which footage any of them ran on."""
    report = runner.run_acceptance([_record()])
    footage = report["results"][0]["footage"]
    assert footage["profile"] == "two_camera_interview"
    assert footage["existing_adjustments"]
    assert footage["media_conditions"]
    # A brief that hides no edge case carries none, so the row's edge case
    # is either one of the set's named cases or nothing - and the report
    # must not invent one for it.
    assert report["results"][0]["edge_case"] in (None, *briefs.EDGE_CASES)


def test_the_brief_set_the_runner_uses_refuses_a_leak():
    """The defect: the runner builds the agent's view by filtering the
    brief, and a field added later leaks by being forgotten."""
    the_set = briefs.load_brief_set()
    for brief in the_set["briefs"]:
        view = briefs.public_view(brief)
        briefs.assert_no_leak(view, briefs.oracle(brief))
        assert set(view) == set(briefs.PUBLIC_KEYS)


def test_the_report_carries_the_demonstration_before_it_has_been_run():
    """The defect: the recurring demonstration's ten obligations and ten
    pieces of evidence are in someone's head, so each run discovers them
    again."""
    report = runner.run_acceptance([])
    demonstration = report["demonstration"]
    assert demonstration["run_state"] == "not_run"
    assert demonstration["obligations_total"] == 10
    assert demonstration["evidence_total"] == 10
    assert demonstration["milestone"] is False


def test_the_demonstration_is_scored_when_its_brief_runs():
    """The defect: the demonstration brief is measured like any other and
    its own ten are never looked at."""
    report = runner.run_acceptance([_record("the-whole-job")])
    assert report["demonstration"]["brief_id"] == "the-whole-job"
    assert report["demonstration"]["obligations_total"] == 10
    assert report["brief_set"]["demonstration_brief"] == "the-whole-job"


def test_the_report_says_what_the_judge_was_handed():
    """The defect: the report says a file was delivered and nothing
    about the evidence the judge was given, so a reader cannot tell a
    three-still packet from a full one - and cannot tell that the four
    criteria which were unmeasurable are measurable now."""
    record = _record()
    record["delivery"] = dict(record["delivery"],
                              transcript={"measured": True, "word_count": 249,
                                          "aligner": "mfa"},
                              shots={"measured": True, "count": 1,
                                     "soft_count": 5},
                              frame_count=45)
    report = runner.run_acceptance([record])
    delivery = report["results"][0]["delivery"]
    assert delivery["frame_count"] == 45
    assert delivery["transcript"]["word_count"] == 249
    assert delivery["shots"]["soft_count"] == 5


def test_the_aligner_is_the_harness_s_to_ask_for(monkeypatch):
    """The defect: the aligner costs minutes per delivered file and
    every run pays it, with nothing to say that the run wanted the
    cheaper timing."""
    from library.tools.editorial_acceptance import delivery

    asked: list[bool] = []

    def _fake_build(path, **kwargs):
        asked.append(bool(kwargs.get("align_transcript", True)))
        return {"sha256": "d" * 64, "path": str(path),
                "duration_seconds": 12.0, "evidence": [{"kind": "frame"}],
                "technical_verification": {"audio_streams": True}}

    monkeypatch.setattr(delivery, "build_render_evidence", _fake_build)
    record = _record()
    record["delivery"] = {"path": "/tmp/out.mp4"}
    record.pop("assessment")
    runner.run_acceptance([record], align_transcript=False)
    assert asked == [False]
