"""PLAN-003, measured on the eight-brief benchmark.

The measurement the punch list names: on the eight-brief benchmark, the
target roles grounded against the footage, the destructive plans refused
for a target that could not be established, and the planning score NOT
dropping.

These tests run the measurement over the benchmark's own records, so the
numbers in the report cannot drift from the records they come from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.editorial_acceptance import grounding_stage

RECORDS = Path(__file__).resolve().parents[2] / "live" / "reports" / (
    "editorial-bench-records")


def _records() -> list[dict]:
    if not RECORDS.is_dir():
        pytest.skip("the benchmark records are not in this checkout")
    return grounding_stage._read_records(RECORDS)


def _the_reel_is_recorded() -> bool:
    """Whether this machine holds the reel the benchmark ran against."""
    from library.tools import timeline_shadow

    try:
        store = timeline_shadow.ShadowStore()
        snapshots = store.current_snapshots("Ren Scratch - breaktest")
    except Exception:  # noqa: BLE001 - a missing store is an answer
        return False
    return any(generation.timeline_name ==
               "Reel 13 - the-accounting-firm-ai-called-healthcare"
               for generation, _snapshot in snapshots)


def test_the_measurement_reads_the_benchmarks_own_records():
    records = _records()

    assert len(records) == 8
    assert {str(record.get("brief_id")) for record in records} == {
        "keep-it-but-tighten-it", "logo-not-in-the-project",
        "name-bar-fights-the-captions", "no-extra-footage",
        "open-with-the-cost", "the-whole-job", "the-wrong-caption",
        "two-voices-at-once"}


def test_every_target_role_the_plan_names_is_grounded_or_reported():
    # Defect: a role the plan named was simply absent from the report,
    # so the number of targets read as the number that were easy.
    payload = grounding_stage.measure(_records())

    assert payload["brief_count"] == 8
    assert payload["role_instances"] == 11
    assert (payload["grounded_role_instances"]
            + payload["observed_role_instances"]
            + payload["claimed_role_instances"]
            + payload["unresolved_role_instances"]) == 11
    assert payload["unresolved_role_instances"] == 7


def test_a_destructive_plan_is_refused_for_a_target_that_is_not_established():
    # Defect: a plan named a target the footage does not carry, and the
    # destructive operation on it was not refused before staging.
    payload = grounding_stage.measure(_records())

    assert payload["destructive_operations"] == 8
    assert payload["destructive_operations_refused"] == 8

    rows = {row["brief_id"]: row for row in payload["briefs"]}
    whole_job = rows["the-whole-job"]
    refused = {refusal["op"] for refusal in whole_job["refusals"]}
    assert {"reel.opening", "clip.trim", "multicam.select_angle",
            "broll.insert", "caption_fix"} <= refused
    # The one operation that destroys nothing is not refused: the stage
    # forbids destructive changes, not every change.
    assert "overlay.position" not in refused


def test_the_gate_refuses_a_plan_whose_destructive_target_is_not_established():
    # Defect: the stage measured the plan and nothing acted on the
    # measurement, so a plan with an unestablished target could still
    # be staged.
    records = {str(record.get("brief_id")): record for record in _records()}

    refused = grounding_stage.gate(records["the-whole-job"])
    assert not refused["may_stage"]
    assert refused["destructive_operations_refused"] == 5

    # A brief whose only operation destroys nothing may stage.
    allowed = grounding_stage.gate(records["name-bar-fights-the-captions"])
    assert allowed["may_stage"]
    assert allowed["destructive_operations_refused"] == 0


def test_the_planning_number_does_not_drop():
    # The coverage gate: a grounding refusal happens AFTER the planning
    # stage and rewrites nothing the planning stage reads, so a plan
    # whose targets are refused keeps its planning credit.
    payload = grounding_stage.measure(_records())
    planning = payload["coverage"]["planning"]

    assert planning["passed"] == 4
    assert planning["measured"] == 8
    assert planning["value"] == 0.5
    assert payload["coverage"]["planning_before"] == planning
    assert payload["coverage"]["planning_after"] == planning


@pytest.mark.skipif(not _the_reel_is_recorded(),
                    reason="this machine holds no recorded reel")
def test_a_target_the_recorded_timeline_carries_is_verified():
    # Defect: the run's own claim was read as a fact, and a target the
    # timeline really carries was never confirmed against it.
    payload = grounding_stage.measure(_records())

    rows = {row["brief_id"]: row for row in payload["briefs"]}
    name_bar = rows["name-bar-fights-the-captions"]
    assert name_bar["grounded_role_instances"] == 1
    target = name_bar["targets"][0]
    assert target["state"] == "verified"
    assert target["identity"]["name"] == "b-soft-focus-craig.mov"
    assert target["interval"]["start_frame"] == 0
    assert target["interval"]["end_frame"] == 84


@pytest.mark.skipif(not _the_reel_is_recorded(),
                    reason="this machine holds no recorded reel")
def test_a_target_the_timeline_carries_several_candidates_is_observed():
    # Defect: an ambiguous target was read as resolved.
    payload = grounding_stage.measure(_records())

    rows = {row["brief_id"]: row for row in payload["briefs"]}
    whole_job = rows["the-whole-job"]
    opening = next(row for row in whole_job["targets"]
                   if row["role"] == "opening_section")
    assert opening["state"] == "observed"
    assert opening["candidate_count"] > 1
    surfaces = [attempt["surface"] for attempt in opening["attempts"]]
    assert surfaces[0] == "timeline"


def test_the_claim_reader_names_what_the_run_actually_said():
    # Defect: the reader took the run's prose for a fact, or missed the
    # identity the run did name.
    claim = grounding_stage.selectors_of(
        "the lower third 'b-soft-focus-craig.mov' on V6, unique_id "
        "c71c3d10-cc65-4764-b567-82b1f6222b66, frames 0-84")

    assert claim["unique_id"] == "c71c3d10-cc65-4764-b567-82b1f6222b66"
    assert "b-soft-focus-craig.mov" in claim["names"]
    assert claim["record_spans"] == [(0, 84)]
    assert claim["row"] == "V6"


def test_a_claim_that_names_no_media_file_names_no_clip():
    # Defect: an apostrophe in ordinary English was read as a quote, and
    # the reader invented a clip out of it.
    claim = grounding_stage.selectors_of(
        "the reel's first picture (frames 0-129)")

    assert "names" not in claim
    assert claim["record_spans"] == [(0, 129)]


def test_the_report_is_written_as_json():
    payload = grounding_stage.measure(_records())

    assert json.loads(json.dumps(payload, default=str))["schema"] == (
        grounding_stage.SCHEMA)
