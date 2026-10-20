"""The recurring demonstration: one complete professional edit.

The defect: a demonstration that is met because Ren ran every command.
The captain's own words are that the milestone is not achieved merely
because the commands ran - it is achieved when the finished output
fulfils the editorial intent. So the milestone here needs the delivered
file, an independent judge and every piece of evidence, and a run that
planned all ten operations but delivered nothing does not get it.

The second defect: the demonstration is RECURRING, and a regression
between two runs is a number rather than a memory of how the last one
felt.
"""

from __future__ import annotations

from library.tools.editorial_acceptance import demo
from library.tools.editorial_acceptance import record as record_module


def _record(**overrides) -> dict:
    record = record_module.new_record(
        task_id="the-whole-job",
        brief_id="the-whole-job",
        instruction="Do the whole job on the launch reel.",
        interpretation={
            "restated_outcome": "the whole launch reel, start to delivery",
            "selected_operations": ["reel.opening", "clip.trim",
                                    "multicam.select_angle", "broll.insert",
                                    "caption_fix", "overlay.position",
                                    "audio.gain"],
            "resolved_targets": [],
        },
        retrieval={
            "moments": [{"moment_id": "hook-moment", "source_id": "cam-a",
                         "start_seconds": 12.0, "end_seconds": 18.0,
                         "evidence_ids": ["ev-1"]}],
            "abstained": False,
            "unavailable": [],
        },
        planning={
            "operations": [
                {"op": "reel.opening", "target_role": "opening_section"},
                {"op": "clip.trim", "target_role": "repeated_passages"},
                {"op": "multicam.select_angle", "target_role": "weak_angle_turn"},
                {"op": "broll.insert", "target_role": "broll_cover_range"},
                {"op": "caption_fix", "target_role": "wrong_caption_card"},
                {"op": "overlay.position", "target_role": "name_lower_third"},
                {"op": "audio.gain", "target_role": "music_bed"},
            ],
            "requirements_covered": [],
            "unauthorised_operations": [],
        },
        execution={
            "receipt": {"status": "verified"},
            "checks": {"target_readback": True, "approved_edits_preserved": True},
            "collateral_changes": [],
            "attempts": 1,
            "route": "resolve_axi",
            "recovery": {"state": "none"},
        },
        delivery={
            "path": "/tmp/out.mp4",
            "sha256": "e" * 64,
            "duration_seconds": 55.0,
            "evidence": [{"kind": "frame", "artifact": "/tmp/f.png",
                          "observation": "the first frame"}],
            "technical_verification": {"audio_streams": True, "lufs": True},
            "technical_not_checked": [],
        },
        assessment={
            "judge": "an independent editor",
            "editorial": {"status": "pass", "score": 1.0, "passed": 10,
                          "failed": 0, "not_measured": 0},
            "technical": {"status": "pass", "checks": {"audio_streams": True}},
        },
        run={"wall_seconds": 412.5, "instruction_to_verified_seconds": 388.0},
        resource={"peak_rss_bytes": 187_000_000},
    )
    for key, value in overrides.items():
        record[key] = value
    return record


def _brief() -> dict:
    return {"id": "the-whole-job",
            "instruction": "Do the whole job on the launch reel."}


def test_the_demonstration_carries_ten_obligations_and_ten_evidence():
    """The defect: the assignment is described in prose and the list of
    what must be shown is remembered differently each time."""
    assert len(demo.OBLIGATIONS) == 10
    assert len(demo.EVIDENCE) == 10
    assert len({row["id"] for row in demo.OBLIGATIONS}) == 10
    assert len({row["id"] for row in demo.EVIDENCE}) == 10


def test_a_complete_run_establishes_everything():
    report = demo.score_demo(_record(), _brief())
    assert report["obligations_established"] == 10
    assert report["evidence_established"] == 10
    assert report["milestone"] is True


def test_a_run_that_planned_everything_and_delivered_nothing_does_not_pass():
    """The defect: the milestone is met because the commands ran, which
    is exactly what the captain's assignment says it is not."""
    record = _record()
    record["delivery"] = {}
    record["assessment"] = {}
    report = demo.score_demo(record, _brief())
    assert report["obligations_established"] < 10
    assert report["milestone"] is False


def test_a_run_the_judge_refused_does_not_reach_the_milestone():
    """The defect: the plan was complete and the delivered file was not,
    and the demonstration calls it done."""
    record = _record()
    record["assessment"] = {
        "judge": "an independent editor",
        "editorial": {"status": "fail", "score": 0.4, "passed": 4,
                      "failed": 6, "not_measured": 0},
        "technical": {"status": "pass", "checks": {"audio_streams": True}},
    }
    report = demo.score_demo(record, _brief())
    assert report["milestone"] is False
    exported = next(row for row in report["evidence"]
                    if row["id"] == "export-carries-changes")
    assert exported["established"] is False


def test_a_run_that_changed_unrelated_work_fails_the_preservation_obligation():
    """The captain's standing rule: an unintended change to approved work
    is never a successful outcome."""
    record = _record()
    record["execution"]["collateral_changes"] = [{"item": "a-lower-third"}]
    report = demo.score_demo(record, _brief())
    preservation = next(row for row in report["obligations"]
                        if row["id"] == "preservation")
    assert preservation["established"] is False
    assert report["milestone"] is False


def test_an_unresolved_recovery_fails_the_evidence():
    """The defect: a run that left a recovery pending calls itself
    finished."""
    record = _record()
    record["execution"]["recovery"] = {"state": "pending"}
    report = demo.score_demo(record, _brief())
    pending = next(row for row in report["evidence"]
                   if row["id"] == "no-open-transaction")
    assert pending["established"] is False
    assert report["milestone"] is False


def test_a_moment_with_no_evidence_behind_it_fails_the_evidence():
    """The defect: a source moment nobody can support, counted as
    evidence."""
    record = _record()
    record["retrieval"]["moments"][0]["evidence_ids"] = []
    report = demo.score_demo(record, _brief())
    backed = next(row for row in report["evidence"]
                  if row["id"] == "evidence-backed")
    assert backed["established"] is False


def test_a_run_that_measured_nothing_says_so():
    """The defect: a demonstration with no latency and no resource figure
    reads as a demonstration that was cheap."""
    record = _record()
    record.pop("run")
    record.pop("resource")
    report = demo.score_demo(record, _brief())
    measured = next(row for row in report["evidence"]
                    if row["id"] == "latency-and-resource")
    assert measured["established"] is False
    assert report["latency"]["wall_seconds"] is None


def test_two_runs_are_compared_item_by_item():
    """The defect: a recurring demonstration with no comparison, so a
    regression is a feeling."""
    before = demo.score_demo(_record(), _brief())
    worse = _record()
    worse["execution"]["collateral_changes"] = [{"item": "a-lower-third"}]
    after = demo.score_demo(worse, _brief())
    comparison = demo.compare_runs(before, after)
    assert comparison["regressed"] is True
    assert comparison["milestone_was"] is True
    assert comparison["milestone_is"] is False
    regressed = {row["id"] for row in
                 comparison["obligations"]["regressions"]}
    assert "preservation" in regressed


def test_two_identical_runs_show_no_regression():
    """The defect: the comparison reports a change every time, so it is
    read as noise."""
    first = demo.score_demo(_record(), _brief())
    second = demo.score_demo(_record(), _brief())
    comparison = demo.compare_runs(first, second)
    assert comparison["regressed"] is False
    assert comparison["obligations"]["changed"] == []
